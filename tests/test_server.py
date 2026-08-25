import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote
import xml.etree.ElementTree as ET

from economist_rss.config import AppConfig
from economist_rss.feed import FeedItem
from economist_rss.server import (
    CatalogSearchQuery,
    _api_article_fetch_response,
    _api_article_status_response,
    _api_articles_response,
    _api_search_response,
    _api_search_scope,
    _api_stats_response,
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
    _required_bearer_authorized,
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

    def test_required_bearer_rejects_missing_configuration_and_query_tokens(self):
        old_value = os.environ.pop("ECONOMIST_REFRESH_TOKEN", None)
        try:
            self.assertFalse(
                _required_bearer_authorized("", "ECONOMIST_REFRESH_TOKEN")
            )
            os.environ["ECONOMIST_REFRESH_TOKEN"] = "write-secret"
            self.assertFalse(
                _required_bearer_authorized(
                    "write-secret",
                    "ECONOMIST_REFRESH_TOKEN",
                )
            )
            self.assertTrue(
                _required_bearer_authorized(
                    "Bearer write-secret",
                    "ECONOMIST_REFRESH_TOKEN",
                )
            )
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_REFRESH_TOKEN", None)
            else:
                os.environ["ECONOMIST_REFRESH_TOKEN"] = old_value


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


class ServerApiTests(unittest.TestCase):
    def test_stats_endpoint_summarizes_full_database_and_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            with ArticleStore(database_path) as store:
                first = store.upsert_feed_item(
                    FeedItem(
                        title="Asia story",
                        link="https://www.economist.com/asia/2026/08/20/story",
                        guid="asia-story",
                        published="Thu, 20 Aug 2026 12:00:00 +0000",
                        categories=["Asia", "Interactive"],
                    )
                )
                store.upsert_feed_item(
                    FeedItem(
                        title="Business story",
                        link="https://www.economist.com/business/2026/08/21/story",
                        guid="business-story",
                        published="Fri, 21 Aug 2026 12:00:00 +0000",
                        categories=["Business"],
                    )
                )
                store.save_article_content(
                    first,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )
                store.set_state("last_refresh_at", "2026-08-25T12:00:00+00:00")
                store.set_state("current_issue_id", "2026-08-22")
                store.set_state("current_issue_article_count", "74")

            response = _api_stats_response(
                AppConfig(feeds=[], database_path=str(database_path))
            )

            self.assertEqual(response["articles"]["total"], 2)
            self.assertEqual(response["articles"]["full_text"], 1)
            self.assertEqual(response["articles"]["metadata_only"], 1)
            self.assertEqual(
                response["articles"]["full_text_coverage_percent"],
                50.0,
            )
            self.assertEqual(response["sections"]["total"], 3)
            self.assertEqual(
                {section["name"] for section in response["sections"]["values"]},
                {"Asia", "Business", "Interactive"},
            )
            self.assertEqual(
                response["refresh"]["last_refresh_at"],
                "2026-08-25T12:00:00+00:00",
            )
            self.assertEqual(
                response["refresh"]["current_issue_article_count"],
                74,
            )
            self.assertEqual(
                response["refresh"]["default_feed_article_count"],
                1,
            )

    def test_search_is_local_first_and_feed_scope_is_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            with ArticleStore(database_path) as store:
                local = store.upsert_feed_item(
                    FeedItem(
                        title="A difficult diplomatic bargain",
                        link="https://www.economist.com/asia/1998/01/02/bargain",
                        guid="local",
                        summary="Negotiators return to the table",
                        published="Fri, 02 Jan 1998 12:00:00 +0000",
                        categories=["Asia"],
                    )
                )
                store.save_article_content(
                    local,
                    content_html="<p>Iran nuclear negotiations</p>",
                    content_text="Iran nuclear negotiations SECRET BODY MARKER",
                    content_source="test",
                )
                store.upsert_feed_item(
                    FeedItem(
                        title="Iran changes its economic strategy",
                        link=(
                            "https://www.economist.com/asia/1999/12/31/"
                            "iran-strategy"
                        ),
                        guid="feed",
                        summary="<p>A regional &amp; policy shift</p>",
                        published="Fri, 31 Dec 1999 12:00:00 +0000",
                        categories=["Asia"],
                    )
                )
                store.set_state("last_refresh_at", "2000-01-01T00:00:00+00:00")

            config = AppConfig(feeds=[], database_path=str(database_path))
            response = _api_search_response(
                config,
                "q=Iran&start_date=1990-01-01&end_date=1999-12-31"
                "&category=Asia&limit=10",
            )

            self.assertEqual(response["count"], 2)
            self.assertEqual(response["local_count"], 1)
            self.assertEqual(response["feed_count"], 1)
            results = response["results"]
            assert isinstance(results, list)
            self.assertEqual([item["guid"] for item in results], ["local", "feed"])
            self.assertEqual(results[0]["match_source"], "local_full_text")
            self.assertEqual(results[1]["match_source"], "economist_feed_metadata")
            self.assertEqual(results[1]["snippet"], "A regional & policy shift")
            self.assertNotIn("content_text", results[0])
            self.assertNotIn("content_html", results[0])
            self.assertNotIn("SECRET BODY MARKER", str(response))

            feed_response = _api_search_response(
                config,
                "q=Iran&scope=rss&limit=10",
            )
            feed_results = feed_response["results"]
            assert isinstance(feed_results, list)
            self.assertEqual([item["guid"] for item in feed_results], ["feed"])

    def test_articles_endpoint_is_paginated_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            with ArticleStore(database_path) as store:
                for day in range(1, 4):
                    store.upsert_feed_item(
                        FeedItem(
                            title=f"Story {day}",
                            link=f"https://www.economist.com/business/2000/01/0{day}/story",
                            guid=f"story-{day}",
                            summary=f"Snippet {day}",
                            published=f"{day:02d} Jan 2000 12:00:00 +0000",
                            categories=["Business"],
                        )
                    )

            config = AppConfig(feeds=[], database_path=str(database_path))
            first_page = _api_articles_response(
                config,
                "start_date=2000-01-01&end_date=2000-01-03"
                "&category=Business&limit=2",
            )
            first_results = first_page["results"]
            assert isinstance(first_results, list)
            self.assertEqual(
                [item["guid"] for item in first_results],
                ["story-3", "story-2"],
            )
            self.assertTrue(first_page["has_more"])
            self.assertEqual(first_page["next_offset"], 2)

            second_page = _api_articles_response(config, "limit=2&offset=2")
            second_results = second_page["results"]
            assert isinstance(second_results, list)
            self.assertEqual([item["guid"] for item in second_results], ["story-1"])
            self.assertFalse(second_page["has_more"])

    def test_fetch_endpoint_queues_known_article_and_status_can_poll_it(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            article_url = "https://www.economist.com/leaders/1999/01/01/story"
            with ArticleStore(database_path) as store:
                store.upsert_feed_item(
                    FeedItem(
                        title="Story",
                        link=article_url,
                        guid="story-guid",
                    )
                )

            config = AppConfig(feeds=[], database_path=str(database_path))
            queued, status = _api_article_fetch_response(
                config,
                {"guid": "story-guid"},
            )

            self.assertEqual(status, 202)
            self.assertEqual(queued["status"], "queued")
            queued_article = queued["article"]
            assert isinstance(queued_article, dict)
            self.assertTrue(queued_article["fetch_requested_at"])
            self.assertEqual(queued_article["fetch_request_count"], 1)

            status_payload, status_code = _api_article_status_response(
                config,
                f"url={quote(article_url, safe='')}",
            )
            self.assertEqual(status_code, 200)
            status_article = status_payload["article"]
            assert isinstance(status_article, dict)
            self.assertTrue(status_article["fetch_requested"])
            self.assertFalse(status_article["full_text_available"])

    def test_fetch_endpoint_rejects_unknown_and_non_economist_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            with ArticleStore(database_path) as store:
                store.upsert_feed_item(
                    FeedItem(
                        title="Not allowed",
                        link="https://example.com/story",
                        guid="not-allowed",
                    )
                )
            config = AppConfig(feeds=[], database_path=str(database_path))

            missing, missing_status = _api_article_fetch_response(
                config,
                {"url": "https://www.economist.com/missing"},
            )
            rejected, rejected_status = _api_article_fetch_response(
                config,
                {"guid": "not-allowed"},
            )

            self.assertEqual(missing_status, 404)
            self.assertEqual(rejected_status, 400)
            self.assertIn("not an allowed Economist URL", rejected["error"])

    def test_api_validates_scope_limit_and_offset(self):
        self.assertEqual(_api_search_scope("scope=rss"), "feed")
        self.assertEqual(_api_search_scope("scope=local"), "local")
        with self.assertRaisesRegex(ValueError, "scope must be"):
            _api_search_scope("scope=internet")

        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                feeds=[],
                database_path=str(Path(directory) / "articles.sqlite3"),
            )
            with self.assertRaisesRegex(ValueError, "limit must be between"):
                _api_search_response(config, "limit=501")
            with self.assertRaisesRegex(ValueError, "offset must be between"):
                _api_articles_response(config, "offset=-1")


if __name__ == "__main__":
    unittest.main()
