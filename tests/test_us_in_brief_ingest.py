import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from economist_rss.config import AppConfig
from economist_rss.feed import categories_for_item, category_for_slug
from economist_rss.refresh import (
    ingest_us_in_brief_html,
    ingest_world_in_brief_html,
    us_in_brief_last_push,
)
from economist_rss.server import _category_from_feed_path, _us_in_brief_ingest_response
from economist_rss.store import ArticleStore

from test_world_in_brief_ingest import WIB_URL, wib_page

US_URL = (
    "https://www.economist.com/united-states/2026/10/12/"
    "a-backlash-against-trumps-diesel-deal"
)
STORY = (
    "Republican governors in farm states are complaining about the administration's deal "
    "on diesel fuel, which they say will raise costs for growers ahead of the harvest. "
)


def us_page(title="The US in Brief: A backlash against Trump's diesel deal", paragraphs=8):
    body = "".join(f"<p>{STORY}Item {n}.</p>" for n in range(paragraphs))
    return (
        f"<html><head><title>{title}</title></head><body><main><article><h1>{title}</h1>"
        '<p><time datetime="2026-10-12T20:00:00.000Z">Oct 12th 2026</time></p>'
        f"{body}</article></main></body></html>"
    )


class UsInBriefIngestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = AppConfig(database_path=str(Path(self.directory.name) / "a.sqlite3"))

    def tearDown(self):
        self.directory.cleanup()

    def test_saves_page_with_brief_categories(self):
        result = ingest_us_in_brief_html(self.config, url=US_URL, html=us_page(), source_host="mac1")
        self.assertTrue(result.ok, result.message)
        with ArticleStore(self.config.database_path) as store:
            article = store.get_article(US_URL)
            items = store.feed_items(limit=10)
        self.assertEqual(article.content_source, "economist_us_in_brief_push")
        self.assertEqual([i.link for i in items], [US_URL])
        categories = categories_for_item(items[0])
        for expected in ("In Brief", "United States", "The US in Brief"):
            self.assertIn(expected, categories)
        self.assertEqual(us_in_brief_last_push(self.config)["source_host"], "mac1")

    def test_title_without_prefix_is_labelled_when_page_says_us_in_brief(self):
        html = us_page(title="A backlash against Trump's diesel deal")
        url = "https://www.economist.com/the-us-in-brief/2026/10/12/a-backlash"
        result = ingest_us_in_brief_html(self.config, url=url, html=html)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.title, "The US in Brief: A backlash against Trump's diesel deal")

    def test_rejections(self):
        cases = {
            "bad_url": ("https://evil.example/united-states/2026/10/12/x", us_page()),
            "bad_url_wib": (WIB_URL, us_page()),
            "cloudflare_challenge": (US_URL, "<title>Just a moment...</title>"),
            "excerpt_or_login_required": (US_URL, us_page(paragraphs=1)),
            "not_us_in_brief": (US_URL, us_page(title="Why the Fed is wrong about tariffs")),
        }
        for name, (url, html) in cases.items():
            with self.subTest(name):
                result = ingest_us_in_brief_html(self.config, url=url, html=html)
                self.assertFalse(result.ok)
                self.assertEqual(result.status, name.removesuffix("_wib"))
        paywall = us_page().replace(STORY, "Subscribe to read the full story. Log in. ")
        self.assertFalse(ingest_us_in_brief_html(self.config, url=US_URL, html=paywall).ok)
        self.assertIsNone(us_in_brief_last_push(self.config))
        with ArticleStore(self.config.database_path) as store:
            self.assertEqual(store.feed_items(limit=10), [])

    def test_only_latest_us_in_brief_appears_alongside_world_in_brief(self):
        old = US_URL.replace("2026/10/12", "2026/10/11")
        old_html = us_page("The US in Brief: Older news").replace("2026-10-12", "2026-10-11")
        ingest_us_in_brief_html(self.config, url=old, html=old_html)
        ingest_us_in_brief_html(self.config, url=US_URL, html=us_page())
        ingest_world_in_brief_html(self.config, url=WIB_URL, html=wib_page())
        with ArticleStore(self.config.database_path) as store:
            items = store.feed_items(limit=10)
        self.assertEqual({i.link for i in items}, {US_URL, WIB_URL})

    def test_category_feed_path(self):
        self.assertEqual(category_for_slug("the-us-in-brief"), "The US in Brief")
        self.assertEqual(
            _category_from_feed_path("/rss/category/the-us-in-brief.xml"), "The US in Brief"
        )

    def test_response_status_codes(self):
        payload, status = _us_in_brief_ingest_response(self.config, {"url": US_URL, "html": us_page()})
        self.assertEqual((status, payload["status"]), (200, "ok"))
        self.assertEqual(_us_in_brief_ingest_response(self.config, {"url": US_URL})[1], 400)
        payload, status = _us_in_brief_ingest_response(
            self.config, {"url": US_URL, "html": "<p>Log in to read</p>"}
        )
        self.assertEqual((status, payload["status"]), (422, "excerpt_or_login_required"))


def load_mac_script():
    path = Path(__file__).resolve().parents[1] / "mac" / "wib_push.py"
    spec = importlib.util.spec_from_file_location("wib_push", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacScriptTests(unittest.TestCase):
    def setUp(self):
        self.mac = load_mac_script()

    def test_only_https_economist_links_are_followed(self):
        self.assertEqual(self.mac.us_in_brief_url(US_URL), US_URL)
        for bad in (None, "", "http://www.economist.com/x", "https://evil.example/x", "javascript:1"):
            self.assertIsNone(self.mac.us_in_brief_url(bad))

    def test_push_loads_one_page_and_posts_to_us_endpoint(self):
        loads = []
        calls = []

        class FakeBrowser:
            def load(self, url, ready, timeout=120):
                loads.append(url)
                return url, "<html>page</html>"

        def fake_api(url, token, payload=None):
            calls.append((url, payload))
            return 200, {"status": "ok"}

        with patch.object(self.mac, "api", fake_api), patch.object(self.mac.time, "sleep", lambda s: None):
            ok = self.mac.push_us_in_brief(
                FakeBrowser(), US_URL, "https://feeds.example/api/world-in-brief/ingest", "t"
            )
        self.assertTrue(ok)
        self.assertEqual(loads, [US_URL])
        self.assertEqual(calls[0][0], "https://feeds.example/api/us-in-brief/ingest")
        self.assertEqual(calls[0][1]["url"], US_URL)

    def test_challenge_or_missing_link_is_skipped_without_posting(self):
        class StuckBrowser:
            def load(self, url, ready, timeout=120):
                raise RuntimeError("did not load (challenge)")

        with patch.object(self.mac, "api", side_effect=AssertionError("must not post")), \
                patch.object(self.mac.time, "sleep", lambda s: None):
            self.assertFalse(self.mac.push_us_in_brief(StuckBrowser(), US_URL, "https://h/api/x", "t"))
            self.assertFalse(self.mac.push_us_in_brief(StuckBrowser(), None, "https://h/api/x", "t"))

    def test_in_brief_feed_is_pushed(self):
        self.assertIn("https://www.economist.com/in-brief/rss.xml", self.mac.FEEDS)


if __name__ == "__main__":
    unittest.main()
