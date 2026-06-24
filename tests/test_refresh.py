import json
import tempfile
import unittest
from pathlib import Path

from economist_rss.browser import BrowserResult
from economist_rss.config import AppConfig, FeedConfig
from economist_rss.extract import ArticleContent
from economist_rss.fetch import FetchError, FetchResponse
from economist_rss.refresh import refresh
from economist_rss.store import ArticleStore


class RefreshRateLimitTests(unittest.TestCase):
    def test_refresh_stops_batch_after_article_403(self):
        rss = """
        <rss version="2.0">
          <channel>
            <item>
              <title>First</title>
              <link>https://www.economist.com/finance/2026/06/23/first</link>
              <guid>first</guid>
              <pubDate>Tue, 23 Jun 2026 10:00:00 +0000</pubDate>
            </item>
            <item>
              <title>Second</title>
              <link>https://www.economist.com/finance/2026/06/23/second</link>
              <guid>second</guid>
              <pubDate>Tue, 23 Jun 2026 09:00:00 +0000</pubDate>
            </item>
          </channel>
        </rss>
        """
        calls = []

        class FakeFetcher:
            def __init__(self, **_kwargs):
                pass

            def fetch_text(self, url):
                calls.append(url)
                if url == "https://www.economist.com/latest/rss.xml":
                    return FetchResponse(
                        url=url,
                        status=200,
                        text=rss,
                        content_type="application/rss+xml",
                        headers={},
                    )
                raise FetchError(f"HTTP 403 while fetching {url}", status_code=403)

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            config = AppConfig(
                feeds=[
                    FeedConfig(
                        name="The Economist",
                        url="https://www.economist.com/latest/rss.xml",
                    )
                ],
                database_path=str(database_path),
                max_articles_per_refresh=2,
                min_article_delay_seconds=0,
                max_article_delay_seconds=0,
                browser_fetch_enabled=False,
            )
            with ArticleStore(database_path) as store:
                import economist_rss.refresh as refresh_module

                original_fetcher = refresh_module.Fetcher
                refresh_module.Fetcher = FakeFetcher
                try:
                    with self.assertLogs("economist_rss.refresh", level="INFO") as logs:
                        summary = refresh(store, config, force=True)
                finally:
                    refresh_module.Fetcher = original_fetcher

                self.assertEqual(summary.articles_fetched, 0)
                self.assertEqual(summary.articles_failed, 1)
                self.assertIn("HTTP 403", summary.stop_reason)
                self.assertEqual(
                    calls,
                    [
                        "https://www.economist.com/latest/rss.xml",
                        "https://www.economist.com/finance/2026/06/23/first",
                    ],
                )
                payloads = [
                    json.loads(message.split("article_fetch ", 1)[1])
                    for message in logs.output
                ]
                self.assertEqual(
                    [payload["event"] for payload in payloads],
                    ["article_fetch_start", "article_fetch_result"],
                )
                result = payloads[1]
                self.assertEqual(result["title"], "First")
                self.assertEqual(result["status"], "rate_limited")
                self.assertEqual(result["http_status"], 403)
                self.assertTrue(result["stop_refresh"])
                self.assertIn("HTTP 403", result["stop_reason"])

    def test_world_in_brief_fetch_saves_resolved_dated_item(self):
        final_url = (
            "https://www.economist.com/the-world-in-brief/2026/06/23/"
            "6ec0913d-b5b1-40cb-a4a1-4ea8314aec8b"
        )

        def fake_browser_fetch(url, _config):
            self.assertEqual(url, "https://www.economist.com/the-world-in-brief")
            return BrowserResult(
                ok=True,
                status="ok",
                message="Fetched full article text with authenticated browser.",
                url=url,
                final_url=final_url,
                http_status=200,
                article=ArticleContent(
                    title="The world in brief",
                    content_html="<p>The world in brief daily news</p>",
                    text="The world in brief " + ("daily news " * 100),
                    method="test",
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            config = AppConfig(
                feeds=[],
                database_path=str(database_path),
                max_articles_per_refresh=1,
                min_article_delay_seconds=0,
                max_article_delay_seconds=0,
                browser_fetch_enabled=True,
                world_in_brief_enabled=True,
                world_in_brief_refresh_interval_seconds=0,
            )
            with ArticleStore(database_path) as store:
                import economist_rss.refresh as refresh_module

                original_browser_fetch = refresh_module.fetch_article_with_browser
                refresh_module.fetch_article_with_browser = fake_browser_fetch
                try:
                    with self.assertLogs("economist_rss.refresh", level="INFO") as logs:
                        summary = refresh(store, config, force=False)
                finally:
                    refresh_module.fetch_article_with_browser = original_browser_fetch

                self.assertEqual(summary.articles_fetched, 1)
                self.assertEqual(summary.articles_failed, 0)
                items = store.feed_items(limit=10)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].title, "The world in brief")
                self.assertEqual(items[0].link, final_url)
                self.assertIn("daily news", items[0].content_html)

                payloads = [
                    json.loads(message.split("article_fetch ", 1)[1])
                    for message in logs.output
                ]
                self.assertEqual(
                    [payload["event"] for payload in payloads],
                    ["article_fetch_start", "article_fetch_result"],
                )
                self.assertEqual(payloads[1]["special_source"], "world_in_brief")
                self.assertEqual(payloads[1]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
