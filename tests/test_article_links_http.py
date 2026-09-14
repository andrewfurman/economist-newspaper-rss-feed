from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from economist_rss.config import AppConfig
from economist_rss.feed import FeedItem
from economist_rss.server import EconomistRssServer
from economist_rss.store import ArticleStore


class ArticleLinksHttpTests(unittest.TestCase):
    """Exercise feed generation and article authorization through real HTTP."""

    feed_token = "private-feed-token-for-http-tests"
    article_url = (
        "https://www.economist.com/business/2026/09/14/cached-story"
        "?edition=Europe%20%26%20Asia&tag=a%2Bb%3Dc&currency=%C2%A3"
    )
    missing_url = "https://www.economist.com/business/2026/09/14/missing-story"
    article_text = "First paragraph with £5 & café.\n\nSecond paragraph."

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database_path = Path(directory.name) / "articles.sqlite3"
        with ArticleStore(database_path) as store:
            article = store.upsert_feed_item(
                FeedItem(
                    title="Cached business story",
                    link=self.article_url,
                    guid="cached-story",
                    summary="A short preview",
                    categories=["Business"],
                )
            )
            store.save_article_content(
                article,
                content_html="<p>First paragraph with £5 &amp; café.</p>",
                content_text=self.article_text,
                content_source="test",
            )
            store.upsert_feed_item(
                FeedItem(
                    title="Missing business story",
                    link=self.missing_url,
                    guid="missing-story",
                    summary="Only metadata has been cached",
                    categories=["Business"],
                )
            )

        environment = patch.dict(
            os.environ,
            {
                "ECONOMIST_FEED_TOKEN": self.feed_token,
                "ECONOMIST_REFRESH_TOKEN": "private-refresh-token-for-http-tests",
                "ECONOMIST_PUBLIC_BASE_URL": "",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        refresh = patch("economist_rss.server.refresh_if_stale")
        refresh.start()
        self.addCleanup(refresh.stop)

        created = Event()
        servers = []
        errors = []

        def create_server(address, handler):
            server = ThreadingHTTPServer(address, handler)
            servers.append(server)
            created.set()
            return server

        server_factory = patch(
            "economist_rss.server.ThreadingHTTPServer", side_effect=create_server
        )
        server_factory.start()
        self.addCleanup(server_factory.stop)
        owner = EconomistRssServer(
            AppConfig(
                database_path=str(database_path),
                article_lookback_days=None,
                current_issue_filter_enabled=False,
            ),
            host="127.0.0.1",
            port=0,
        )

        def serve():
            try:
                owner.serve_forever()
            except Exception as exc:
                errors.append(exc)
                created.set()

        self.server_thread = Thread(target=serve, daemon=True)
        self.server_thread.start()
        self.assertTrue(created.wait(timeout=5), "HTTP server did not start")
        self.assertEqual(errors, [])
        self.httpd = servers[0]
        self.addCleanup(self._stop_server)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=5)
        self.assertFalse(self.server_thread.is_alive(), "HTTP server did not stop")

    def _request(self, target, *, headers=None, method="GET"):
        # Public-origin links are routed to this test server while keeping their
        # complete encoded path/query, so no external service is contacted.
        parsed = urlsplit(target)
        path = urlunsplit(("", "", parsed.path, parsed.query, ""))
        connection = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return (
                response.status,
                response.getheader("Content-Type", ""),
                response.read().decode("utf-8"),
            )
        finally:
            connection.close()

    def _feed_items(self, path="/rss.xml", *, headers=None):
        request_headers = {"Authorization": f"Bearer {self.feed_token}"}
        request_headers.update(headers or {})
        status, content_type, body = self._request(path, headers=request_headers)
        self.assertEqual(status, 200, body)
        self.assertIn("application/rss+xml", content_type)
        self.assertNotIn(self.feed_token, body)
        return {
            item.findtext("guid"): item
            for item in ET.fromstring(body).findall("./channel/item")
        }

    def _cached_link(self, path="/rss.xml", *, headers=None):
        link = self._feed_items(path, headers=headers)["cached-story"].findtext("link")
        self.assertTrue(link)
        return link

    def test_default_category_and_search_links_open_cached_text_without_headers(self):
        for path in (
            "/rss.xml",
            "/rss/category/business.xml",
            "/rss.xml?q=Cached",
        ):
            with self.subTest(path=path):
                link = self._cached_link(path)
                parsed = urlsplit(link)
                self.assertEqual(f"{parsed.scheme}://{parsed.netloc}", self.base_url)
                self.assertEqual(parsed.path, "/article.txt")
                parameters = parse_qs(parsed.query)
                self.assertEqual(parameters["url"], [self.article_url])
                self.assertTrue(parameters.get("key"))
                self.assertNotIn("token", parameters)
                status, content_type, body = self._request(link)
                self.assertEqual(status, 200, body)
                self.assertEqual(content_type, "text/plain; charset=utf-8")
                self.assertEqual(body, self.article_text + "\n")

    def test_query_authenticated_feed_produces_links_without_the_feed_token(self):
        path = "/rss.xml?" + urlencode({"token": self.feed_token})
        status, _, body = self._request(path)
        self.assertEqual(status, 200, body)
        self.assertNotIn(self.feed_token, body)
        link = ET.fromstring(body).findtext("./channel/item/link")
        self.assertTrue(link)
        self.assertEqual(self._request(link)[2], self.article_text + "\n")

    def test_key_is_invalid_for_a_different_article(self):
        key = parse_qs(urlsplit(self._cached_link()).query)["key"][0]
        for lookup in ({"url": self.missing_url}, {"guid": "missing-story"}):
            with self.subTest(lookup=lookup):
                status, _, _ = self._request(
                    "/article.txt?" + urlencode({**lookup, "key": key})
                )
                self.assertEqual(status, 401)

    def test_missing_or_tampered_key_is_rejected(self):
        for key in (None, "invalid-signature", "£invalid-signature"):
            with self.subTest(key=key):
                parameters = {"url": self.article_url}
                if key is not None:
                    parameters["key"] = key
                status, _, _ = self._request("/article.txt?" + urlencode(parameters))
                self.assertEqual(status, 401)

    def test_article_key_does_not_authorize_feed_api_or_refresh(self):
        key = parse_qs(urlsplit(self._cached_link()).query)["key"][0]
        query = urlencode({"url": self.article_url, "key": key})
        for method, path in (
            ("GET", "/rss.xml"),
            ("GET", "/rss/category/business.xml"),
            ("GET", "/api/search"),
            ("GET", "/api/articles"),
            ("GET", "/api/articles/status"),
            ("GET", "/api/stats"),
            ("POST", "/refresh"),
            ("POST", "/api/articles/fetch"),
        ):
            with self.subTest(method=method, path=path):
                status, _, _ = self._request(f"{path}?{query}", method=method)
                self.assertEqual(status, 401)

    def test_missing_cached_text_returns_404_from_its_signed_search_link(self):
        items = self._feed_items("/rss.xml?q=Missing")
        link = items["missing-story"].findtext("link")
        self.assertTrue(link)
        self.assertTrue(parse_qs(urlsplit(link).query).get("key"))
        status, _, _ = self._request(link)
        self.assertEqual(status, 404)

    def test_bartleby_can_wrap_generated_link_in_bearer_authenticated_lookup(self):
        link = self._cached_link()
        status, _, body = self._request(
            "/article.txt?" + urlencode({"url": link}),
            headers={"Authorization": f"Bearer {self.feed_token}"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body, self.article_text + "\n")

    def test_existing_bearer_and_query_article_auth_still_work(self):
        for parameters, headers in (
            ({"url": self.article_url}, {"Authorization": f"Bearer {self.feed_token}"}),
            ({"guid": "cached-story", "token": self.feed_token}, {}),
        ):
            with self.subTest(parameters=parameters):
                status, _, body = self._request(
                    "/article.txt?" + urlencode(parameters), headers=headers
                )
                self.assertEqual(status, 200, body)
                self.assertEqual(body, self.article_text + "\n")

    def test_auth_disabled_emits_unsigned_links_that_open(self):
        with patch.dict(os.environ, {"ECONOMIST_FEED_TOKEN": ""}):
            status, _, body = self._request("/rss.xml")
            self.assertEqual(status, 200, body)
            link = ET.fromstring(body).findtext("./channel/item/link")
            self.assertTrue(link)
            parameters = parse_qs(urlsplit(link).query)
            self.assertNotIn("key", parameters)
            self.assertNotIn("token", parameters)
            self.assertEqual(self._request(link)[2], self.article_text + "\n")

    def test_configured_https_origin_preserves_article_query_characters(self):
        with patch.dict(
            os.environ, {"ECONOMIST_PUBLIC_BASE_URL": "https://feed.example.test"}
        ):
            link = self._cached_link(headers={"Host": "internal.example.test"})
        parsed = urlsplit(link)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "feed.example.test")
        self.assertEqual(parse_qs(parsed.query)["url"], [self.article_url])
        self.assertEqual(self._request(link)[2], self.article_text + "\n")

    def test_forwarded_https_uses_public_host(self):
        link = self._cached_link(
            headers={"Host": "feed.example.test", "X-Forwarded-Proto": "https"}
        )
        parsed = urlsplit(link)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "feed.example.test")
        self.assertEqual(self._request(link)[2], self.article_text + "\n")

    def test_invalid_forwarded_scheme_cannot_be_used_for_article_links(self):
        status, _, _ = self._request(
            "/rss.xml",
            headers={
                "Authorization": f"Bearer {self.feed_token}",
                "X-Forwarded-Proto": "javascript",
            },
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
