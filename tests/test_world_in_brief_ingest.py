import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from economist_rss.config import AppConfig, load_config
from economist_rss.refresh import (
    ingest_world_in_brief_html,
    refresh_if_stale,
    world_in_brief_last_push,
)
from economist_rss.server import _world_in_brief_ingest_response
from economist_rss.store import ArticleStore


WIB_URL = (
    "https://www.economist.com/the-world-in-brief/2026/10/13/"
    "b498cfce-4726-4419-a615-17d1c1c51a03"
)
STORY = (
    "American bank earnings were mixed. Goldman Sachs's quarterly net income of $5.3bn "
    "was nearly a third higher than a year ago, thanks in part to big profits from trading. "
)


def wib_page(paragraphs: int = 9) -> str:
    body = "".join(f"<p>{STORY}Story {n}.</p>" for n in range(paragraphs))
    return (
        "<html><head><title>World in Brief: American banks' mixed earnings</title></head>"
        f"<body><main><article><h1>World in Brief: American banks' mixed earnings</h1>"
        '<p><time datetime="2026-10-13T00:00:00.000Z"> <!-- -->Oct 13th 2026</time>'
        '<time datetime="2026-10-13T16:28:23.000Z" class="css-j1r0u5">&nbsp;(<span>updated</span>'
        ' <!-- -->1h ago<!-- -->)</time></p>'
        f"{body}</article></main></body></html>"
    )


class WorldInBriefIngestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = AppConfig(database_path=str(Path(self.directory.name) / "a.sqlite3"))

    def tearDown(self):
        self.directory.cleanup()

    def test_saves_pushed_page_as_world_in_brief_item(self):
        result = ingest_world_in_brief_html(
            self.config, url=WIB_URL, html=wib_page(), source_host="DXNY124"
        )

        self.assertTrue(result.ok, result.message)
        self.assertIn("mixed earnings", result.title)
        with ArticleStore(self.config.database_path) as store:
            items = store.feed_items(limit=10)
            article = store.get_article(WIB_URL)
        self.assertEqual([item.link for item in items], [WIB_URL])
        self.assertIn("Goldman Sachs", items[0].content_html)
        self.assertEqual(article.content_source, "economist_world_in_brief_push")
        self.assertEqual(items[0].published, "Tue, 13 Oct 2026 16:28:23 +0000")
        last_push = world_in_brief_last_push(self.config)
        self.assertEqual(last_push["url"], WIB_URL)
        self.assertEqual(last_push["source_host"], "DXNY124")

    def test_rejects_pages_that_are_not_full_world_in_brief_text(self):
        cases = {
            "bad_url": ("https://www.economist.com/leaders/2026/10/13/x", wib_page()),
            "bad_url_host": ("https://evil.example/the-world-in-brief/2026/10/13/x", wib_page()),
            "cloudflare_challenge": (WIB_URL, "<title>Just a moment...</title>"),
            "excerpt_or_login_required": (WIB_URL, wib_page(paragraphs=1)),
        }
        for name, (url, html) in cases.items():
            with self.subTest(name):
                result = ingest_world_in_brief_html(self.config, url=url, html=html)
                self.assertFalse(result.ok)
                self.assertEqual(result.status, name.removesuffix("_host"))
        self.assertIsNone(world_in_brief_last_push(self.config))

    def test_response_status_codes(self):
        payload, status = _world_in_brief_ingest_response(
            self.config, {"url": WIB_URL, "html": wib_page()}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["url"], WIB_URL)

        payload, status = _world_in_brief_ingest_response(self.config, {"url": WIB_URL})
        self.assertEqual(status, 400)

        payload, status = _world_in_brief_ingest_response(
            self.config, {"url": WIB_URL, "html": "<p>Log in to read</p>"}
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["status"], "excerpt_or_login_required")


class RefreshDisabledTests(unittest.TestCase):
    def test_disabled_refresh_never_fetches(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                database_path=str(Path(directory) / "a.sqlite3"), refresh_enabled=False
            )
            with patch("economist_rss.refresh.refresh") as refresh:
                summary = refresh_if_stale(config, force=True)
        refresh.assert_not_called()
        self.assertEqual(summary.skipped_reason, "refresh_disabled")

    def test_env_turns_refresh_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feeds.toml"
            path.write_text('database_path = "x.sqlite3"\n')
            self.assertTrue(load_config(path).refresh_enabled)
            with patch.dict(os.environ, {"ECONOMIST_REFRESH_ENABLED": "false"}):
                self.assertFalse(load_config(path).refresh_enabled)


if __name__ == "__main__":
    unittest.main()
