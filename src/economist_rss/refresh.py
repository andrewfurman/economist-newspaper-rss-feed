from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import logging
from pathlib import Path
import random
import re
import time
from contextlib import contextmanager

from .browser import fetch_article_with_browser, minimum_text_length_for_url
from .config import AppConfig
from .extract import ArticleContent, extract_article, is_cloudflare_challenge
from .feed import FeedItem, parse_feed
from .fetch import FetchError, Fetcher
from .store import ArticleStore, StoredArticle
from .util import canonical_url, cutoff_datetime, now_iso, parse_datetime


LOGGER = logging.getLogger(__name__)
WORLD_IN_BRIEF_DATE_RE = re.compile(r"/the-world-in-brief/(\d{4})/(\d{2})/(\d{2})/")


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


def refresh_if_stale(
    config: AppConfig,
    *,
    force: bool = False,
    ignore_refresh_interval: bool = False,
) -> RefreshSummary:
    with _refresh_lock(config.database_path) as lock_acquired:
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

    for feed_config in config.feeds:
        feeds_checked += 1
        try:
            response = fetcher.fetch_text(feed_config.url)
        except FetchError as exc:
            store.set_state("last_feed_error", str(exc))
            continue
        try:
            items = parse_feed(response.text, feed_config.name)
        except Exception as exc:  # noqa: BLE001 - feed parser errors should not kill refresh.
            store.set_state(
                "last_feed_error",
                f"Could not parse feed {feed_config.url}: {exc}",
            )
            continue
        if feed_config.limit is not None:
            items = items[: feed_config.limit]
        items = _recent_feed_items(items, published_after)
        feed_items_seen += len(items)
        for item in items:
            store.upsert_feed_item(_normal_feed_item(item))

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

    candidates = store.pending_articles(
        limit=0 if stop_reason else max(0, fetch_budget - used_fetch_budget),
        retry_failed_after_seconds=config.retry_failed_after_seconds,
        exclude_url_patterns=config.exclude_url_patterns,
        published_after=published_after,
        force=force,
    )

    for index, article in enumerate(candidates):
        started_at = time.monotonic()
        _log_article_fetch(
            "article_fetch_start",
            article,
            run_id=run_id,
            queue_index=index + 1,
            queue_size=len(candidates),
            force=force,
            attempt_count_before=article.attempt_count,
        )
        result = _fetch_article(store, article, fetcher, config)
        if result.content:
            store.save_article_content(
                article,
                content_html=result.content.content_html,
                content_text=result.content.text,
                content_source=result.source,
            )
            articles_fetched += 1
        else:
            articles_failed += 1
            if result.stop_refresh:
                stop_reason = result.stop_reason
                store.set_state("last_refresh_stop_reason", result.stop_reason)

        _log_article_fetch(
            "article_fetch_result",
            article,
            run_id=run_id,
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
            _polite_delay(config)

    store.set_state("last_refresh_at", now_iso())
    return RefreshSummary(
        status="ok",
        feeds_checked=feeds_checked,
        feed_items_seen=feed_items_seen,
        articles_fetched=articles_fetched,
        articles_failed=articles_failed,
        stop_reason=stop_reason,
    )


def _fetch_article(
    store: ArticleStore,
    article: StoredArticle,
    fetcher: Fetcher,
    config: AppConfig,
) -> ArticleFetchResult:
    if config.browser_fetch_enabled:
        browser_result = fetch_article_with_browser(article.url, config)
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
    if content is None or len(content.text) < minimum_text_length_for_url(article.url):
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

    browser_result = fetch_article_with_browser(url, config)
    store.set_state("world_in_brief_last_fetch_at", now_iso())
    if browser_result.ok and browser_result.article is not None:
        final_url = browser_result.final_url or url
        item = FeedItem(
            title=browser_result.article.title or "The world in brief",
            link=final_url,
            guid=final_url,
            summary=_text_summary(browser_result.article.text),
            published=_published_from_world_in_brief_url(final_url),
            source="The World in Brief",
        )
        stored = store.upsert_feed_item(_normal_feed_item(item))
        store.save_article_content(
            stored,
            content_html=browser_result.article.content_html,
            content_text=browser_result.article.text,
            content_source="economist_world_in_brief",
        )
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


def _is_refresh_stop_status(status: str) -> bool:
    return status in {
        "blocked_by_cloudflare",
        "cloudflare_challenge",
        "excerpt_or_login_required",
        "rate_limited",
    }


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


def _polite_delay(config: AppConfig) -> None:
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


def _text_summary(text: str, *, limit: int = 320) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


@contextmanager
def _refresh_lock(database_path: str):
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
