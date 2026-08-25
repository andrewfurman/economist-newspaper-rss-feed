import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from economist_rss.config import AppConfig
from economist_rss.feed import FeedItem
from economist_rss.server import (
    CatalogSearchQuery,
    _article_lookup_key,
    _article_text_body,
    _authorized,
    _catalog_search_query,
    _category_from_feed_path,
    _category_filters,
    _filter_items_by_category,
    _rss_item_limit,
    _rss_description,
    _rss_title,
    _rss_response,
)
from economist_rss.store import ArticleStore


class ServerAuthTests(unittest.TestCase):
    def test_allows_when_token_is_not_configured(self):
        old_value = os.environ.pop("ECONOMIST_FEED_TOKEN", None)
        try:
            self.assertTrue(_authorized("", "", "ECONOMIST_FEED_TOKEN"))
        finally:
            if old_value is not None:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value

    def test_allows_bearer_token(self):
        old_value = os.environ.get("ECONOMIST_FEED_TOKEN")
        os.environ["ECONOMIST_FEED_TOKEN"] = "secret-token"
        try:
            self.assertTrue(
                _authorized("Bearer secret-token", "", "ECONOMIST_FEED_TOKEN")
            )
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_FEED_TOKEN", None)
            else:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value

    def test_allows_query_token_for_rss_readers(self):
        old_value = os.environ.get("ECONOMIST_FEED_TOKEN")
        os.environ["ECONOMIST_FEED_TOKEN"] = "secret-token"
        try:
            self.assertTrue(_authorized("", "token=secret-token", "ECONOMIST_FEED_TOKEN"))
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_FEED_TOKEN", None)
            else:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value

    def test_rejects_wrong_token(self):
        old_value = os.environ.get("ECONOMIST_FEED_TOKEN")
        os.environ["ECONOMIST_FEED_TOKEN"] = "secret-token"
        try:
            self.assertFalse(_authorized("", "token=wrong", "ECONOMIST_FEED_TOKEN"))
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_FEED_TOKEN", None)
            else:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value


