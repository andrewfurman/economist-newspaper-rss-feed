from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

from .config import AppConfig
from .feed import build_rss
from .refresh import refresh_if_stale
from .store import ArticleStore
from .util import cutoff_datetime


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
                if parsed.path in {"/", "/rss.xml", "/economist-fulltext.xml"}:
                    if not _authorized(self.headers.get("Authorization", ""), parsed.query, "ECONOMIST_FEED_TOKEN"):
                        self.send_error(401)
                        return
                    with owner.lock:
                        refresh_if_stale(owner.config)
                    with ArticleStore(owner.config.database_path) as store:
                        rss = build_rss(
                            store.feed_items(
                                limit=owner.config.rss_item_limit,
                                published_after=cutoff_datetime(
                                    owner.config.article_lookback_days
                                )
                            )
                        )
                    self._send_text(rss, content_type="application/rss+xml; charset=utf-8")
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
