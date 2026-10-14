import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from economist_rss.config import AppConfig, FeedConfig
from economist_rss.refresh import (
    ingest_article_html,
    ingest_feed_xml,
    wanted_article_urls,
)
from economist_rss.server import (
    EconomistRssServer,
    _article_ingest_response,
    _feeds_ingest_response,
)
from economist_rss.store import ArticleStore


FEED_URL = "https://www.economist.com/leaders/rss.xml"
ART_1 = "https://www.economist.com/leaders/2026/10/13/the-first-leader"
ART_2 = "https://www.economist.com/leaders/2026/10/12/the-second-leader"
WIB = (
    "https://www.economist.com/the-world-in-brief/2026/10/13/"
    "b498cfce-4726-4419-a615-17d1c1c51a03"
)
STORY = (
    "American bank earnings were mixed. Goldman Sachs's quarterly net income of $5.3bn "
    "was nearly a third higher than a year ago, thanks in part to big profits from trading. "
)


def rss(*items: tuple[str, str, str]) -> str:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link><guid isPermaLink=\"false\">{link}</guid>"
        f"<pubDate>{pub}</pubDate><description>Teaser for {title}</description></item>"
        for title, link, pub in items
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>Leaders</title>'
        f"<link>https://www.economist.com/leaders</link>{body}</channel></rss>"
    )


def fresh_pub(days_ago: int = 0) -> str:
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    return format_datetime(datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1))


def article_page(paragraphs: int = 9) -> str:
    body = "".join(f"<p>{STORY}Story {n}.</p>" for n in range(paragraphs))
    return (
        "<html><head><title>A leader</title></head><body><main><article>"
        f"<h1>A leader</h1>{body}</article></main></body></html>"
    )


class FeedIngestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = AppConfig(
            database_path=str(Path(self.directory.name) / "a.sqlite3"),
            feeds=[FeedConfig(name="Leaders", url=FEED_URL)],
            refresh_enabled=False,
            max_articles_per_refresh=5,
        )
        self.xml = rss(
            ("The first leader", ART_1, fresh_pub(0)),
            ("The second leader", ART_2, fresh_pub(1)),
            ("World in brief", WIB, fresh_pub(0)),
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_feed_ingest_saves_items_and_returns_wanted_without_world_in_brief(self):
        results, wanted = ingest_feed_xml(
            self.config, feeds=[(FEED_URL, self.xml)], source_host="mac1"
        )

        self.assertEqual([(r.status, r.items) for r in results], [("ok", 3)])
        with ArticleStore(self.config.database_path) as store:
            self.assertEqual(store.get_article(ART_1).title, "The first leader")
            self.assertIsNotNone(store.get_article(WIB))
            self.assertIsNotNone(store.get_state("feeds_last_push"))
        self.assertEqual(set(wanted), {ART_1, ART_2})
        self.assertNotIn(WIB, wanted)

    def test_max_articles_caps_wanted_and_hard_max_is_ten(self):
        items = [(f"Leader {n}", f"{ART_1}-{n}", fresh_pub(0)) for n in range(15)]
        _, wanted = ingest_feed_xml(
            self.config, feeds=[(FEED_URL, rss(*items))], max_articles=2
        )
        self.assertEqual(len(wanted), 2)
        _, wanted = ingest_feed_xml(
            self.config, feeds=[(FEED_URL, rss(*items))], max_articles=99
        )
        self.assertEqual(len(wanted), 10)

    def test_unknown_feed_challenge_and_non_rss_are_rejected_per_entry(self):
        results, wanted = ingest_feed_xml(
            self.config,
            feeds=[
                ("https://evil.example/rss.xml", self.xml),
                (FEED_URL, "<title>Just a moment...</title>"),
                (FEED_URL, "<html><body>not rss"),
            ],
        )
        self.assertEqual(
            [r.status for r in results], ["unknown_feed", "cloudflare_challenge", "bad_feed"]
        )
        self.assertEqual(wanted, [])
        with ArticleStore(self.config.database_path) as store:
            self.assertIsNone(store.get_article(ART_1))
            self.assertIsNone(store.get_state("feeds_last_push"))

    def test_feed_response_shape_and_status_codes(self):
        payload, status = _feeds_ingest_response(
            self.config,
            {"feeds": [{"url": FEED_URL, "xml": self.xml}], "source_host": "m", "max_articles": 1},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["feeds"], [{"url": FEED_URL, "items": 3, "status": "ok"}])
        self.assertEqual(len(payload["wanted"]), 1)

        payload, status = _feeds_ingest_response(
            self.config, {"feeds": [{"url": "https://x.example/", "xml": self.xml}]}
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["feeds"][0]["status"], "unknown_feed")

        _, status = _feeds_ingest_response(self.config, {"feeds": "nope"})
        self.assertEqual(status, 400)

    def test_article_ingest_saves_full_text_with_push_source(self):
        ingest_feed_xml(self.config, feeds=[(FEED_URL, self.xml)])

        result = ingest_article_html(
            self.config, url=ART_1, html=article_page(), source_host="mac1"
        )

        self.assertTrue(result.ok, result.message)
        self.assertGreater(result.chars, 700)
        with ArticleStore(self.config.database_path) as store:
            article = store.get_article(ART_1)
            last = json.loads(store.get_state("articles_last_push"))
            self.assertEqual(article.content_source, "economist_mac_push")
            self.assertEqual(article.content_status, "ok")
            self.assertIn("Goldman Sachs", article.content_text)
            self.assertEqual(wanted_article_urls(store, self.config), [ART_2])
        self.assertEqual(last["url"], ART_1)
        self.assertEqual(last["source_host"], "mac1")

    def test_article_ingest_of_unknown_item_creates_it(self):
        result = ingest_article_html(self.config, url=ART_1, html=article_page())
        self.assertTrue(result.ok, result.message)
        with ArticleStore(self.config.database_path) as store:
            self.assertEqual(store.get_article(ART_1).content_source, "economist_mac_push")

    def test_short_excerpt_is_422_and_failure_keeps_it_out_of_wanted(self):
        ingest_feed_xml(self.config, feeds=[(FEED_URL, self.xml)])

        payload, status = _article_ingest_response(
            self.config, {"url": ART_1, "html": article_page(paragraphs=1)}
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["status"], "excerpt_or_login_required")
        with ArticleStore(self.config.database_path) as store:
            article = store.get_article(ART_1)
            self.assertEqual(article.content_status, "excerpt_or_login_required")
            self.assertEqual(article.attempt_count, 1)
            self.assertEqual(wanted_article_urls(store, self.config), [ART_2])

    def test_challenge_and_bad_host_rejected(self):
        payload, status = _article_ingest_response(
            self.config, {"url": ART_1, "html": "<title>Just a moment...</title>"}
        )
        self.assertEqual((status, payload["status"]), (422, "cloudflare_challenge"))
        for url in (
            "https://evil.example/leaders/2026/10/13/x",
            "http://www.economist.com/leaders/2026/10/13/x",
        ):
            payload, status = _article_ingest_response(
                self.config, {"url": url, "html": article_page()}
            )
            self.assertEqual((status, payload["status"]), (422, "bad_url"))
        payload, status = _article_ingest_response(self.config, {"url": ART_1})
        self.assertEqual(status, 400)

    def test_success_response_shape(self):
        payload, status = _article_ingest_response(
            self.config, {"url": ART_1, "html": article_page(), "source_host": "m"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["url"], ART_1)
        self.assertEqual(payload["title"], "A leader")
        self.assertIsInstance(payload["chars"], int)


class FeedIngestHttpTests(unittest.TestCase):
    TOKEN = "secret-token"

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        config = AppConfig(
            database_path=str(Path(cls.directory.name) / "a.sqlite3"),
            feeds=[FeedConfig(name="Leaders", url=FEED_URL)],
            refresh_enabled=False,
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        server = EconomistRssServer(config, host="127.0.0.1", port=port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        for _ in range(50):
            try:
                urlopen(cls.base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def request(self, method, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request(self.base + path, data=data, headers=headers, method=method)) as r:
                return r.status, json.loads(r.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_bearer_auth_required_and_endpoints_work(self):
        body = {"feeds": [{"url": FEED_URL, "xml": rss(("L", ART_1, fresh_pub(0)))}]}
        with patch.dict(os.environ, {"ECONOMIST_INGEST_TOKEN": self.TOKEN}):
            self.assertEqual(self.request("POST", "/api/feeds/ingest", body)[0], 401)
            self.assertEqual(
                self.request("POST", "/api/feeds/ingest", body, token="wrong")[0], 401
            )
            self.assertEqual(
                self.request("POST", "/api/articles/ingest", {"url": ART_1, "html": "x"})[0], 401
            )
            self.assertEqual(self.request("GET", "/api/feeds/wanted")[0], 401)

            status, payload = self.request("POST", "/api/feeds/ingest", body, token=self.TOKEN)
            self.assertEqual(status, 200)
            self.assertEqual(payload["wanted"], [ART_1])
            status, payload = self.request("GET", "/api/feeds/wanted?limit=1", token=self.TOKEN)
            self.assertEqual((status, payload), (200, {"wanted": [ART_1]}))
            status, payload = self.request(
                "POST",
                "/api/articles/ingest",
                {"url": ART_1, "html": article_page()},
                token=self.TOKEN,
            )
            self.assertEqual((status, payload["status"]), (200, "ok"))
            status, payload = self.request("GET", "/api/feeds/wanted", token=self.TOKEN)
            self.assertEqual(payload, {"wanted": []})


if __name__ == "__main__":
    unittest.main()