class ServerCategoryFilterTests(unittest.TestCase):
    def test_catalog_search_query_parses_keywords_and_inclusive_dates(self):
        parsed = _catalog_search_query(
            "q=Iran%27s+nuclear+policy&start_date=1990-01-01&end_date=1999-12-31"
        )

        self.assertTrue(parsed.active)
        self.assertEqual(parsed.query, "Iran's nuclear policy")
        self.assertEqual(parsed.start_date.isoformat(), "1990-01-01")
        self.assertEqual(parsed.end_date.isoformat(), "1999-12-31")

    def test_catalog_search_query_validates_dates_and_ranges(self):
        with self.assertRaisesRegex(ValueError, "start_date must use YYYY-MM-DD"):
            _catalog_search_query("start_date=01-01-1990")
        with self.assertRaisesRegex(ValueError, "start_date must be on or before"):
            _catalog_search_query("start_date=2000-01-01&end_date=1990-01-01")

    def test_search_feed_uses_local_catalog_without_refreshing(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            with ArticleStore(database_path) as store:
                store.upsert_feed_item(
                    FeedItem(
                        title="Iran in the 1990s",
                        link="https://www.economist.com/leaders/1999/12/31/iran",
                        guid="iran-1999",
                        summary="A decade of change",
                        published="Fri, 31 Dec 1999 12:00:00 +0000",
                        categories=["Leaders"],
                    )
                )

            config = AppConfig(feeds=[], database_path=str(database_path))
            with patch("economist_rss.server.refresh_if_stale") as refresh_mock:
                rss = _rss_response(
                    config,
                    "q=Iran&start_date=1990-01-01&end_date=1999-12-31&limit=10",
                )

            refresh_mock.assert_not_called()
            root = ET.fromstring(rss)
            self.assertEqual(root.findtext("./channel/item/guid"), "iran-1999")
            self.assertIn("Search: Iran", root.findtext("./channel/title") or "")

    def test_default_feed_still_runs_freshness_check(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            config = AppConfig(
                feeds=[],
                database_path=str(database_path),
                current_issue_filter_enabled=False,
            )
            with patch("economist_rss.server.refresh_if_stale") as refresh_mock:
                _rss_response(config, "limit=10")

            refresh_mock.assert_called_once_with(config)

    def test_category_feed_path_maps_slug_to_category(self):
        self.assertEqual(
            _category_from_feed_path("/rss/category/united-states.xml"),
            "United States",
        )
        self.assertEqual(
            _category_from_feed_path("/rss/category/the-world-in-brief.xml"),
            "The World in Brief",
        )

    def test_category_feed_path_accepts_url_encoded_category(self):
        self.assertEqual(
            _category_from_feed_path("/rss/category/United%20States.xml"),
            "United States",
        )

    def test_category_feed_path_rejects_non_feed_paths(self):
        self.assertIsNone(_category_from_feed_path("/rss.xml"))
        self.assertIsNone(_category_from_feed_path("/rss/category/united-states"))

    def test_category_filters_accept_repeated_and_comma_separated_values(self):
        self.assertEqual(
            _category_filters(
                "token=secret&category=United+States&category=Business,Culture"
            ),
            ["United States", "Business", "Culture"],
        )

    def test_filter_items_by_derived_category(self):
        items = [
            FeedItem(
                title="The US in Brief: A big night for Zohran Mamdani",
                link=(
                    "https://www.economist.com/in-brief/2026/06/24/"
                    "the-us-in-brief-a-big-night-for-zohran-mamdani"
                ),
                guid="us-in-brief",
            ),
            FeedItem(
                title="Electronics can now be printed onto living tissues",
                link=(
                    "https://www.economist.com/science-and-technology/2026/06/24/"
                    "electronics-can-now-be-printed-onto-living-tissues"
                ),
                guid="science",
            ),
        ]

        filtered = _filter_items_by_category(items, ["United States"])

        self.assertEqual([item.guid for item in filtered], ["us-in-brief"])

    def test_category_feed_metadata_names_filtered_feed(self):
        self.assertEqual(
            _rss_title(["United States"]),
            "The Economist private article feed - United States",
        )
        self.assertIn("United States", _rss_description(["United States"]))

    def test_search_feed_metadata_names_catalog_query(self):
        search = CatalogSearchQuery(query="Iran")
        self.assertIn("Search: Iran", _rss_title([], search))
        self.assertIn("keywords: Iran", _rss_description([], search))

    def test_rss_item_limit_defaults_to_configured_limit(self):
        self.assertEqual(_rss_item_limit("token=secret", 500), 500)
        self.assertEqual(_rss_item_limit("token=secret", None), 500)

    def test_rss_item_limit_accepts_limit_or_count(self):
        self.assertEqual(_rss_item_limit("token=secret&limit=20", 500), 20)
        self.assertEqual(_rss_item_limit("token=secret&count=50", 500), 50)

    def test_rss_item_limit_is_capped_by_configured_limit(self):
        self.assertEqual(_rss_item_limit("token=secret&limit=1000", 500), 500)
        self.assertEqual(_rss_item_limit("token=secret&limit=1000", None), 500)

    def test_rss_item_limit_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            _rss_item_limit("token=secret&limit=0", 500)
        with self.assertRaises(ValueError):
            _rss_item_limit("token=secret&limit=abc", 500)


class ServerArticleTextTests(unittest.TestCase):
    def test_article_lookup_key_accepts_url_link_or_guid(self):
        self.assertEqual(
            _article_lookup_key(
                "token=secret&url=https%3A%2F%2Fwww.economist.com%2Fstory"
            ),
            "https://www.economist.com/story",
        )
        self.assertEqual(
            _article_lookup_key(
                "token=secret&link=https%3A%2F%2Fwww.economist.com%2Fstory"
            ),
            "https://www.economist.com/story",
        )
        self.assertEqual(
            _article_lookup_key("token=secret&guid=story-1"),
            "story-1",
        )

    def test_article_lookup_key_rejects_empty_lookup(self):
        self.assertIsNone(_article_lookup_key("token=secret&url="))
        self.assertIsNone(_article_lookup_key("token=secret"))

    def test_article_text_body_returns_cached_plain_text(self):
        article = SimpleNamespace(
            content_status="ok",
            content_text="\nFirst paragraph.\n\nSecond paragraph.  \n",
        )

        self.assertEqual(
            _article_text_body(article),
            "First paragraph.\n\nSecond paragraph.",
        )

    def test_article_text_body_rejects_missing_or_failed_content(self):
        self.assertIsNone(_article_text_body(None))
        self.assertIsNone(
            _article_text_body(
                SimpleNamespace(content_status="login_required", content_text="Text")
            )
        )
        self.assertIsNone(
            _article_text_body(SimpleNamespace(content_status="ok", content_text="   "))
        )


if __name__ == "__main__":
    unittest.main()
