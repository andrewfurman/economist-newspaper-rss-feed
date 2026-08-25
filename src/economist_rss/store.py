from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from .feed import FeedItem, categories_for_item
from .util import canonical_url, normalized_datetime, now_iso, parse_datetime, stable_id

SEARCH_INDEX_VERSION = "1"
CATEGORY_BACKFILL_VERSION = "1"


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
    issue_id: str | None
    issue_date: str | None
    issue_source: str | None
    content_html: str | None
    content_text: str | None
    content_source: str | None
    content_status: str | None
    error: str | None
    fetched_at: str | None
    last_attempt_at: str | None
    attempt_count: int
    fetch_requested_at: str | None
    fetch_request_count: int


@dataclass(frozen=True)
class CatalogIssue:
    issue_id: str
    issue_date: str
    issue_url: str
    source: str
    discovery_status: str
    article_count: int
    first_attempt_at: str
    last_attempt_at: str
    completed_at: str | None
    error: str
    attempt_count: int


@dataclass(frozen=True)
class CatalogStats:
    article_count: int
    full_text_count: int
    metadata_only_count: int
    earliest_published_at: str | None
    latest_published_at: str | None
    issues_discovered: int
    issues_failed: int


@dataclass(frozen=True)
class CategoryStat:
    name: str
    article_count: int


class ArticleStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.search_index_enabled = False
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
                _encode_categories(categories_for_item(item)),
                normalized_datetime(item.published),
                timestamp,
                timestamp,
            ),
        )
        if self.search_index_enabled:
            _sync_search_index_row(self.conn, key)
        self.conn.commit()
        return self.get_article(key)  # type: ignore[return-value]

    def get_article(self, url_or_key: str) -> StoredArticle | None:
        key = canonical_url(url_or_key) or url_or_key
        row = self.conn.execute(
            "select * from articles where canonical_url = ? or url = ? or guid = ?",
            (key, url_or_key, url_or_key),
        ).fetchone()
        return _row_to_article(row) if row else None

    def request_article_fetch(self, url_or_key: str) -> StoredArticle | None:
        article = self.get_article(url_or_key)
        if article is None or _has_full_text(article):
            return article
        timestamp = now_iso()
        self.conn.execute(
            """
            update articles
            set fetch_requested_at = coalesce(fetch_requested_at, ?),
                fetch_request_count = fetch_request_count + 1,
                updated_at = ?
            where canonical_url = ?
            """,
            (timestamp, timestamp, article.canonical_url),
        )
        self.conn.commit()
        return self.get_article(article.canonical_url)

    def requested_articles(
        self,
        *,
        limit: int,
        retry_failed_after_seconds: float,
        exclude_url_patterns: Iterable[str],
        force: bool = False,
    ) -> list[StoredArticle]:
        if limit <= 0:
            return []
        rows = self.conn.execute(
            """
            select * from articles
            where fetch_requested_at is not null
            order by fetch_requested_at asc, canonical_url asc
            """
        ).fetchall()
        excluded = tuple(exclude_url_patterns)
        pending: list[StoredArticle] = []
        for row in rows:
            article = _row_to_article(row)
            if any(pattern and pattern in article.url for pattern in excluded):
                continue
            if _has_full_text(article):
                continue
            if force or _needs_fetch(article, retry_failed_after_seconds):
                pending.append(article)
            if len(pending) >= limit:
                break
        return pending

    def pending_articles(
        self,
        *,
        limit: int,
        retry_failed_after_seconds: float,
        exclude_url_patterns: Iterable[str],
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        require_published: bool = False,
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
            if require_published and not parse_datetime(
                article.published_at or article.published
            ):
                continue
            if published_after and not _is_recent_article(article, published_after):
                continue
            if published_before and not _is_before_or_at(article, published_before):
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
                fetch_requested_at = null,
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
        if self.search_index_enabled:
            _sync_search_index_row(self.conn, article.canonical_url)
        self.conn.commit()

    def upsert_issue_article(
        self,
        item: FeedItem,
        *,
        issue_id: str,
        issue_date: str,
        issue_source: str,
    ) -> StoredArticle:
        article = self.upsert_feed_item(item)
        timestamp = now_iso()
        self.conn.execute(
            """
            update articles
            set issue_id = case
                  when issue_date is null or issue_date <= ? then ?
                  else issue_id
                end,
                issue_date = case
                  when issue_date is null or issue_date <= ? then ?
                  else issue_date
                end,
                issue_source = case
                  when issue_date is null or issue_date <= ? then ?
                  else issue_source
                end,
                updated_at = ?
            where canonical_url = ?
            """,
            (
                issue_date,
                issue_id,
                issue_date,
                issue_date,
                issue_date,
                issue_source,
                timestamp,
                article.canonical_url,
            ),
        )
        self.conn.commit()
        return self.get_article(article.canonical_url)  # type: ignore[return-value]

    def upsert_current_issue_article(
        self,
        item: FeedItem,
        *,
        issue_id: str,
        issue_date: str,
        issue_source: str,
    ) -> StoredArticle:
        return self.upsert_issue_article(
            item,
            issue_id=issue_id,
            issue_date=issue_date,
            issue_source=issue_source,
        )

    def get_catalog_issue(self, issue_id: str) -> CatalogIssue | None:
        row = self.conn.execute(
            "select * from catalog_issues where issue_id = ?", (issue_id,)
        ).fetchone()
        return _row_to_catalog_issue(row) if row else None

    def record_catalog_issue(
        self,
        *,
        issue_id: str,
        issue_date: str,
        issue_url: str,
        source: str,
        status: str,
        article_count: int = 0,
        error: str = "",
    ) -> CatalogIssue:
        timestamp = now_iso()
        completed_at = timestamp if status == "ok" else None
        self.conn.execute(
            """
            insert into catalog_issues (
              issue_id, issue_date, issue_url, source, discovery_status,
              article_count, first_attempt_at, last_attempt_at, completed_at,
              error, attempt_count
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            on conflict(issue_id) do update set
              issue_date = excluded.issue_date,
              issue_url = excluded.issue_url,
              source = excluded.source,
              discovery_status = excluded.discovery_status,
              article_count = excluded.article_count,
              last_attempt_at = excluded.last_attempt_at,
              completed_at = excluded.completed_at,
              error = excluded.error,
              attempt_count = catalog_issues.attempt_count + 1
            """,
            (
                issue_id,
                issue_date,
                issue_url,
                source,
                status,
                article_count,
                timestamp,
                timestamp,
                completed_at,
                error[:1000],
            ),
        )
        self.conn.commit()
        return self.get_catalog_issue(issue_id)  # type: ignore[return-value]

    def catalog_issue_needs_discovery(
        self,
        issue_id: str,
        *,
        retry_failed_after_seconds: float,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        issue = self.get_catalog_issue(issue_id)
        if issue is None:
            return True
        if issue.discovery_status == "ok":
            return False
        attempted_at = parse_datetime(issue.last_attempt_at)
        if attempted_at is None:
            return True
        elapsed = (datetime.now(timezone.utc) - attempted_at).total_seconds()
        backoff = retry_failed_after_seconds * max(1, min(issue.attempt_count, 8))
        return elapsed >= backoff

    def catalog_stats(self) -> CatalogStats:
        article = self.conn.execute(
            """
            select
              count(*) as article_count,
              sum(case
                when content_status = 'ok' and trim(coalesce(content_text, '')) != ''
                then 1 else 0 end
              ) as full_text_count,
              min(nullif(published_at, '')) as earliest_published_at,
              max(nullif(published_at, '')) as latest_published_at
            from articles
            """
        ).fetchone()
        issues = self.conn.execute(
            """
            select
              sum(case when discovery_status = 'ok' then 1 else 0 end) as discovered,
              sum(case when discovery_status != 'ok' then 1 else 0 end) as failed
            from catalog_issues
            """
        ).fetchone()
        article_count = int(article["article_count"] or 0)
        full_text_count = int(article["full_text_count"] or 0)
        return CatalogStats(
            article_count=article_count,
            full_text_count=full_text_count,
            metadata_only_count=article_count - full_text_count,
            earliest_published_at=article["earliest_published_at"],
            latest_published_at=article["latest_published_at"],
            issues_discovered=int(issues["discovered"] or 0),
            issues_failed=int(issues["failed"] or 0),
        )

    def category_stats(self) -> list[CategoryStat]:
        counts: Counter[str] = Counter()
        rows = self.conn.execute("select categories from articles").fetchall()
        for row in rows:
            counts.update(set(_decode_categories(row["categories"])))
        return [
            CategoryStat(name=name, article_count=article_count)
            for name, article_count in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ]

    def content_status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            select coalesce(nullif(content_status, ''), 'not_fetched') as status,
                   count(*) as article_count
            from articles
            group by coalesce(nullif(content_status, ''), 'not_fetched')
            order by article_count desc, status asc
            """
        ).fetchall()
        return {str(row["status"]): int(row["article_count"]) for row in rows}

    def queued_article_count(self) -> int:
        row = self.conn.execute(
            "select count(*) as article_count "
            "from articles where fetch_requested_at is not null"
        ).fetchone()
        return int(row["article_count"] or 0)

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
        current_issue_only: bool = False,
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
        rows = self.conn.execute(
            """
            select * from articles
            where """ + " and ".join(where) + """
            order by
              case when published_at is null or published_at = '' then 1 else 0 end,
              published_at desc,
              fetched_at desc
            """,
            params,
        ).fetchall()
        if current_issue_only:
            rows = _filter_current_issue_rows(rows, _current_issue_filter(self))
        items = [
            FeedItem(
                title=row["title"] or "Untitled",
                link=row["url"] or row["canonical_url"],
                guid=row["guid"] or row["canonical_url"],
                published=row["published"],
                summary=row["summary"],
                content_html=row["content_html"],
                content_text=row["content_text"],
                source=row["source"],
                categories=_decode_categories(row["categories"]),
            )
            for row in rows
        ]
        items = _latest_brief_items_only(items)
        if limit is not None:
            return items[:limit]
        return items

    def feed_item_count(
        self,
        *,
        published_after: datetime | None = None,
        current_issue_only: bool = False,
    ) -> int:
        params: list[object] = []
        where = [
            "content_status = 'ok'",
            "content_html is not null",
            "content_html != ''",
        ]
        if published_after is not None:
            where.append("(published_at is null or published_at >= ?)")
            params.append(published_after.isoformat())
        rows = self.conn.execute(
            """
            select url, guid, title, published, published_at, categories, issue_id
            from articles
            where """ + " and ".join(where) + """
            order by
              case when published_at is null or published_at = '' then 1 else 0 end,
              published_at desc,
              fetched_at desc
            """,
            params,
        ).fetchall()
        if current_issue_only:
            rows = _filter_current_issue_rows(rows, _current_issue_filter(self))

        seen_brief_groups: set[str] = set()
        count = 0
        for row in rows:
            brief_group = _brief_item_group(
                FeedItem(
                    title=row["title"] or "Untitled",
                    link=row["url"] or "",
                    guid=row["guid"] or row["url"] or "",
                    published=row["published"],
                    categories=_decode_categories(row["categories"]),
                )
            )
            if brief_group is not None:
                if brief_group in seen_brief_groups:
                    continue
                seen_brief_groups.add(brief_group)
            count += 1
        return count

    def search_items(
        self,
        *,
        query: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
        categories: Iterable[str] = (),
        limit: int | None = 500,
    ) -> list[FeedItem]:
        articles = self.catalog_articles(
            query=query,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            limit=limit,
            search_content=True,
        )
        return [_article_to_feed_item(article) for article in articles]

    def catalog_articles(
        self,
        *,
        query: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
        categories: Iterable[str] = (),
        limit: int | None = 500,
        offset: int = 0,
        search_content: bool = True,
        full_text_only: bool = False,
    ) -> list[StoredArticle]:
        if offset < 0 or (limit is not None and limit <= 0):
            return []

        tokens = _search_tokens(query)
        params: list[object] = []
        where: list[str] = []
        join = ""
        if tokens and search_content and self.search_index_enabled:
            join = (
                " join article_search"
                " on article_search.canonical_url = articles.canonical_url"
            )
            where.append("article_search match ?")
            params.append(" AND ".join(f'\"{token}\"' for token in tokens))
        elif tokens:
            fields = [
                "coalesce(articles.title, '')",
                "coalesce(articles.summary, '')",
                "coalesce(articles.categories, '')",
            ]
            if search_content:
                fields.append("coalesce(articles.content_text, '')")
            searchable_text = "lower(" + " || ' ' || ".join(fields) + ")"
            for token in tokens:
                where.append(f"{searchable_text} like ? escape '\\'")
                params.append(f"%{_escape_like(token.casefold())}%")

        if full_text_only:
            where.extend(
                [
                    "articles.content_status = 'ok'",
                    "trim(coalesce(articles.content_text, '')) != ''",
                ]
            )

        if start_date is not None:
            where.append("articles.published_at >= ?")
            params.append(
                datetime.combine(start_date, time.min, tzinfo=timezone.utc).isoformat()
            )
        if end_date is not None:
            where.append("articles.published_at < ?")
            params.append(
                datetime.combine(
                    end_date + timedelta(days=1),
                    time.min,
                    tzinfo=timezone.utc,
                ).isoformat()
            )

        normalized_categories = _unique_categories(categories)
        if normalized_categories:
            category_clauses = []
            for category in normalized_categories:
                category_clauses.append(
                    "lower(coalesce(articles.categories, '')) like ? escape '\\'"
                )
                encoded = json.dumps(category, ensure_ascii=False).casefold()
                params.append(f"%{_escape_like(encoded)}%")
            where.append("(" + " or ".join(category_clauses) + ")")

        where_sql = " and ".join(where) if where else "1 = 1"
        sql = f"""
            select articles.*
            from articles{join}
            where {where_sql}
            order by
              case when articles.published_at is null or articles.published_at = ''
                then 1 else 0 end,
              articles.published_at desc,
              articles.canonical_url desc
        """
        if limit is not None:
            sql += " limit ?"
            requested_rows = offset + limit
            params.append(max(requested_rows * 4, requested_rows + 50))

        rows = self.conn.execute(sql, params).fetchall()
        articles = _latest_brief_articles_only(
            [_row_to_article(row) for row in rows]
        )
        if limit is None:
            return articles[offset:]
        return articles[offset : offset + limit]

    def rebuild_search_index(self) -> int:
        if not self.search_index_enabled:
            return 0
        count = _rebuild_search_index(self.conn)
        self.conn.commit()
        return count

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
              issue_id text,
              issue_date text,
              issue_source text,
              content_html text,
              content_text text,
              content_source text,
              content_status text,
              error text,
              first_seen_at text not null,
              updated_at text not null,
              fetched_at text,
              last_attempt_at text,
              attempt_count integer not null default 0,
              fetch_requested_at text,
              fetch_request_count integer not null default 0
            )
            """
        )
        self.conn.execute(
            """
            create table if not exists catalog_issues (
              issue_id text primary key,
              issue_date text not null,
              issue_url text not null,
              source text not null,
              discovery_status text not null,
              article_count integer not null default 0,
              first_attempt_at text not null,
              last_attempt_at text not null,
              completed_at text,
              error text not null default '',
              attempt_count integer not null default 0
            )
            """
        )
        self.conn.execute("create index if not exists idx_articles_published on articles(published)")
        _ensure_column(self.conn, "articles", "published_at", "text")
        _ensure_column(self.conn, "articles", "categories", "text")
        _ensure_column(self.conn, "articles", "issue_id", "text")
        _ensure_column(self.conn, "articles", "issue_date", "text")
        _ensure_column(self.conn, "articles", "issue_source", "text")
        _ensure_column(self.conn, "articles", "fetch_requested_at", "text")
        _ensure_column(
            self.conn,
            "articles",
            "fetch_request_count",
            "integer not null default 0",
        )
        _backfill_published_at(self.conn)
        _backfill_categories(self.conn)
        self.conn.execute(
            "create index if not exists idx_articles_published_at on articles(published_at)"
        )
        self.conn.execute(
            "create index if not exists idx_articles_status on articles(content_status)"
        )
        self.conn.execute(
            "create index if not exists idx_articles_guid on articles(guid)"
        )
        self.conn.execute(
            "create index if not exists idx_articles_issue_id on articles(issue_id)"
        )
        self.conn.execute(
            "create index if not exists idx_articles_fetch_requested "
            "on articles(fetch_requested_at)"
        )
        self.conn.execute(
            "create index if not exists idx_catalog_issues_status "
            "on catalog_issues(discovery_status, issue_date)"
        )
        self.search_index_enabled = _ensure_search_index(self.conn)
        self.conn.commit()


def _needs_fetch(article: StoredArticle, retry_failed_after_seconds: float) -> bool:
    if _has_full_text(article):
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


def _has_full_text(article: StoredArticle) -> bool:
    return article.content_status == "ok" and bool(
        (article.content_text or "").strip()
    )


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


def _rows_to_feed_items(rows: Iterable[sqlite3.Row]) -> list[FeedItem]:
    return [_article_to_feed_item(_row_to_article(row)) for row in rows]


def _article_to_feed_item(article: StoredArticle) -> FeedItem:
    return FeedItem(
        title=article.title,
        link=article.url or article.canonical_url,
        guid=article.guid or article.canonical_url,
        published=article.published,
        summary=article.summary,
        content_html=article.content_html,
        content_text=article.content_text,
        source=article.source,
        categories=article.categories,
    )


def _search_tokens(query: str) -> list[str]:
    without_possessives = re.sub(
        r"(?<=[^\W_])['\u2019]s\b",
        "",
        query,
        flags=re.IGNORECASE | re.UNICODE,
    )
    tokens = re.findall(r"[^\W_]+", without_possessives, flags=re.UNICODE)
    return [token.casefold() for token in tokens if token.strip()]


def _unique_categories(categories: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for category in categories:
        normalized = category.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ensure_search_index(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            """
            create virtual table if not exists article_search using fts5(
              canonical_url unindexed,
              title,
              summary,
              categories,
              content_text,
              tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).casefold():
            return False
        raise

    version_row = conn.execute(
        "select value from state where key = 'search_index_version'"
    ).fetchone()
    article_count = int(conn.execute("select count(*) from articles").fetchone()[0])
    index_count = int(conn.execute("select count(*) from article_search").fetchone()[0])
    if (
        version_row is None
        or str(version_row["value"]) != SEARCH_INDEX_VERSION
        or article_count != index_count
    ):
        _rebuild_search_index(conn)
    return True


def _rebuild_search_index(conn: sqlite3.Connection) -> int:
    conn.execute("delete from article_search")
    conn.execute(
        """
        insert into article_search (
          canonical_url, title, summary, categories, content_text
        )
        select
          canonical_url,
          coalesce(title, ''),
          coalesce(summary, ''),
          coalesce(categories, ''),
          coalesce(content_text, '')
        from articles
        """
    )
    count = int(conn.execute("select count(*) from article_search").fetchone()[0])
    conn.execute(
        """
        insert into state (key, value, updated_at)
        values ('search_index_version', ?, ?)
        on conflict(key) do update set
          value = excluded.value,
          updated_at = excluded.updated_at
        """,
        (SEARCH_INDEX_VERSION, now_iso()),
    )
    return count


def _sync_search_index_row(conn: sqlite3.Connection, canonical_url: str) -> None:
    conn.execute("delete from article_search where canonical_url = ?", (canonical_url,))
    conn.execute(
        """
        insert into article_search (
          canonical_url, title, summary, categories, content_text
        )
        select
          canonical_url,
          coalesce(title, ''),
          coalesce(summary, ''),
          coalesce(categories, ''),
          coalesce(content_text, '')
        from articles
        where canonical_url = ?
        """,
        (canonical_url,),
    )


def _is_recent_article(article: StoredArticle, published_after: datetime) -> bool:
    published = parse_datetime(article.published_at or article.published)
    return published is None or published >= published_after


def _is_before_or_at(article: StoredArticle, published_before: datetime) -> bool:
    published = parse_datetime(article.published_at or article.published)
    return published is None or published <= published_before


@dataclass(frozen=True)
class _CurrentIssueFilter:
    issue_id: str
    issue_date: date
    strict_issue_membership: bool


def _current_issue_filter(store: ArticleStore) -> _CurrentIssueFilter | None:
    issue_id = (store.get_state("current_issue_id") or "").strip()
    issue_date_raw = (store.get_state("current_issue_date") or "").strip()
    if not issue_id or not issue_date_raw:
        return None
    try:
        issue_date = date.fromisoformat(issue_date_raw)
    except ValueError:
        return None
    article_count = _int_state(store.get_state("current_issue_article_count"))
    return _CurrentIssueFilter(
        issue_id=issue_id,
        issue_date=issue_date,
        strict_issue_membership=article_count > 0,
    )


def _filter_current_issue_rows(
    rows: list[sqlite3.Row],
    current_issue: _CurrentIssueFilter | None,
) -> list[sqlite3.Row]:
    if current_issue is None:
        return rows
    return [row for row in rows if _row_matches_current_issue(row, current_issue)]


def _row_matches_current_issue(
    row: sqlite3.Row,
    current_issue: _CurrentIssueFilter,
) -> bool:
    issue_id = (row["issue_id"] or "").strip()
    if issue_id == current_issue.issue_id:
        return True
    if issue_id:
        return False

    published = parse_datetime(row["published_at"] or row["published"])
    if published is None:
        return False

    published_date = published.date()
    if current_issue.strict_issue_membership:
        return published_date >= current_issue.issue_date

    previous_issue_date = current_issue.issue_date - timedelta(days=7)
    return previous_issue_date < published_date


def _int_state(raw: str | None) -> int:
    try:
        return int(raw or "0")
    except ValueError:
        return 0


def _latest_brief_items_only(items: list[FeedItem]) -> list[FeedItem]:
    seen_brief_groups: set[str] = set()
    filtered: list[FeedItem] = []
    for item in items:
        brief_group = _brief_item_group(item)
        if brief_group is not None:
            if brief_group in seen_brief_groups:
                continue
            seen_brief_groups.add(brief_group)
        filtered.append(item)
    return filtered


def _latest_brief_articles_only(
    articles: list[StoredArticle],
) -> list[StoredArticle]:
    seen_brief_groups: set[str] = set()
    filtered: list[StoredArticle] = []
    for article in articles:
        brief_group = _brief_item_group(_article_to_feed_item(article))
        if brief_group is not None:
            if brief_group in seen_brief_groups:
                continue
            seen_brief_groups.add(brief_group)
        filtered.append(article)
    return filtered


def _brief_item_group(item: FeedItem) -> str | None:
    categories = set(categories_for_item(item))
    if "The World in Brief" in categories:
        return "world_in_brief"
    if {"In Brief", "United States"}.issubset(categories):
        return "united_states_in_brief"
    return None


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
        issue_id=row["issue_id"],
        issue_date=row["issue_date"],
        issue_source=row["issue_source"],
        content_html=row["content_html"],
        content_text=row["content_text"],
        content_source=row["content_source"],
        content_status=row["content_status"],
        error=row["error"],
        fetched_at=row["fetched_at"],
        last_attempt_at=row["last_attempt_at"],
        attempt_count=int(row["attempt_count"] or 0),
        fetch_requested_at=row["fetch_requested_at"],
        fetch_request_count=int(row["fetch_request_count"] or 0),
    )


def _row_to_catalog_issue(row: sqlite3.Row) -> CatalogIssue:
    return CatalogIssue(
        issue_id=row["issue_id"],
        issue_date=row["issue_date"],
        issue_url=row["issue_url"],
        source=row["source"],
        discovery_status=row["discovery_status"],
        article_count=int(row["article_count"] or 0),
        first_attempt_at=row["first_attempt_at"],
        last_attempt_at=row["last_attempt_at"],
        completed_at=row["completed_at"],
        error=row["error"] or "",
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


def _backfill_categories(conn: sqlite3.Connection) -> None:
    version = conn.execute(
        "select value from state where key = 'category_backfill_version'"
    ).fetchone()
    if version is not None and str(version["value"]) == CATEGORY_BACKFILL_VERSION:
        return

    rows = conn.execute(
        "select canonical_url, url, guid, title, categories from articles"
    ).fetchall()
    changed = False
    for row in rows:
        item = FeedItem(
            title=row["title"] or "Untitled",
            link=row["url"] or row["canonical_url"],
            guid=row["guid"] or row["canonical_url"],
            categories=_decode_categories(row["categories"]),
        )
        encoded = _encode_categories(categories_for_item(item))
        if encoded == (row["categories"] or "[]"):
            continue
        conn.execute(
            "update articles set categories = ? where canonical_url = ?",
            (encoded, row["canonical_url"]),
        )
        changed = True
    if changed:
        conn.execute("delete from state where key = 'search_index_version'")
    conn.execute(
        """
        insert into state (key, value, updated_at)
        values ('category_backfill_version', ?, ?)
        on conflict(key) do update set
          value = excluded.value,
          updated_at = excluded.updated_at
        """,
        (CATEGORY_BACKFILL_VERSION, now_iso()),
    )
