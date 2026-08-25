from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from economist_rss.catalog import (
    _PageFetch,
    backfill_catalog_content,
    discover_catalog,
    weekly_issue_dates,
)
from economist_rss.config import AppConfig
from economist_rss.feed import FeedItem
from economist_rss.refresh import ArticleBatchSummary
from economist_rss.store import ArticleStore


class CatalogDiscoveryTests(unittest.TestCase):
    def test_weekly_issue_dates_are_inclusive_saturdays(self):
        self.assertEqual(
            weekly_issue_dates(date(2026, 6, 18), date(2026, 6, 28)),
            [date(2026, 6, 20), date(2026, 6, 27)],
        )

    def test_discovery_checkpoints_and_resumes_without_duplicate_articles(self):
        html_by_issue = {
            "2026-06-27": """
                <a href="/leaders/2026/06/25/shared-story">Shared story</a>
                <a href="/business/2026/06/24/business-story">Business story</a>
            """,
            "2026-06-20": """
                <a href="/leaders/2026/06/25/shared-story">Shared story</a>
                <a href="/asia/2026/06/18/asia-story">Asia story</a>
            """,
        }

        def fake_page(url, _fetcher, _config):
            issue_id = url.rsplit("/", 1)[-1]
            return _PageFetch(
                ok=True,
                status="ok",
                message="ok",
                source="test_weeklyedition",
                html=html_by_issue[issue_id],
                http_status=200,
            )

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            config = AppConfig(
                database_path=str(database_path),
                browser_fetch_enabled=False,
            )
            with patch("economist_rss.catalog._fetch_issue_page", side_effect=fake_page):
                first = discover_catalog(
                    config,
                    start_date=date(2026, 6, 20),
                    end_date=date(2026, 6, 27),
                    max_issues=1,
                )
                second = discover_catalog(
                    config,
                    start_date=date(2026, 6, 20),
                    end_date=date(2026, 6, 27),
                    max_issues=1,
                )

            self.assertEqual(first.issues_discovered, 1)
            self.assertEqual(second.issues_discovered, 1)
            with ArticleStore(database_path) as store:
                newest = store.get_catalog_issue("2026-06-27")
                older = store.get_catalog_issue("2026-06-20")
                stats = store.catalog_stats()
                shared = store.get_article(
                    "https://www.economist.com/leaders/2026/06/25/shared-story"
                )
                asia = store.get_article(
                    "https://www.economist.com/asia/2026/06/18/asia-story"
                )

            self.assertEqual(newest.discovery_status, "ok")
            self.assertEqual(older.discovery_status, "ok")
            self.assertEqual(stats.issues_discovered, 2)
            self.assertEqual(stats.article_count, 3)
            self.assertEqual(shared.issue_id, "2026-06-27")
            self.assertEqual(asia.categories, ["Asia"])
            self.assertEqual(asia.published_at, "2026-06-18T00:00:00+00:00")

    def test_discovery_stops_on_rate_limit_and_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            config = AppConfig(database_path=str(database_path))
            blocked = _PageFetch(
                ok=False,
                status="rate_limited",
                message="HTTP 429",
                source="test_http",
                http_status=429,
            )
            with patch(
                "economist_rss.catalog._fetch_issue_page", return_value=blocked
            ) as fetch_mock:
                summary = discover_catalog(
                    config,
                    start_date=date(2026, 6, 1),
                    end_date=date(2026, 6, 27),
                    max_issues=5,
                )

            self.assertEqual(summary.status, "stopped")
            self.assertEqual(summary.issues_attempted, 1)
            self.assertEqual(summary.issues_failed, 1)
            fetch_mock.assert_called_once()
            with ArticleStore(database_path) as store:
                issue = store.get_catalog_issue("2026-06-27")
                stop_reason = store.get_state("catalog_last_discovery_stop_reason")
            self.assertEqual(issue.discovery_status, "rate_limited")
            self.assertEqual(stop_reason, "HTTP 429")

    def test_discovery_validates_range_and_per_run_limit(self):
        config = AppConfig(database_path=":memory:")
        with self.assertRaisesRegex(ValueError, "start_date"):
            discover_catalog(
                config,
                start_date=date(2026, 6, 28),
                end_date=date(2026, 6, 20),
            )
        with self.assertRaisesRegex(ValueError, "max_issues"):
            discover_catalog(config, max_issues=11)

    def test_content_backfill_uses_date_range_and_existing_fetch_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            config = AppConfig(
                database_path=str(database_path),
                max_articles_per_refresh=3,
            )
            with ArticleStore(database_path) as store:
                for guid, published in (
                    ("in-range", "Fri, 31 Dec 1999 12:00:00 +0000"),
                    ("too-new", "Sat, 01 Jan 2000 12:00:00 +0000"),
                    ("undated", None),
                ):
                    store.upsert_feed_item(
                        FeedItem(
                            title=guid,
                            link=f"https://www.economist.com/leaders/1999/12/31/{guid}",
                            guid=guid,
                            published=published,
                        )
                    )

            captured = []

            def fake_batch(_store, _config, candidates, **_kwargs):
                captured.extend(candidates)
                return ArticleBatchSummary(fetched=0, failed=0)

            with patch("economist_rss.catalog.fetch_article_batch", side_effect=fake_batch):
                summary = backfill_catalog_content(
                    config,
                    start_date=date(1990, 1, 1),
                    end_date=date(1999, 12, 31),
                    max_articles=5,
                )

            self.assertEqual(summary.status, "ok")
            self.assertEqual(summary.candidates, 1)
            self.assertEqual([article.guid for article in captured], ["in-range"])


if __name__ == "__main__":
    unittest.main()
