from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import format_datetime
import json
import logging
import re

from .browser import fetch_html_with_browser
from .config import AppConfig
from .extract import is_cloudflare_challenge
from .feed import FeedItem
from .fetch import FetchError, Fetcher
from .issue import parse_weekly_edition_articles
from .refresh import fetch_article_batch, polite_delay, refresh_lock
from .store import ArticleStore
from .util import now_iso


LOGGER = logging.getLogger(__name__)
DIGITAL_ARCHIVE_START = date(1997, 1, 4)
MAX_ISSUES_PER_RUN = 10
ARTICLE_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")
STOP_STATUSES = {"cloudflare_challenge", "rate_limited"}


@dataclass(frozen=True)
class CatalogDiscoverySummary:
    status: str
    issues_considered: int
    issues_attempted: int
    issues_discovered: int
    issues_failed: int
    articles_seen: int
    stop_reason: str = ""


@dataclass(frozen=True)
class CatalogContentSummary:
    status: str
    candidates: int
    articles_fetched: int
    articles_failed: int
    stop_reason: str = ""


@dataclass(frozen=True)
class _PageFetch:
    ok: bool
    status: str
    message: str
    source: str
    html: str = ""
    http_status: int | None = None


def weekly_issue_dates(start_date: date, end_date: date) -> list[date]:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    first_saturday = start_date + timedelta(days=(5 - start_date.weekday()) % 7)
    issue_dates: list[date] = []
    current = first_saturday
    while current <= end_date:
        issue_dates.append(current)
        current += timedelta(days=7)
    return issue_dates


def discover_catalog(
    config: AppConfig,
    *,
    start_date: date = DIGITAL_ARCHIVE_START,
    end_date: date | None = None,
    max_issues: int = 1,
    force: bool = False,
) -> CatalogDiscoverySummary:
    if max_issues < 1 or max_issues > MAX_ISSUES_PER_RUN:
        raise ValueError(
            f"max_issues must be between 1 and {MAX_ISSUES_PER_RUN}"
        )
    resolved_end = end_date or datetime.now(timezone.utc).date()
    issue_dates = list(reversed(weekly_issue_dates(start_date, resolved_end)))

    with refresh_lock(config.database_path) as lock_acquired:
        if not lock_acquired:
            return CatalogDiscoverySummary(
                status="skipped",
                issues_considered=len(issue_dates),
                issues_attempted=0,
                issues_discovered=0,
                issues_failed=0,
                articles_seen=0,
                stop_reason="refresh_already_running",
            )

        fetcher = Fetcher(
            user_agent=config.user_agent,
            timeout_seconds=config.timeout_seconds,
        )
        attempted = 0
        discovered = 0
        failed = 0
        articles_seen = 0
        stop_reason = ""

        with ArticleStore(config.database_path) as store:
            store.set_state("catalog_last_discovery_stop_reason", "")
            for issue_date in issue_dates:
                issue_id = issue_date.isoformat()
                if not store.catalog_issue_needs_discovery(
                    issue_id,
                    retry_failed_after_seconds=config.retry_failed_after_seconds,
                    force=force,
                ):
                    continue
                if attempted >= max_issues:
                    break

                attempted += 1
                issue_url = (
                    f"{config.weekly_edition_base_url.rstrip('/')}/{issue_id}"
                )
                page = _fetch_issue_page(issue_url, fetcher, config)
                if not page.ok:
                    failed += 1
                    store.record_catalog_issue(
                        issue_id=issue_id,
                        issue_date=issue_id,
                        issue_url=issue_url,
                        source=page.source,
                        status=page.status,
                        error=page.message,
                    )
                    _log_discovery(issue_id, issue_url, page, article_count=0)
                    if page.status in STOP_STATUSES:
                        stop_reason = page.message
                        store.set_state(
                            "catalog_last_discovery_stop_reason", stop_reason
                        )
                        break
                    if attempted < max_issues:
                        polite_delay(config)
                    continue

                articles = parse_weekly_edition_articles(page.html, issue_url)
                if not articles:
                    failed += 1
                    message = (
                        "The weekly-edition page contained no dated article links; "
                        "discovery stopped to avoid repeated malformed requests."
                    )
                    empty_page = _PageFetch(
                        ok=False,
                        status="empty_issue_page",
                        message=message,
                        source=page.source,
                        http_status=page.http_status,
                    )
                    store.record_catalog_issue(
                        issue_id=issue_id,
                        issue_date=issue_id,
                        issue_url=issue_url,
                        source=page.source,
                        status=empty_page.status,
                        error=message,
                    )
                    _log_discovery(issue_id, issue_url, empty_page, article_count=0)
                    stop_reason = message
                    store.set_state("catalog_last_discovery_stop_reason", message)
                    break

                for article in articles:
                    store.upsert_issue_article(
                        FeedItem(
                            title=article.title,
                            link=article.url,
                            guid=article.url,
                            published=_published_from_article_url(article.url),
                            source="Weekly edition archive",
                        ),
                        issue_id=issue_id,
                        issue_date=issue_id,
                        issue_source=page.source,
                    )
                count = len(articles)
                articles_seen += count
                discovered += 1
                store.record_catalog_issue(
                    issue_id=issue_id,
                    issue_date=issue_id,
                    issue_url=issue_url,
                    source=page.source,
                    status="ok",
                    article_count=count,
                )
                _log_discovery(issue_id, issue_url, page, article_count=count)
                if attempted < max_issues:
                    polite_delay(config)

            store.set_state("catalog_last_discovery_at", now_iso())

    return CatalogDiscoverySummary(
        status="ok" if not stop_reason else "stopped",
        issues_considered=len(issue_dates),
        issues_attempted=attempted,
        issues_discovered=discovered,
        issues_failed=failed,
        articles_seen=articles_seen,
        stop_reason=stop_reason,
    )


