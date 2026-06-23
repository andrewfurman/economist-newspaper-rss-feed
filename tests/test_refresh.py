import tempfile
import unittest
from pathlib import Path

from economist_rss.config import AppConfig, FeedConfig
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


if __name__ == "__main__":
    unittest.main()
