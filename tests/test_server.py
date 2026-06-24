import os
import unittest

from economist_rss.feed import FeedItem
from economist_rss.server import (
    _authorized,
    _category_filters,
    _filter_items_by_category,
)


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


if __name__ == "__main__":
    unittest.main()
