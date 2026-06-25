from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .feed import FeedItem
from .util import canonical_url, normalized_datetime, now_iso, parse_datetime, stable_id


@dataclass(frozen=True)
class StoredArticle:
    canonical_url: str
    url: str
    guid: str
    title: str
    summary: str | None
    published: str | None
    published_at: str | None
    source: str | None
    categories: list[str]
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
              canonical_url, url, guid, title, summary, published, source, categories,
              published_at, first_seen_at, updated_at, attempt_count
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            on conflict(canonical_url) do update set
              url = excluded.url,
              guid = coalesce(nullif(excluded.guid, ''), articles.guid),
              title = excluded.title,
              summary = excluded.summary,
              published = excluded.published,
              published_at = excluded.published_at,
              source = excluded.source,
              categories = case
                when excluded.categories != '[]' then excluded.categories
                else articles.categories
              end,
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
                _encode_categories(item.categories),
                normalized_datetime(item.published),
                timestamp,
                timestamp,
            ),
        )
        self.conn.commit()
        return self.get_article(key)  # type: ignore[return-value]

    def get_article(self, url_or_key: str) -> StoredArticle | None:
        key = canonical_url(url_or_key) or url_or_key
        row = self.conn.execute(
            "select * from articles where canonical_url = ? or url = ? or guid = ?",
            (key, url_or_key, url_or_key),
        ).fetchone()
        return _row_to_article(row) if row else None

    def pending_articles(
        self,
        *,
        limit: int,
        retry_failed_after_seconds: float,
        exclude_url_patterns: Iterable[str],
        published_after: datetime | None = None,
        force: bool = False,
    ) -> list[StoredArticle]:
        if limit <= 0:
            return []
        rows = self.conn.execute(
            """
            select * from articles
            order by
              case when published_at is null or published_at = '' then 1 else 0 end,
              published_at desc,
              first_seen_at desc
            """
        ).fetchall()
        excluded = tuple(exclude_url_patterns)
        pending: list[StoredArticle] = []
        for row in rows:
            article = _row_to_article(row)
            if published_after and not _is_recent_article(article, published_after):
                continue
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

    def feed_items(
        self,
        *,
        limit: int | None = 500,
        published_after: datetime | None = None,
    ) -> list[FeedItem]:
        if limit is not None and limit <= 0:
            return []
        params: list[object] = []
        where = [
            "content_status = 'ok'",
            "content_html is not null",
            "content_html != ''",
        ]
        if published_after is not None:
            where.append("(published_at is null or published_at >= ?)")
            params.append(published_after.isoformat())
        limit_clause = ""
        if limit is not None:
            limit_clause = "limit ?"
            params.append(limit)

        rows = self.conn.execute(
            """
            select * from articles
            where """ + " and ".join(where) + """
            order by
              case when published_at is null or published_at = '' then 1 else 0 end,
              published_at desc,
              fetched_at desc
            """ + limit_clause + """
            """,
            params,
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
                categories=_decode_categories(row["categories"]),
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
              published_at text,
              source text,
              categories text,
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
        _ensure_column(self.conn, "articles", "published_at", "text")
        _ensure_column(self.conn, "articles", "categories", "text")
        _backfill_published_at(self.conn)
        self.conn.execute(
            "create index if not exists idx_articles_published_at on articles(published_at)"
        )
        self.conn.execute(
            "create index if not exists idx_articles_status on articles(content_status)"
        )
        self.conn.execute(
            "create index if not exists idx_articles_guid on articles(guid)"
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


def _encode_categories(categories: Iterable[str]) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for category in categories:
        normalized = category.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return json.dumps(unique, ensure_ascii=False)


def _decode_categories(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [
        category.strip()
        for category in decoded
        if isinstance(category, str) and category.strip()
    ]


def _is_recent_article(article: StoredArticle, published_after: datetime) -> bool:
    published = parse_datetime(article.published_at or article.published)
    return published is None or published >= published_after


def _row_to_article(row: sqlite3.Row) -> StoredArticle:
    return StoredArticle(
        canonical_url=row["canonical_url"],
        url=row["url"],
        guid=row["guid"] or "",
        title=row["title"] or "Untitled",
        summary=row["summary"],
        published=row["published"],
        published_at=row["published_at"],
        source=row["source"],
        categories=_decode_categories(row["categories"]),
        content_html=row["content_html"],
        content_text=row["content_text"],
        content_source=row["content_source"],
        content_status=row["content_status"],
        error=row["error"],
        fetched_at=row["fetched_at"],
        last_attempt_at=row["last_attempt_at"],
        attempt_count=int(row["attempt_count"] or 0),
    )


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"alter table {table_name} add column {column_name} {column_type}")


def _backfill_published_at(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        select canonical_url, published
        from articles
        where (published_at is null or published_at = '')
          and published is not null
          and published != ''
        """
    ).fetchall()
    for row in rows:
        published_at = normalized_datetime(row["published"])
        if not published_at:
            continue
        conn.execute(
            "update articles set published_at = ? where canonical_url = ?",
            (published_at, row["canonical_url"]),
        )
