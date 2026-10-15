from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import logging
import multiprocessing
import os
from pathlib import Path
import random
import re
import signal
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from .browser import (
    BrowserResult,
    fetch_article_with_browser,
    minimum_text_length_for_url,
)
from .config import AppConfig, FeedConfig
from .extract import (
    ArticleContent,
    extract_article,
    is_cloudflare_challenge,
    looks_like_paywall_excerpt,
)
from .feed import FeedItem, parse_feed
from .fetch import FetchError, Fetcher
from .issue import CurrentIssue, resolve_current_issue
from .store import ArticleStore, StoredArticle
from .util import canonical_url, cutoff_datetime, now_iso, parse_datetime


LOGGER = logging.getLogger(__name__)
WORLD_IN_BRIEF_DATE_RE = re.compile(r"/the-world-in-brief/(\d{4})/(\d{2})/(\d{2})/")
WORLD_IN_BRIEF_UPDATED_RE = re.compile(
    r"\bupdated\s+(?:(just now|moments ago)|(\d+)\s*"
    r"(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s+ago)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RefreshSummary:
    status: str
    feeds_checked: int
    feed_items_seen: int
    articles_fetched: int
    articles_failed: int
    skipped_reason: str = ""
    stop_reason: str = ""


@dataclass(frozen=True)
class ArticleFetchResult:
    content: ArticleContent | None = None
    source: str = ""
    status: str = ""
    message: str = ""
    http_status: int | None = None
    retry_after_seconds: int | None = None
    final_url: str = ""
    stop_refresh: bool = False
    stop_reason: str = ""


@dataclass(frozen=True)
class ArticleBatchSummary:
    fetched: int
    failed: int
    stop_reason: str = ""


def refresh_if_stale(
    config: AppConfig,
    *,
    force: bool = False,
    ignore_refresh_interval: bool = False,
) -> RefreshSummary:
    if not config.refresh_enabled:
        return RefreshSummary(
            status="skipped",
            feeds_checked=0,
            feed_items_seen=0,
            articles_fetched=0,
            articles_failed=0,
            skipped_reason="refresh_disabled",
        )
    with refresh_lock(config.database_path) as lock_acquired:
        if not lock_acquired:
            return RefreshSummary(
                status="skipped",
                feeds_checked=0,
                feed_items_seen=0,
                articles_fetched=0,
                articles_failed=0,
                skipped_reason="refresh_already_running",
            )
        with ArticleStore(config.database_path) as store:
            if (
                not force
                and not ignore_refresh_interval
                and not _is_stale(store, config.refresh_interval_seconds)
            ):
                return RefreshSummary(
                    status="skipped",
                    feeds_checked=0,
                    feed_items_seen=0,
                    articles_fetched=0,
                    articles_failed=0,
                    skipped_reason="refresh_interval_not_elapsed",
                )
            return refresh(store, config, force=force)


def refresh(store: ArticleStore, config: AppConfig, *, force: bool = False) -> RefreshSummary:
    fetcher = Fetcher(user_agent=config.user_agent, timeout_seconds=config.timeout_seconds)
    feeds_checked = 0
    feed_items_seen = 0
    articles_fetched = 0
    articles_failed = 0
    stop_reason = ""
    published_after = cutoff_datetime(config.article_lookback_days)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    store.set_state("last_refresh_stop_reason", "")

    if config.current_issue_filter_enabled:
        _refresh_current_issue_if_stale(store, fetcher, config, force=force)

    for feed_config in config.feeds:
        feeds_checked += 1
        try:
            response = fetcher.fetch_text(feed_config.url)
        except FetchError as exc:
            store.set_state("last_feed_error", str(exc))
            continue
        try:
            seen = save_feed_xml(store, feed_config, response.text, published_after)
        except Exception as exc:  # noqa: BLE001 - feed parser errors should not kill refresh.
            store.set_state(
                "last_feed_error",
                f"Could not parse feed {feed_config.url}: {exc}",
            )
            continue
        feed_items_seen += seen

    fetch_budget = max(0, config.max_articles_per_refresh)
    used_fetch_budget = 0
    if fetch_budget > 0:
        world_result = _refresh_world_in_brief_if_stale(
            store,
            config,
            run_id=run_id,
            force=force,
        )
        if world_result is not None:
            used_fetch_budget += 1
            if world_result.content:
                articles_fetched += 1
            else:
                articles_failed += 1
                if world_result.stop_refresh:
                    stop_reason = world_result.stop_reason
                    store.set_state("last_refresh_stop_reason", world_result.stop_reason)

    remaining_budget = 0 if stop_reason else max(0, fetch_budget - used_fetch_budget)
    candidates = select_article_candidates(
        store, config, remaining_budget, published_after=published_after, force=force
    )

    batch = fetch_article_batch(
        store,
        config,
        candidates,
        fetcher=fetcher,
        force=force,
        run_id=run_id,
        run_kind="latest_refresh",
        stop_state_key="last_refresh_stop_reason",
    )
    articles_fetched += batch.fetched
    articles_failed += batch.failed
    stop_reason = stop_reason or batch.stop_reason

    store.set_state("last_refresh_at", now_iso())
    return RefreshSummary(
        status="ok",
        feeds_checked=feeds_checked,
        feed_items_seen=feed_items_seen,
        articles_fetched=articles_fetched,
        articles_failed=articles_failed,
        stop_reason=stop_reason,
    )


def save_feed_xml(
    store: ArticleStore,
    feed_config: FeedConfig,
    xml_text: str,
    published_after: datetime | None,
) -> int:
    """Parse one feed's XML and upsert its recent items; the part of refresh() shared with push ingest."""
    items = parse_feed(xml_text, feed_config.name)
    if feed_config.limit is not None:
        items = items[: feed_config.limit]
    items = _recent_feed_items(items, published_after)
    for item in items:
        store.upsert_feed_item(_normal_feed_item(item))
    return len(items)


def select_article_candidates(
    store: ArticleStore,
    config: AppConfig,
    limit: int,
    *,
    published_after: datetime | None,
    force: bool = False,
) -> list[StoredArticle]:
    """Articles refresh() would fetch next: explicitly requested first, then newest pending."""
    requested_candidates = store.requested_articles(
        limit=limit,
        retry_failed_after_seconds=config.retry_failed_after_seconds,
        exclude_url_patterns=config.exclude_url_patterns,
        force=force,
    )
    requested_urls = {article.canonical_url for article in requested_candidates}
    normal_candidates = store.pending_articles(
        limit=limit,
        retry_failed_after_seconds=config.retry_failed_after_seconds,
        exclude_url_patterns=config.exclude_url_patterns,
        published_after=published_after,
        force=force,
    )
    return [
        *requested_candidates,
        *(a for a in normal_candidates if a.canonical_url not in requested_urls),
    ][:limit]


MAX_WANTED_ARTICLES = 10


def wanted_article_urls(
    store: ArticleStore, config: AppConfig, limit: int | None = None
) -> list[str]:
    """Article URLs a pushing Mac should load next (World in Brief has its own push)."""
    cap = config.max_articles_per_refresh if limit is None else limit
    cap = max(0, min(cap, MAX_WANTED_ARTICLES))
    if cap == 0:
        return []
    candidates = select_article_candidates(
        store,
        config,
        cap + 50,  # headroom: World in Brief pages are filtered out below
        published_after=cutoff_datetime(config.article_lookback_days),
    )
    urls = [a.url for a in candidates if not WORLD_IN_BRIEF_DATE_RE.search(urlparse(a.url).path)]
    return urls[:cap]


@dataclass(frozen=True)
class FeedPushResult:
    url: str
    status: str
    items: int = 0
    message: str = ""


def ingest_feed_xml(
    config: AppConfig,
    *,
    feeds: list[tuple[str, str]],
    source_host: str = "",
    max_articles: int | None = None,
) -> tuple[list[FeedPushResult], list[str]]:
    """Save RSS XML that a signed-in Mac loaded, then say which articles still need full text."""
    by_url = {feed.url: feed for feed in config.feeds}
    published_after = cutoff_datetime(config.article_lookback_days)
    results: list[FeedPushResult] = []
    with ArticleStore(config.database_path) as store:
        for url, xml in feeds:
            feed_config = by_url.get(url)
            if feed_config is None:
                results.append(FeedPushResult(url, "unknown_feed", message="url is not a configured feed"))
            elif is_cloudflare_challenge(xml):
                results.append(
                    FeedPushResult(url, "cloudflare_challenge", message="xml is a Cloudflare challenge page")
                )
            else:
                try:
                    count = save_feed_xml(store, feed_config, xml, published_after)
                except Exception as exc:  # noqa: BLE001
                    results.append(FeedPushResult(url, "bad_feed", message=f"could not parse feed: {exc}"[:200]))
                else:
                    results.append(FeedPushResult(url, "ok", items=count))
        if any(r.status == "ok" for r in results):
            store.set_state(
                "feeds_last_push",
                json.dumps({"at": now_iso(), "source_host": source_host[:80]}, sort_keys=True),
            )
        wanted = wanted_article_urls(store, config, max_articles)
    return results, wanted


@dataclass(frozen=True)
class ArticlePushResult:
    ok: bool
    status: str
    message: str
    title: str = ""
    url: str = ""
    chars: int = 0


def ingest_article_html(
    config: AppConfig, *, url: str, html: str, source_host: str = ""
) -> ArticlePushResult:
    """Save an article page that a signed-in browser elsewhere loaded (like a browser fetch success)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.economist.com", "economist.com"}:
        return ArticlePushResult(False, "bad_url", "url must be an https economist.com page")
    with ArticleStore(config.database_path) as store:
        article = store.get_article(url)

        def fail(status: str, message: str) -> ArticlePushResult:
            if article is not None:
                store.mark_fetch_error(article, status=status, error=message)
            return ArticlePushResult(False, status, message)

        if is_cloudflare_challenge(html):
            return fail("cloudflare_challenge", "The article response was a Cloudflare challenge page.")
        content = extract_article(html)
        if (
        content is None
        or len(content.text) < minimum_text_length_for_url(url)
        or looks_like_paywall_excerpt(content.text)
    ):
            return fail(
                "excerpt_or_login_required",
                "The article page did not expose full subscriber text.",
            )
        if article is None:
            article = store.upsert_feed_item(
                _normal_feed_item(
                    FeedItem(
                        title=content.title or url,
                        link=url,
                        guid=url,
                        summary=_text_summary(content.text),
                        published=None,
                        source="The Economist",
                    )
                )
            )
        store.save_article_content(
            article,
            content_html=content.content_html,
            content_text=content.text,
            content_source="economist_mac_push",
        )
        store.set_state(
            "articles_last_push",
            json.dumps({"at": now_iso(), "url": url, "source_host": source_host[:80]}, sort_keys=True),
        )
    return ArticlePushResult(True, "ok", "saved", title=content.title, url=url, chars=len(content.text))


def fetch_article_batch(
    store: ArticleStore,
    config: AppConfig,
    candidates: list[StoredArticle],
    *,
    fetcher: Fetcher | None = None,
    force: bool = False,
    run_id: str | None = None,
    run_kind: str = "article_backfill",
    stop_state_key: str = "last_backfill_stop_reason",
) -> ArticleBatchSummary:
    resolved_fetcher = fetcher or Fetcher(
        user_agent=config.user_agent,
        timeout_seconds=config.timeout_seconds,
    )
    resolved_run_id = run_id or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )
    fetched = 0
    failed = 0
    stop_reason = ""

    for index, article in enumerate(candidates):
        started_at = time.monotonic()
        _log_article_fetch(
            "article_fetch_start",
            article,
            run_id=resolved_run_id,
            run_kind=run_kind,
            queue_index=index + 1,
            queue_size=len(candidates),
            force=force,
            attempt_count_before=article.attempt_count,
        )
        result = _fetch_article(store, article, resolved_fetcher, config)
        if result.content:
            store.save_article_content(
                article,
                content_html=result.content.content_html,
                content_text=result.content.text,
                content_source=result.source,
            )
            fetched += 1
        else:
            failed += 1
            if result.stop_refresh:
                stop_reason = result.stop_reason
                store.set_state(stop_state_key, result.stop_reason)

        _log_article_fetch(
            "article_fetch_result",
            article,
            run_id=resolved_run_id,
            run_kind=run_kind,
            queue_index=index + 1,
            queue_size=len(candidates),
            force=force,
            attempt_count_before=article.attempt_count,
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            status=result.status or ("ok" if result.content else "unknown"),
            source=result.source,
            http_status=result.http_status,
            retry_after_seconds=result.retry_after_seconds,
            final_url=result.final_url,
            stop_refresh=result.stop_refresh,
            stop_reason=result.stop_reason,
            message=result.message,
        )

        if result.stop_refresh:
            break

        if index < len(candidates) - 1 and not result.stop_refresh:
            polite_delay(config)

    return ArticleBatchSummary(
        fetched=fetched,
        failed=failed,
        stop_reason=stop_reason,
    )


def _fetch_article(
    store: ArticleStore,
    article: StoredArticle,
    fetcher: Fetcher,
    config: AppConfig,
) -> ArticleFetchResult:
    if config.browser_fetch_enabled:
        browser_result = _fetch_article_with_browser_with_timeout(article.url, config)
        if browser_result.ok and browser_result.article is not None:
            return ArticleFetchResult(
                content=browser_result.article,
                source="economist_browser_fetch",
                status="ok",
                message=browser_result.message,
                http_status=browser_result.http_status,
                final_url=browser_result.final_url,
            )
        store.mark_fetch_error(
            article,
            status=browser_result.status,
            error=browser_result.message,
        )
        return ArticleFetchResult(
            status=browser_result.status,
            message=browser_result.message,
            http_status=browser_result.http_status,
            final_url=browser_result.final_url,
            stop_refresh=_is_refresh_stop_status(browser_result.status),
            stop_reason=(
                f"Stopped refresh after browser fetch returned "
                f"{browser_result.status} for {article.url}"
            ),
        )

    try:
        response = fetcher.fetch_text(article.url)
    except FetchError as exc:
        status = "rate_limited" if exc.status_code in {403, 429} else "fetch_failed"
        store.mark_fetch_error(article, status=status, error=str(exc))
        return ArticleFetchResult(
            status=status,
            message=str(exc),
            http_status=exc.status_code,
            retry_after_seconds=exc.retry_after_seconds,
            stop_refresh=status == "rate_limited",
            stop_reason=f"Stopped refresh after HTTP {exc.status_code} for {article.url}",
        )

    if is_cloudflare_challenge(response.text):
        store.mark_fetch_error(
            article,
            status="cloudflare_challenge",
            error="The article response was a Cloudflare challenge page.",
        )
        return ArticleFetchResult(
            status="cloudflare_challenge",
            message="The article response was a Cloudflare challenge page.",
            http_status=response.status,
            final_url=response.url,
            stop_refresh=True,
            stop_reason=f"Stopped refresh after Cloudflare challenge for {article.url}",
        )

    content = extract_article(response.text)
    if (
        content is None
        or len(content.text) < minimum_text_length_for_url(article.url)
        or looks_like_paywall_excerpt(content.text)
    ):
        store.mark_fetch_error(
            article,
            status="excerpt_or_login_required",
            error="The article page did not expose full subscriber text.",
        )
        return ArticleFetchResult(
            status="excerpt_or_login_required",
            message="The article page did not expose full subscriber text.",
            http_status=response.status,
            final_url=response.url,
            stop_refresh=True,
            stop_reason=f"Stopped refresh after login/excerpt page for {article.url}",
        )
    return ArticleFetchResult(
        content=content,
        source="http_article_fetch",
        status="ok",
        message="Fetched full article text with HTTP.",
        http_status=response.status,
        final_url=response.url,
    )


def _refresh_current_issue_if_stale(
    store: ArticleStore,
    fetcher: Fetcher,
    config: AppConfig,
    *,
    force: bool,
) -> None:
    if (
        not force
        and not _state_is_stale(
            store,
            "current_issue_last_refresh_at",
            config.current_issue_refresh_interval_seconds,
        )
    ):
        return

    current_issue = resolve_current_issue(
        fetcher,
        base_url=config.weekly_edition_base_url,
        lookahead_days=config.current_issue_lookahead_days,
    )
    article_count = _save_current_issue(store, current_issue)
    store.set_state("current_issue_id", current_issue.issue_id)
    store.set_state("current_issue_date", current_issue.issue_date)
    store.set_state("current_issue_url", current_issue.issue_url)
    store.set_state("current_issue_source", current_issue.source)
    store.set_state("current_issue_article_count", str(article_count))
    store.set_state("current_issue_error", current_issue.error)
    store.set_state("current_issue_last_refresh_at", now_iso())
    LOGGER.info(
        "current_issue %s",
        json.dumps(
            {
                "issue_id": current_issue.issue_id,
                "issue_url": current_issue.issue_url,
                "source": current_issue.source,
                "articles_seen": len(current_issue.articles),
                "articles_saved": article_count,
                "error": current_issue.error,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _save_current_issue(store: ArticleStore, current_issue: CurrentIssue) -> int:
    previous_issue_id = store.get_state("current_issue_id")
    previous_count = _int_state(store.get_state("current_issue_article_count"))
    if not current_issue.articles:
        if previous_issue_id == current_issue.issue_id and previous_count > 0:
            return previous_count
        return 0

    saved = 0
    for article in current_issue.articles:
        store.upsert_current_issue_article(
            FeedItem(
                title=article.title,
                link=article.url,
                guid=article.url,
                source="Weekly edition",
            ),
            issue_id=current_issue.issue_id,
            issue_date=current_issue.issue_date,
            issue_source=current_issue.source,
        )
        saved += 1
    return saved


def _int_state(raw: str | None) -> int:
    try:
        return int(raw or "0")
    except ValueError:
        return 0


def _refresh_world_in_brief_if_stale(
    store: ArticleStore,
    config: AppConfig,
    *,
    run_id: str,
    force: bool,
) -> ArticleFetchResult | None:
    if not config.world_in_brief_enabled or not config.browser_fetch_enabled:
        return None
    if (
        not force
        and not _state_is_stale(
            store,
            "world_in_brief_last_fetch_at",
            config.world_in_brief_refresh_interval_seconds,
        )
    ):
        return None

    started_at = time.monotonic()
    url = config.world_in_brief_url
    _log_fetch_payload(
        "article_fetch_start",
        title="The world in brief",
        url=url,
        canonical_url=canonical_url(url) or url,
        run_id=run_id,
        queue_index=0,
        queue_size=0,
        force=force,
        attempt_count_before=None,
        special_source="world_in_brief",
    )

    browser_result = _fetch_article_with_browser_with_timeout(url, config)
    store.set_state("world_in_brief_last_fetch_at", now_iso())
    if browser_result.ok and browser_result.article is not None:
        final_url = browser_result.final_url or url
        _save_world_in_brief(store, browser_result.article, final_url)
        result = ArticleFetchResult(
            content=browser_result.article,
            source="economist_world_in_brief",
            status="ok",
            message=browser_result.message,
            http_status=browser_result.http_status,
            final_url=final_url,
        )
    else:
        store.set_state("world_in_brief_last_error", browser_result.message)
        result = ArticleFetchResult(
            status=browser_result.status,
            message=browser_result.message,
            http_status=browser_result.http_status,
            final_url=browser_result.final_url,
            stop_refresh=_is_refresh_stop_status(browser_result.status),
            stop_reason=(
                "Stopped refresh after World in Brief browser fetch returned "
                f"{browser_result.status} for {url}"
            ),
        )

    _log_fetch_payload(
        "article_fetch_result",
        title="The world in brief",
        url=url,
        canonical_url=canonical_url(url) or url,
        run_id=run_id,
        queue_index=0,
        queue_size=0,
        force=force,
        attempt_count_before=None,
        elapsed_ms=int((time.monotonic() - started_at) * 1000),
        status=result.status or ("ok" if result.content else "unknown"),
        source=result.source,
        http_status=result.http_status,
        retry_after_seconds=result.retry_after_seconds,
        final_url=result.final_url,
        stop_refresh=result.stop_refresh,
        stop_reason=result.stop_reason,
        message=result.message,
        special_source="world_in_brief",
    )
    return result


def _save_world_in_brief(
    store: ArticleStore,
    article: ArticleContent,
    final_url: str,
    *,
    content_source: str = "economist_world_in_brief",
    published: str | None = None,
) -> StoredArticle:
    item = FeedItem(
        title=article.title or "The world in brief",
        link=final_url,
        guid=final_url,
        summary=_text_summary(article.text),
        published=published or _published_from_world_in_brief(article.text, final_url),
        source="The World in Brief",
    )
    stored = store.upsert_feed_item(_normal_feed_item(item))
    store.save_article_content(
        stored,
        content_html=article.content_html,
        content_text=article.text,
        content_source=content_source,
    )
    return stored


@dataclass(frozen=True)
class WorldInBriefIngestResult:
    ok: bool
    status: str
    message: str
    title: str = ""
    url: str = ""
    published: str | None = None


def ingest_world_in_brief_html(
    config: AppConfig,
    *,
    url: str,
    html: str,
    source_host: str = "",
) -> WorldInBriefIngestResult:
    """Save a World in Brief page that a signed-in browser elsewhere already loaded.

    This host's address is blocked by economist.com (Oct 2026), so a Mac on a home or
    hosted-Mac network loads the page in its own signed-in Chrome and posts the HTML
    here. Nothing is fetched from economist.com by this call.
    """
    if not WORLD_IN_BRIEF_DATE_RE.search(urlparse(url).path) or urlparse(url).hostname not in {
        "www.economist.com",
        "economist.com",
    }:
        return WorldInBriefIngestResult(False, "bad_url", "url is not a dated World in Brief page")
    if is_cloudflare_challenge(html):
        return WorldInBriefIngestResult(False, "cloudflare_challenge", "html is a Cloudflare challenge page")
    article = extract_article(html)
    if (
        article is None
        or len(article.text) < minimum_text_length_for_url(url)
        or looks_like_paywall_excerpt(article.text)
    ):
        return WorldInBriefIngestResult(
            False, "excerpt_or_login_required", "only short text was found; is the Mac signed in?"
        )
    with ArticleStore(config.database_path) as store:
        stored = _save_world_in_brief(
            store,
            article,
            url,
            content_source="economist_world_in_brief_push",
            published=_world_in_brief_updated_from_html(html),
        )
        store.set_state(
            "world_in_brief_last_push",
            json.dumps(
                {"at": now_iso(), "url": url, "title": article.title, "source_host": source_host[:80]},
                sort_keys=True,
            ),
        )
    return WorldInBriefIngestResult(
        True, "ok", "saved", title=article.title, url=url, published=stored.published
    )


WORLD_IN_BRIEF_UPDATED_TIME_RE = re.compile(
    r'<time\b[^>]*\bdatetime="([^"]+)"[^>]*>(?:\s|&nbsp;|\(|<[^>]+>)*updated\b', re.I
)


def _world_in_brief_updated_from_html(html: str) -> str | None:
    """The page's exact edit time: <time datetime="...Z">&nbsp;(<span>updated</span> 1h ago)</time>.

    The extracted article text drops that element, so read it from the HTML.
    """
    match = WORLD_IN_BRIEF_UPDATED_TIME_RE.search(html or "")
    if not match:
        return None
    updated_at = parse_datetime(match.group(1))
    return format_datetime(updated_at.astimezone(timezone.utc)) if updated_at else None


def world_in_brief_last_push(config: AppConfig) -> dict[str, object] | None:
    with ArticleStore(config.database_path) as store:
        raw = store.get_state("world_in_brief_last_push")
    return json.loads(raw) if raw else None


def _is_refresh_stop_status(status: str) -> bool:
    return status in {
        "blocked_by_cloudflare",
        "browser_fetch_timeout",
        "cloudflare_challenge",
        "excerpt_or_login_required",
        "rate_limited",
    }


def _fetch_article_with_browser_with_timeout(url: str, config: AppConfig) -> BrowserResult:
    timeout_seconds = max(0.0, config.browser_fetch_timeout_seconds)
    if timeout_seconds <= 0:
        return fetch_article_with_browser(url, config)

    context = multiprocessing.get_context("fork" if hasattr(os, "fork") else "spawn")
    parent_conn, child_conn = context.Pipe(duplex=False)
    process = context.Process(
        target=_browser_fetch_worker,
        args=(child_conn, url, config),
    )
    process.start()
    child_conn.close()

    try:
        if parent_conn.poll(timeout_seconds):
            try:
                result = parent_conn.recv()
            except EOFError:
                result = BrowserResult(
                    ok=False,
                    status="browser_fetch_failed",
                    message="Browser fetch worker exited without returning a result.",
                    url=url,
                    final_url=url,
                )
            process.join(timeout=5)
            if process.is_alive():
                _terminate_process_group(process)
            return result

        _terminate_process_group(process)
        return BrowserResult(
            ok=False,
            status="browser_fetch_timeout",
            message=f"Browser fetch exceeded {timeout_seconds:.0f} seconds.",
            url=url,
            final_url=url,
        )
    finally:
        parent_conn.close()


def _browser_fetch_worker(conn: object, url: str, config: AppConfig) -> None:
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    try:
        conn.send(fetch_article_with_browser(url, config))
    except BaseException as exc:  # noqa: BLE001 - preserve worker failure as telemetry.
        conn.send(
            BrowserResult(
                ok=False,
                status="browser_fetch_failed",
                message=str(exc),
                url=url,
                final_url=url,
            )
        )
    finally:
        conn.close()


def _terminate_process_group(process: multiprocessing.Process) -> None:
    if process.pid is not None and hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.terminate()
        except OSError:
            process.terminate()
    else:
        process.terminate()

    process.join(timeout=5)
    if not process.is_alive():
        return

    if process.pid is not None and hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
        except OSError:
            process.kill()
    else:
        process.kill()
    process.join(timeout=5)


def _is_stale(store: ArticleStore, refresh_interval_seconds: float) -> bool:
    return _state_is_stale(store, "last_refresh_at", refresh_interval_seconds)


def _state_is_stale(
    store: ArticleStore,
    state_key: str,
    refresh_interval_seconds: float,
) -> bool:
    last_refresh = parse_datetime(store.get_state(state_key))
    if last_refresh is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last_refresh).total_seconds()
    return elapsed >= refresh_interval_seconds


def _normal_feed_item(item: FeedItem) -> FeedItem:
    item.guid = item.guid or item.link
    return item


def _recent_feed_items(
    items: list[FeedItem],
    published_after: datetime | None,
) -> list[FeedItem]:
    if published_after is None:
        return items
    recent: list[FeedItem] = []
    for item in items:
        published = parse_datetime(item.published)
        if published is None or published >= published_after:
            recent.append(item)
    return recent


def polite_delay(config: AppConfig) -> None:
    low = max(0.0, config.min_article_delay_seconds)
    high = max(low, config.max_article_delay_seconds)
    time.sleep(random.uniform(low, high))


def _log_article_fetch(event: str, article: StoredArticle, **fields: object) -> None:
    _log_fetch_payload(
        event,
        title=article.title,
        url=article.url,
        canonical_url=article.canonical_url,
        **fields,
    )


def _log_fetch_payload(
    event: str,
    *,
    title: str,
    url: str,
    canonical_url: str,
    **fields: object,
) -> None:
    payload = {
        "event": event,
        "title": title,
        "url": url,
        "canonical_url": canonical_url,
        **fields,
    }
    LOGGER.info(
        "article_fetch %s",
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _published_from_world_in_brief_url(url: str) -> str | None:
    match = WORLD_IN_BRIEF_DATE_RE.search(url)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return format_datetime(datetime(year, month, day, tzinfo=timezone.utc))


def _published_from_world_in_brief(
    text: str,
    url: str,
    *,
    now: datetime | None = None,
) -> str | None:
    updated_at = _world_in_brief_updated_datetime(text, now=now)
    if updated_at is not None:
        return format_datetime(updated_at)
    return _published_from_world_in_brief_url(url)


def _world_in_brief_updated_datetime(
    text: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    match = WORLD_IN_BRIEF_UPDATED_RE.search(text or "")
    if not match:
        return None

    resolved_now = now or datetime.now(timezone.utc)
    if resolved_now.tzinfo is None:
        resolved_now = resolved_now.replace(tzinfo=timezone.utc)
    resolved_now = resolved_now.astimezone(timezone.utc)

    instant = match.group(1)
    if instant:
        return resolved_now

    amount = int(match.group(2))
    unit = match.group(3).lower()
    if unit.startswith("m"):
        delta = timedelta(minutes=amount)
    elif unit.startswith("h"):
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(days=amount)
    return resolved_now - delta


def _text_summary(text: str, *, limit: int = 320) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


@contextmanager
def refresh_lock(database_path: str):
    lock_path = Path(database_path).with_suffix(Path(database_path).suffix + ".refresh.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            import fcntl
        except ImportError:
            yield True
            return
        locked = False
        try:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            locked = True
            yield True
        finally:
            if locked:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
