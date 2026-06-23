from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable

from .feed import FeedItem
from .util import canonical_url, now_iso, parse_datetime, stable_id


@dataclass(frozen=True)
class StoredArticle:
    canonical_url: str
    url: str
    guid: str
    title: str
    summary: str | None
    published: str | None
    source: str | None
    content_html: str | None
    content_text: str | None
    content_source: str | None
    content_status: str | None
    error: str | None
    fetched_at: str | None
    last_attempt_at: str | None
    attempt_count: int


class ArticleStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ArticleStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("select value from state where key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            insert into state (key, value, updated_at)
            values (?, ?, ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now_iso()),
        )
        self.conn.commit()

    def upsert_feed_item(self, item: FeedItem) -> StoredArticle:
        key = canonical_url(item.link)
        if not key:
            key = stable_id(item.guid, item.title)
        timestamp = now_iso()
        self.conn.execute(
            """
            insert into articles (
              canonical_url, url, guid, title, summary, published, source,
              first_seen_at, updated_at, attempt_count
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            on conflict(canonical_url) do update set
              url = excluded.url,
              guid = coalesce(nullif(excluded.guid, ''), articles.guid),
              title = excluded.title,
              summary = excluded.summary,
              published = excluded.published,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (
                key,
                item.link,
                item.guid,
                item.title,
                item.summary,
                item.published,
                item.source,
                timestamp,
                timestamp,
            ),
        )
        self.conn.commit()
        return self.get_article(key)  # type: ignore[return-value]

    def get_article(self, url_or_key: str) -> StoredArticle | None:
        key = canonical_url(url_or_key) or url_or_key
        row = self.conn.execute(
            "select * from articles where canonical_url = ? or url = ?",
            (key, url_or_key),
        ).fetchone()
        return _row_to_article(row) if row else None

    def pending_articles(
        self,
        *,
        limit: int,
        retry_failed_after_seconds: float,
        exclude_url_patterns: Iterable[str],
        force: bool = False,
    ) -> list[StoredArticle]:
        rows = self.conn.execute(
            """
            select * from articles
            order by
              case when published is null or published = '' then 1 else 0 end,
              published desc,
              first_seen_at desc
            """
        ).fetchall()
        excluded = tuple(exclude_url_patterns)
        pending: list[StoredArticle] = []
        for row in rows:
            article = _row_to_article(row)
            if any(pattern and pattern in article.url for pattern in excluded):
                continue
            if article.content_status == "ok" and article.content_html:
                continue
            if force or _needs_fetch(article, retry_failed_after_seconds):
                pending.append(article)
            if len(pending) >= limit:
                break
        return pending

    def save_article_content(
        self,
        article: StoredArticle,
        *,
        content_html: str,
        content_text: str,
        content_source: str,
    ) -> None:
        timestamp = now_iso()
        self.conn.execute(
            """
            update articles
            set content_html = ?,
                content_text = ?,
                content_source = ?,
                content_status = 'ok',
                error = '',
                fetched_at = ?,
                last_attempt_at = ?,
                attempt_count = attempt_count + 1,
                updated_at = ?
            where canonical_url = ?
            """,
            (
                content_html,
                content_text,
                content_source,
                timestamp,
                timestamp,
                timestamp,
                article.canonical_url,
            ),
        )
        self.conn.commit()

    def mark_fetch_error(self, article: StoredArticle, *, status: str, error: str) -> None:
        timestamp = now_iso()
        self.conn.execute(
            """
            update articles
            set content_status = ?,
                error = ?,
                last_attempt_at = ?,
                attempt_count = attempt_count + 1,
                updated_at = ?
            where canonical_url = ?
            """,
            (status, error[:1000], timestamp, timestamp, article.canonical_url),
        )
        self.conn.commit()

    def feed_items(self, *, limit: int = 200) -> list[FeedItem]:
        rows = self.conn.execute(
            """
            select * from articles
            where content_status = 'ok' and content_html is not null and content_html != ''
            order by
              case when published is null or published = '' then 1 else 0 end,
              published desc,
              fetched_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [
            FeedItem(
                title=row["title"] or "Untitled",
                link=row["url"] or row["canonical_url"],
                guid=row["guid"] or row["canonical_url"],
                published=row["published"],
                summary=row["summary"],
                content_html=row["content_html"],
                source=row["source"],
            )
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            create table if not exists state (
              key text primary key,
              value text not null,
              updated_at text not null
            )
            """
        )
        self.conn.execute(
            """
            create table if not exists articles (
              canonical_url text primary key,
              url text not null,
              guid text,
              title text not null,
              summary text,
              published text,
              source text,
              content_html text,
              content_text text,
              content_source text,
              content_status text,
              error text,
              first_seen_at text not null,
              updated_at text not null,
              fetched_at text,
              last_attempt_at text,
              attempt_count integer not null default 0
            )
            """
        )
        self.conn.execute("create index if not exists idx_articles_published on articles(published)")
        self.conn.execute(
            "create index if not exists idx_articles_status on articles(content_status)"
        )
        self.conn.commit()


def _needs_fetch(article: StoredArticle, retry_failed_after_seconds: float) -> bool:
    if article.content_status == "ok" and article.content_html:
        return False
    if not article.last_attempt_at:
        return True
    attempted_at = parse_datetime(article.last_attempt_at)
    if attempted_at is None:
        return True
    from datetime import datetime, timezone

    elapsed = (datetime.now(timezone.utc) - attempted_at).total_seconds()
    backoff = retry_failed_after_seconds * max(1, min(article.attempt_count, 8))
    return elapsed >= backoff


def _row_to_article(row: sqlite3.Row) -> StoredArticle:
    return StoredArticle(
        canonical_url=row["canonical_url"],
        url=row["url"],
        guid=row["guid"] or "",
        title=row["title"] or "Untitled",
        summary=row["summary"],
        published=row["published"],
        source=row["source"],
        content_html=row["content_html"],
        content_text=row["content_text"],
        content_source=row["content_source"],
        content_status=row["content_status"],
        error=row["error"],
        fetched_at=row["fetched_at"],
        last_attempt_at=row["last_attempt_at"],
        attempt_count=int(row["attempt_count"] or 0),
    )
