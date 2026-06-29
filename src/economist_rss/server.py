from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from threading import Lock
from urllib.parse import parse_qs, unquote, urlparse

from .config import AppConfig
from .feed import FeedItem, build_rss, categories_for_item, category_for_slug
from .refresh import refresh_if_stale
from .store import ArticleStore, StoredArticle
from .util import cutoff_datetime

CATEGORY_FEED_PREFIX = "/rss/category/"
CATEGORY_FEED_SUFFIX = ".xml"


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
                    with owner.lock:
                        refresh_if_stale(owner.config)
                    with ArticleStore(owner.config.database_path) as store:
                        try:
                            requested_limit = _rss_item_limit(
                                parsed.query,
                                owner.config.rss_item_limit,
                            )
                        except ValueError as exc:
                            self.send_error(400, str(exc))
                            return
                        category_filters = _category_filters(parsed.query)
                        if path_category:
                            category_filters = _unique_casefolded(
                                [path_category, *category_filters]
                            )
                        item_limit = None if category_filters else requested_limit
                        feed_items = store.feed_items(
                            limit=item_limit,
                            published_after=cutoff_datetime(
                                owner.config.article_lookback_days
                            ),
                            current_issue_only=(
                                owner.config.current_issue_filter_enabled
                            ),
                        )
                        if category_filters:
                            feed_items = _filter_items_by_category(
                                feed_items,
                                category_filters,
                            )
                            if requested_limit is not None:
                                feed_items = feed_items[:requested_limit]
                        rss = build_rss(
                            feed_items,
                            title=_rss_title(category_filters),
                            description=_rss_description(category_filters),
                        )
                    self._send_text(
                        rss,
                        content_type="application/rss+xml; charset=utf-8",
                    )
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
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

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.serve_forever()


def _authorized(authorization_header: str, query: str, token_env_key: str) -> bool:
    expected = os.environ.get(token_env_key, "")
    if not expected:
        return True
    if authorization_header == f"Bearer {expected}":
        return True
    tokens = parse_qs(query).get("token", [])
    return any(token == expected for token in tokens)


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
    if not raw_values:
        return default_limit
    raw_value = raw_values[-1].strip()
    if not raw_value:
        return default_limit
    try:
        requested_limit = int(raw_value)
    except ValueError as exc:
        raise ValueError("limit must be a positive integer") from exc
    if requested_limit < 1:
        raise ValueError("limit must be a positive integer")
    if default_limit is not None:
        return min(requested_limit, default_limit)
    return requested_limit


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


def _rss_title(category_filters: list[str]) -> str:
    base_title = "The Economist private article feed"
    if not category_filters:
        return base_title
    return f"{base_title} - {', '.join(category_filters)}"


def _rss_description(category_filters: list[str]) -> str:
    if not category_filters:
        return "Private RSS article index generated from authorized article fetches."
    return (
        "Private RSS article index generated from authorized article fetches, "
        f"filtered to: {', '.join(category_filters)}."
    )
