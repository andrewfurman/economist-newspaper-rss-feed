from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import random
import time
from contextlib import contextmanager

from .browser import fetch_article_with_browser
from .config import AppConfig
from .extract import ArticleContent, extract_article, is_cloudflare_challenge
from .feed import FeedItem, parse_feed
from .fetch import FetchError, Fetcher
from .store import ArticleStore, StoredArticle
from .util import cutoff_datetime, now_iso, parse_datetime


@dataclass(frozen=True)
class RefreshSummary:
    status: str
    feeds_checked: int
    feed_items_seen: int
    articles_fetched: int
    articles_failed: int
    skipped_reason: str = ""


def refresh_if_stale(config: AppConfig, *, force: bool = False) -> RefreshSummary:
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
            if not force and not _is_stale(store, config.refresh_interval_seconds):
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
    published_after = cutoff_datetime(config.article_lookback_days)

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

    candidates = store.pending_articles(
        limit=max(0, config.max_articles_per_refresh),
        retry_failed_after_seconds=config.retry_failed_after_seconds,
        exclude_url_patterns=config.exclude_url_patterns,
        published_after=published_after,
        force=force,
    )

    for index, article in enumerate(candidates):
        result = _fetch_article(store, article, fetcher, config)
        if result:
            content, source = result
            store.save_article_content(
                article,
                content_html=content.content_html,
                content_text=content.text,
                content_source=source,
            )
            articles_fetched += 1
        else:
            articles_failed += 1

        if index < len(candidates) - 1:
            _polite_delay(config)

    store.set_state("last_refresh_at", now_iso())
    return RefreshSummary(
        status="ok",
        feeds_checked=feeds_checked,
        feed_items_seen=feed_items_seen,
        articles_fetched=articles_fetched,
        articles_failed=articles_failed,
    )


def _fetch_article(
    store: ArticleStore,
    article: StoredArticle,
    fetcher: Fetcher,
    config: AppConfig,
) -> tuple[ArticleContent, str] | None:
    if config.browser_fetch_enabled:
        browser_result = fetch_article_with_browser(article.url, config)
        if browser_result.ok and browser_result.article is not None:
            return browser_result.article, "economist_browser_fetch"

    try:
        response = fetcher.fetch_text(article.url)
    except FetchError as exc:
        status = "rate_limited" if exc.status_code in {403, 429} else "fetch_failed"
        store.mark_fetch_error(article, status=status, error=str(exc))
        return None

    if is_cloudflare_challenge(response.text):
        store.mark_fetch_error(
            article,
            status="cloudflare_challenge",
            error="The article response was a Cloudflare challenge page.",
        )
        return None

    content = extract_article(response.text)
    if content is None or len(content.text) < 700:
        store.mark_fetch_error(
            article,
            status="excerpt_or_login_required",
            error="The article page did not expose full subscriber text.",
        )
        return None
    return content, "http_article_fetch"


def _is_stale(store: ArticleStore, refresh_interval_seconds: float) -> bool:
    last_refresh = parse_datetime(store.get_state("last_refresh_at"))
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