def backfill_catalog_content(
    config: AppConfig,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    max_articles: int | None = None,
    force: bool = False,
) -> CatalogContentSummary:
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    configured_budget = max(0, config.max_articles_per_refresh)
    requested_budget = configured_budget if max_articles is None else max_articles
    if requested_budget < 1:
        raise ValueError("max_articles must be a positive integer")
    budget = min(requested_budget, configured_budget)
    if budget < 1:
        raise ValueError("max_articles_per_refresh must be at least 1")

    published_after = (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        if start_date
        else None
    )
    published_before = (
        datetime.combine(end_date, time.max, tzinfo=timezone.utc)
        if end_date
        else None
    )

    with refresh_lock(config.database_path) as lock_acquired:
        if not lock_acquired:
            return CatalogContentSummary(
                status="skipped",
                candidates=0,
                articles_fetched=0,
                articles_failed=0,
                stop_reason="refresh_already_running",
            )
        with ArticleStore(config.database_path) as store:
            candidates = store.pending_articles(
                limit=budget,
                retry_failed_after_seconds=config.retry_failed_after_seconds,
                exclude_url_patterns=config.exclude_url_patterns,
                published_after=published_after,
                published_before=published_before,
                require_published=bool(start_date or end_date),
                force=force,
            )
            store.set_state("catalog_last_content_stop_reason", "")
            batch = fetch_article_batch(
                store,
                config,
                candidates,
                force=force,
                run_kind="catalog_content_backfill",
                stop_state_key="catalog_last_content_stop_reason",
            )
            store.set_state("catalog_last_content_fetch_at", now_iso())

    return CatalogContentSummary(
        status="ok" if not batch.stop_reason else "stopped",
        candidates=len(candidates),
        articles_fetched=batch.fetched,
        articles_failed=batch.failed,
        stop_reason=batch.stop_reason,
    )


def _fetch_issue_page(
    url: str,
    fetcher: Fetcher,
    config: AppConfig,
) -> _PageFetch:
    if config.browser_fetch_enabled:
        result = fetch_html_with_browser(url, config)
        return _PageFetch(
            ok=result.ok,
            status=result.status,
            message=result.message,
            source="weeklyedition_authenticated_browser",
            html=result.html,
            http_status=result.http_status,
        )

    try:
        response = fetcher.fetch_text(url)
    except FetchError as exc:
        if exc.status_code in {403, 429}:
            status = "rate_limited"
        elif exc.status_code == 404:
            status = "not_found"
        else:
            status = "fetch_failed"
        return _PageFetch(
            ok=False,
            status=status,
            message=str(exc),
            source="weeklyedition_http",
            http_status=exc.status_code,
        )
    if is_cloudflare_challenge(response.text):
        return _PageFetch(
            ok=False,
            status="cloudflare_challenge",
            message="The weekly-edition response was a Cloudflare challenge page.",
            source="weeklyedition_http",
            http_status=response.status,
        )
    return _PageFetch(
        ok=True,
        status="ok",
        message="Fetched weekly-edition metadata page with HTTP.",
        source="weeklyedition_http",
        html=response.text,
        http_status=response.status,
    )


def _published_from_article_url(url: str) -> str | None:
    match = ARTICLE_DATE_RE.search(url)
    if not match:
        return None
    try:
        published = datetime(
            *(int(part) for part in match.groups()),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return format_datetime(published)


def _log_discovery(
    issue_id: str,
    issue_url: str,
    page: _PageFetch,
    *,
    article_count: int,
) -> None:
    LOGGER.info(
        "catalog_discovery %s",
        json.dumps(
            {
                "event": "catalog_issue_discovery",
                "issue_id": issue_id,
                "issue_url": issue_url,
                "source": page.source,
                "status": page.status,
                "http_status": page.http_status,
                "article_count": article_count,
                "message": page.message,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
