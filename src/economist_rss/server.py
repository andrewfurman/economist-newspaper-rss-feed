from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hmac
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Lock
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from .config import AppConfig
from .feed import FeedItem, build_rss, categories_for_item, category_for_slug
from .refresh import refresh_if_stale
from .store import ArticleStore, StoredArticle
from .util import cutoff_datetime

CATEGORY_FEED_PREFIX = "/rss/category/"
CATEGORY_FEED_SUFFIX = ".xml"
MAX_RSS_ITEM_LIMIT = 500
DEFAULT_API_ITEM_LIMIT = 50
MAX_API_ITEM_LIMIT = 500
MAX_API_OFFSET = 100_000
MAX_JSON_BODY_BYTES = 16_384


@dataclass(frozen=True)
class CatalogSearchQuery:
    query: str = ""
    start_date: date | None = None
    end_date: date | None = None

    @property
    def active(self) -> bool:
        return bool(self.query or self.start_date or self.end_date)


class EconomistRssServer:
    def __init__(self, config: AppConfig, *, host: str, port: int) -> None:
        self.config = config
        self.host = host
        self.port = port
        self.lock = Lock()

    def serve_forever(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/healthz":
                    self._send_text("ok\n", content_type="text/plain")
                    return
                if parsed.path in {
                    "/api/search",
                    "/api/articles",
                    "/api/articles/status",
                }:
                    if not _authorized(
                        self.headers.get("Authorization", ""),
                        parsed.query,
                        "ECONOMIST_FEED_TOKEN",
                    ):
                        self._send_json({"error": "unauthorized"}, status=401)
                        return
                    try:
                        if parsed.path == "/api/search":
                            payload = _api_search_response(owner.config, parsed.query)
                            status = 200
                        elif parsed.path == "/api/articles":
                            payload = _api_articles_response(owner.config, parsed.query)
                            status = 200
                        else:
                            payload, status = _api_article_status_response(
                                owner.config,
                                parsed.query,
                            )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=400)
                        return
                    self._send_json(payload, status=status)
                    return
                if parsed.path == "/article.txt":
                    if not _authorized(
                        self.headers.get("Authorization", ""),
                        parsed.query,
                        "ECONOMIST_FEED_TOKEN",
                    ):
                        self.send_error(401)
                        return
                    lookup_key = _article_lookup_key(parsed.query)
                    if lookup_key is None:
                        self.send_error(400, "Missing url, link, or guid parameter")
                        return
                    with ArticleStore(owner.config.database_path) as store:
                        article = store.get_article(lookup_key)
                    body = _article_text_body(article)
                    if body is None:
                        self.send_error(404, "Article text not found")
                        return
                    self._send_text(
                        body + "\n",
                        content_type="text/plain; charset=utf-8",
                    )
                    return
                path_category = _category_from_feed_path(parsed.path)
                if (
                    parsed.path in {"/", "/rss.xml", "/economist-fulltext.xml"}
                    or path_category
                ):
                    if not _authorized(
                        self.headers.get("Authorization", ""),
                        parsed.query,
                        "ECONOMIST_FEED_TOKEN",
                    ):
                        self.send_error(401)
                        return
                    try:
                        rss = _rss_response(
                            owner.config,
                            parsed.query,
                            path_category=path_category,
                            refresh_lock=owner.lock,
                        )
                    except ValueError as exc:
                        self.send_error(400, str(exc))
                        return
                    self._send_text(
                        rss,
                        content_type="application/rss+xml; charset=utf-8",
                    )
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/articles/fetch":
                    if not os.environ.get("ECONOMIST_REFRESH_TOKEN", ""):
                        self._send_json(
                            {
                                "error": (
                                    "full-text fetch requests are disabled because "
                                    "ECONOMIST_REFRESH_TOKEN is not configured"
                                )
                            },
                            status=503,
                        )
                        return
                    if not _required_bearer_authorized(
                        self.headers.get("Authorization", ""),
                        "ECONOMIST_REFRESH_TOKEN",
                    ):
                        self._send_json({"error": "unauthorized"}, status=401)
                        return
                    try:
                        request_body = self._read_json_body()
                        payload, status = _api_article_fetch_response(
                            owner.config,
                            request_body,
                        )
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=400)
                        return
                    self._send_json(payload, status=status)
                    return
                if parsed.path != "/refresh":
                    self.send_error(404)
                    return
                token = os.environ.get("ECONOMIST_REFRESH_TOKEN", "")
                if token:
                    auth = self.headers.get("Authorization", "")
                    if not _authorized(auth, parsed.query, "ECONOMIST_REFRESH_TOKEN"):
                        self.send_error(401)
                        return
                with owner.lock:
                    summary = refresh_if_stale(owner.config, force=True)
                self._send_text(
                    (
                        "{"
                        f'"status":"{summary.status}",'
                        f'"feeds_checked":{summary.feeds_checked},'
                        f'"feed_items_seen":{summary.feed_items_seen},'
                        f'"articles_fetched":{summary.articles_fetched},'
                        f'"articles_failed":{summary.articles_failed}'
                        "}\n"
                    ),
                    content_type="application/json",
                )

            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def _send_text(self, body: str, *, content_type: str) -> None:
                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_json(self, payload: object, *, status: int) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "\n"
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _read_json_body(self) -> dict[str, object]:
                content_type = self.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip() != "application/json":
                    raise ValueError("Content-Type must be application/json")
                raw_length = self.headers.get("Content-Length", "")
                try:
                    content_length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("Content-Length must be an integer") from exc
                if content_length < 1:
                    raise ValueError("request body must be a JSON object")
                if content_length > MAX_JSON_BODY_BYTES:
                    raise ValueError("request body is too large")
                try:
                    payload = json.loads(
                        self.rfile.read(content_length).decode("utf-8")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("request body must be valid JSON") from exc
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                return payload

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.serve_forever()


def _rss_response(
    config: AppConfig,
    query: str,
    *,
    path_category: str | None = None,
    refresh_lock: Lock | None = None,
) -> str:
    requested_limit = _rss_item_limit(query, config.rss_item_limit)
    catalog_search = _catalog_search_query(query)
    category_filters = _category_filters(query)
    if path_category:
        category_filters = _unique_casefolded([path_category, *category_filters])

    if not catalog_search.active:
        if refresh_lock is None:
            refresh_if_stale(config)
        else:
            with refresh_lock:
                refresh_if_stale(config)

    with ArticleStore(config.database_path) as store:
        if catalog_search.active:
            feed_items = store.search_items(
                query=catalog_search.query,
                start_date=catalog_search.start_date,
                end_date=catalog_search.end_date,
                categories=category_filters,
                limit=requested_limit,
            )
        else:
            item_limit = None if category_filters else requested_limit
            feed_items = store.feed_items(
                limit=item_limit,
                published_after=cutoff_datetime(config.article_lookback_days),
                current_issue_only=config.current_issue_filter_enabled,
            )
            if category_filters:
                feed_items = _filter_items_by_category(feed_items, category_filters)
                if requested_limit is not None:
                    feed_items = feed_items[:requested_limit]
        return build_rss(
            feed_items,
            title=_rss_title(category_filters, catalog_search),
            description=_rss_description(category_filters, catalog_search),
        )


def _api_search_response(config: AppConfig, query: str) -> dict[str, object]:
    catalog_search = _catalog_search_query(query)
    categories = _category_filters(query)
    limit = _api_integer_parameter(
        query,
        "limit",
        default=DEFAULT_API_ITEM_LIMIT,
        minimum=1,
        maximum=MAX_API_ITEM_LIMIT,
    )
    scope = _api_search_scope(query)

    with ArticleStore(config.database_path) as store:
        local_results: list[StoredArticle] = []
        feed_results: list[StoredArticle] = []
        if scope in {"all", "local"}:
            local_results = store.catalog_articles(
                query=catalog_search.query,
                start_date=catalog_search.start_date,
                end_date=catalog_search.end_date,
                categories=categories,
                limit=limit,
                search_content=True,
                full_text_only=True,
            )
        if scope == "feed":
            feed_results = store.catalog_articles(
                query=catalog_search.query,
                start_date=catalog_search.start_date,
                end_date=catalog_search.end_date,
                categories=categories,
                limit=limit,
                search_content=False,
            )
        elif scope == "all" and len(local_results) < limit:
            local_urls = {article.canonical_url for article in local_results}
            feed_candidates = store.catalog_articles(
                query=catalog_search.query,
                start_date=catalog_search.start_date,
                end_date=catalog_search.end_date,
                categories=categories,
                limit=limit + len(local_results) + 50,
                search_content=False,
            )
            feed_results = [
                article
                for article in feed_candidates
                if article.canonical_url not in local_urls
            ][: max(0, limit - len(local_results))]

        results = [
            *(
                _article_api_item(article, match_source="local_full_text")
                for article in local_results
            ),
            *(
                _article_api_item(article, match_source="economist_feed_metadata")
                for article in feed_results
            ),
        ][:limit]
        last_refresh_at = store.get_state("last_refresh_at")

    return {
        "query": _api_query_payload(
            catalog_search,
            categories=categories,
            limit=limit,
            scope=scope,
        ),
        "ordering": (
            "local full-text matches first, then feed metadata matches; "
            "newest first within each group"
        ),
        "count": len(results),
        "local_count": min(len(local_results), len(results)),
        "feed_count": len(results) - min(len(local_results), len(results)),
        "results": results,
        "cache_last_refreshed_at": last_refresh_at,
    }


def _api_articles_response(config: AppConfig, query: str) -> dict[str, object]:
    catalog_search = _catalog_search_query(query)
    categories = _category_filters(query)
    limit = _api_integer_parameter(
        query,
        "limit",
        default=DEFAULT_API_ITEM_LIMIT,
        minimum=1,
        maximum=MAX_API_ITEM_LIMIT,
    )
    offset = _api_integer_parameter(
        query,
        "offset",
        default=0,
        minimum=0,
        maximum=MAX_API_OFFSET,
    )
    with ArticleStore(config.database_path) as store:
        articles = store.catalog_articles(
            query=catalog_search.query,
            start_date=catalog_search.start_date,
            end_date=catalog_search.end_date,
            categories=categories,
            limit=limit + 1,
            offset=offset,
            search_content=False,
        )
        last_refresh_at = store.get_state("last_refresh_at")

    has_more = len(articles) > limit
    page = articles[:limit]
    return {
        "query": _api_query_payload(
            catalog_search,
            categories=categories,
            limit=limit,
            offset=offset,
        ),
        "ordering": "published_at descending",
        "count": len(page),
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "results": [
            _article_api_item(article, match_source="economist_feed_metadata")
            for article in page
        ],
        "cache_last_refreshed_at": last_refresh_at,
    }


def _api_article_status_response(
    config: AppConfig,
    query: str,
) -> tuple[dict[str, object], int]:
    lookup_key = _article_lookup_key(query)
    if lookup_key is None:
        raise ValueError("Missing url, link, or guid parameter")
    with ArticleStore(config.database_path) as store:
        article = store.get_article(lookup_key)
    if article is None:
        return {"error": "article not found"}, 404
    return {"article": _article_status_api_item(article)}, 200


def _api_article_fetch_response(
    config: AppConfig,
    request_body: dict[str, object],
) -> tuple[dict[str, object], int]:
    lookup_key = _article_lookup_from_json(request_body)
    with ArticleStore(config.database_path) as store:
        article = store.get_article(lookup_key)
        if article is None:
            return {"error": "article not found in the local catalog"}, 404
        if not _is_allowed_economist_article(article):
            return {"error": "article URL is not an allowed Economist URL"}, 400
        if any(
            pattern and pattern in article.url
            for pattern in config.exclude_url_patterns
        ):
            return {"error": "article URL is excluded from fetching"}, 409
        if _article_has_full_text(article):
            return {
                "status": "ready",
                "article": _article_status_api_item(article),
            }, 200
        queued = store.request_article_fetch(article.canonical_url)

    assert queued is not None
    return {
        "status": "queued",
        "message": (
            "The article will be attempted by the next sequential refresh run "
            "when retry backoff permits."
        ),
        "refresh_interval_seconds": config.refresh_interval_seconds,
        "article": _article_status_api_item(queued),
    }, 202


def _api_query_payload(
    catalog_search: CatalogSearchQuery,
    *,
    categories: list[str],
    limit: int,
    scope: str | None = None,
    offset: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "q": catalog_search.query,
        "start_date": (
            catalog_search.start_date.isoformat()
            if catalog_search.start_date
            else None
        ),
        "end_date": (
            catalog_search.end_date.isoformat() if catalog_search.end_date else None
        ),
        "categories": categories,
        "limit": limit,
    }
    if scope is not None:
        payload["scope"] = scope
    if offset is not None:
        payload["offset"] = offset
    return payload


def _article_api_item(
    article: StoredArticle,
    *,
    match_source: str,
) -> dict[str, object]:
    full_text_available = _article_has_full_text(article)
    item: dict[str, object] = {
        "title": article.title,
        "url": article.url,
        "guid": article.guid,
        "published": article.published,
        "published_at": article.published_at,
        "categories": article.categories,
        "snippet": _article_snippet(article),
        "source": article.source,
        "match_source": match_source,
        "full_text_available": full_text_available,
        "content_status": article.content_status or "not_fetched",
        "fetch_requested": bool(article.fetch_requested_at),
        "status_url": _relative_article_url("/api/articles/status", article),
        "full_text_url": (
            _relative_article_url("/article.txt", article)
            if full_text_available
            else None
        ),
    }
    return item


def _article_status_api_item(article: StoredArticle) -> dict[str, object]:
    item = _article_api_item(article, match_source="local_catalog")
    item.update(
        {
            "fetch_requested_at": article.fetch_requested_at,
            "fetch_request_count": article.fetch_request_count,
            "fetched_at": article.fetched_at,
            "last_attempt_at": article.last_attempt_at,
            "attempt_count": article.attempt_count,
            "error": article.error or None,
        }
    )
    return item


def _article_snippet(article: StoredArticle) -> str:
    raw = (article.summary or "").strip()
    if not raw and article.content_text:
        raw = article.content_text.strip()
    parser = _PlainTextParser()
    parser.feed(raw)
    parser.close()
    normalized = " ".join(" ".join(parser.parts).split())
    if len(normalized) <= 320:
        return normalized
    return normalized[:317].rstrip() + "..."


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def _relative_article_url(path: str, article: StoredArticle) -> str:
    return f"{path}?{urlencode({'url': article.canonical_url})}"


def _article_lookup_from_json(payload: dict[str, object]) -> str:
    for key in ("url", "link", "guid"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("request body needs a non-empty url, link, or guid")


def _article_has_full_text(article: StoredArticle) -> bool:
    return article.content_status == "ok" and bool(
        (article.content_text or "").strip()
    )


def _is_allowed_economist_article(article: StoredArticle) -> bool:
    parsed = urlparse(article.url)
    hostname = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and (
        hostname == "economist.com" or hostname.endswith(".economist.com")
    )


def _api_search_scope(query: str) -> str:
    parsed = parse_qs(query)
    scope = parsed.get("scope", ["all"])[-1].strip().casefold() or "all"
    if scope == "rss":
        scope = "feed"
    if scope not in {"all", "local", "feed"}:
        raise ValueError("scope must be all, local, feed, or rss")
    return scope


def _api_integer_parameter(
    query: str,
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    parsed = parse_qs(query)
    raw_value = parsed.get(name, [""])[-1].strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _authorized(authorization_header: str, query: str, token_env_key: str) -> bool:
    expected = os.environ.get(token_env_key, "")
    if not expected:
        return True
    if authorization_header == f"Bearer {expected}":
        return True
    tokens = parse_qs(query).get("token", [])
    return any(token == expected for token in tokens)


def _required_bearer_authorized(
    authorization_header: str,
    token_env_key: str,
) -> bool:
    expected = os.environ.get(token_env_key, "")
    if not expected:
        return False
    supplied = authorization_header.removeprefix("Bearer ")
    if supplied == authorization_header:
        return False
    return hmac.compare_digest(supplied, expected)


def _category_from_feed_path(path: str) -> str | None:
    if not path.startswith(CATEGORY_FEED_PREFIX):
        return None
    if not path.endswith(CATEGORY_FEED_SUFFIX):
        return None
    slug = path[len(CATEGORY_FEED_PREFIX) : -len(CATEGORY_FEED_SUFFIX)]
    if not slug:
        return None
    return category_for_slug(unquote(slug))


def _category_filters(query: str) -> list[str]:
    parsed = parse_qs(query)
    raw_values = [*parsed.get("category", []), *parsed.get("categories", [])]
    values: list[str] = []
    for raw_value in raw_values:
        values.extend(part.strip() for part in raw_value.split(","))
    return _unique_casefolded(values)


def _rss_item_limit(query: str, default_limit: int | None) -> int | None:
    parsed = parse_qs(query)
    raw_values = [*parsed.get("limit", []), *parsed.get("count", [])]
    effective_default = min(default_limit or MAX_RSS_ITEM_LIMIT, MAX_RSS_ITEM_LIMIT)
    if not raw_values:
        return effective_default
    raw_value = raw_values[-1].strip()
    if not raw_value:
        return effective_default
    try:
        requested_limit = int(raw_value)
    except ValueError as exc:
        raise ValueError("limit must be a positive integer") from exc
    if requested_limit < 1:
        raise ValueError("limit must be a positive integer")
    return min(requested_limit, effective_default)


def _catalog_search_query(query: str) -> CatalogSearchQuery:
    parsed = parse_qs(query)
    raw_query = parsed.get("q", [""])[-1].strip()
    if len(raw_query) > 200:
        raise ValueError("q must be 200 characters or fewer")
    start_date = _date_parameter(parsed, "start_date")
    end_date = _date_parameter(parsed, "end_date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    return CatalogSearchQuery(
        query=raw_query,
        start_date=start_date,
        end_date=end_date,
    )


def _date_parameter(parsed: dict[str, list[str]], name: str) -> date | None:
    raw_value = parsed.get(name, [""])[-1].strip()
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def _article_lookup_key(query: str) -> str | None:
    parsed = parse_qs(query)
    for parameter in ("url", "link", "guid"):
        for value in parsed.get(parameter, []):
            lookup_key = value.strip()
            if lookup_key:
                return lookup_key
    return None


def _article_text_body(article: StoredArticle | None) -> str | None:
    if article is None:
        return None
    if article.content_status != "ok":
        return None
    if article.content_text is None:
        return None
    text = article.content_text.strip()
    return text or None


def _filter_items_by_category(
    items: list[FeedItem],
    category_filters: list[str],
) -> list[FeedItem]:
    if not category_filters:
        return items
    wanted = {category.casefold() for category in category_filters}
    return [
        item
        for item in items
        if wanted.intersection(
            category.casefold() for category in categories_for_item(item)
        )
    ]


def _unique_casefolded(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _rss_title(
    category_filters: list[str],
    catalog_search: CatalogSearchQuery | None = None,
) -> str:
    base_title = "The Economist private article feed"
    if catalog_search and catalog_search.active:
        terms = catalog_search.query or "Back catalog"
        return f"{base_title} - Search: {terms}"
    if not category_filters:
        return base_title
    return f"{base_title} - {', '.join(category_filters)}"


def _rss_description(
    category_filters: list[str],
    catalog_search: CatalogSearchQuery | None = None,
) -> str:
    if catalog_search and catalog_search.active:
        filters = []
        if catalog_search.query:
            filters.append(f"keywords: {catalog_search.query}")
        if catalog_search.start_date:
            filters.append(f"from: {catalog_search.start_date.isoformat()}")
        if catalog_search.end_date:
            filters.append(f"through: {catalog_search.end_date.isoformat()}")
        if category_filters:
            filters.append(f"categories: {', '.join(category_filters)}")
        return (
            "Private RSS back-catalog search generated from the local article "
            f"index ({'; '.join(filters)})."
        )
    if not category_filters:
        return "Private RSS article index generated from authorized article fetches."
    return (
        "Private RSS article index generated from authorized article fetches, "
        f"filtered to: {', '.join(category_filters)}."
    )
