import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

from economist_rss.feed import FeedItem, build_rss
from economist_rss.server import _filter_items_by_category
from economist_rss.store import ArticleStore


class ArticleStoreTests(unittest.TestCase):
    def test_successful_article_is_not_pending_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                article = store.upsert_feed_item(
                    FeedItem(
                        title="Story",
                        link="https://www.economist.com/business/2026/06/23/story?utm_source=x",
                        guid="story-1",
                        summary="Summary",
                        source="Latest",
                    )
                )
                self.assertEqual(
                    len(
                        store.pending_articles(
                            limit=10,
                            retry_failed_after_seconds=1,
                            exclude_url_patterns=[],
                        )
                    ),
                    1,
                )

                store.save_article_content(
                    article,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )

                self.assertEqual(
                    store.pending_articles(
                        limit=10,
                        retry_failed_after_seconds=1,
                        exclude_url_patterns=[],
                    ),
                    [],
                )
                self.assertEqual(
                    store.pending_articles(
                        limit=10,
                        retry_failed_after_seconds=1,
                        exclude_url_patterns=[],
                        force=True,
                    ),
                    [],
                )

    def test_canonical_url_deduplicates_tracking_params(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                first = store.upsert_feed_item(
                    FeedItem(
                        title="Original",
                        link="https://www.economist.com/finance/2026/06/23/story?utm_source=x",
                        guid="first",
                    )
                )
                second = store.upsert_feed_item(
                    FeedItem(
                        title="Updated",
                        link="https://www.economist.com/finance/2026/06/23/story?utm_medium=y",
                        guid="second",
                    )
                )

                self.assertEqual(first.canonical_url, second.canonical_url)
                self.assertEqual(len(store.pending_articles(limit=10, retry_failed_after_seconds=1, exclude_url_patterns=[])), 1)

    def test_get_article_can_lookup_by_guid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                article = store.upsert_feed_item(
                    FeedItem(
                        title="Story",
                        link="https://www.economist.com/briefing/2026/06/23/story",
                        guid="story-guid",
                    )
                )

                found = store.get_article("story-guid")

                self.assertIsNotNone(found)
                assert found is not None
                self.assertEqual(found.canonical_url, article.canonical_url)

    def test_feed_items_can_be_limited_to_recent_published_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            now = datetime.now(timezone.utc)
            with ArticleStore(path) as store:
                old = store.upsert_feed_item(
                    FeedItem(
                        title="Old",
                        link="https://www.economist.com/finance/2026/06/01/old",
                        guid="old",
                        published=format_datetime(now - timedelta(days=10)),
                    )
                )
                recent = store.upsert_feed_item(
                    FeedItem(
                        title="Recent",
                        link="https://www.economist.com/finance/2026/06/23/recent",
                        guid="recent",
                        published=format_datetime(now - timedelta(days=1)),
                    )
                )
                store.save_article_content(
                    old,
                    content_html="<p>Old text</p>",
                    content_text="Old text",
                    content_source="test",
                )
                store.save_article_content(
                    recent,
                    content_html="<p>Recent text</p>",
                    content_text="Recent text",
                    content_source="test",
                )

                items = store.feed_items(limit=10, published_after=now - timedelta(days=7))

                self.assertEqual([item.title for item in items], ["Recent"])

    def test_feed_items_include_cached_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                article = store.upsert_feed_item(
                    FeedItem(
                        title="The world in brief",
                        link="https://www.economist.com/the-world-in-brief/2026/06/23/id",
                        guid="world-in-brief",
                    )
                )
                store.save_article_content(
                    article,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )

                items = store.feed_items(limit=10)

                self.assertEqual(items[0].content_text, "Full text")

    def test_feed_items_limit_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                for index in range(3):
                    article = store.upsert_feed_item(
                        FeedItem(
                            title=f"Story {index}",
                            link=f"https://www.economist.com/essay/2026/06/2{index}/story",
                            guid=f"story-{index}",
                        )
                    )
                    store.save_article_content(
                        article,
                        content_html=f"<p>Full text {index}</p>",
                        content_text=f"Full text {index}",
                        content_source="test",
                    )

                self.assertEqual(len(store.feed_items(limit=2)), 2)
                self.assertEqual(len(store.feed_items(limit=None)), 3)
                self.assertEqual(store.feed_items(limit=0), [])

    def test_feed_items_only_include_latest_brief_items(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            base = datetime(2026, 6, 27, tzinfo=timezone.utc)
            with ArticleStore(path) as store:
                old_world = store.upsert_feed_item(
                    FeedItem(
                        title="The world in brief: old update",
                        link=(
                            "https://www.economist.com/the-world-in-brief/"
                            "2026/06/25/old"
                        ),
                        guid="old-world",
                        published=format_datetime(base - timedelta(days=2)),
                    )
                )
                latest_world = store.upsert_feed_item(
                    FeedItem(
                        title="The world in brief: latest update",
                        link=(
                            "https://www.economist.com/the-world-in-brief/"
                            "2026/06/27/latest"
                        ),
                        guid="latest-world",
                        published=format_datetime(base),
                    )
                )
                old_us = store.upsert_feed_item(
                    FeedItem(
                        title="The US in Brief: old update",
                        link=(
                            "https://www.economist.com/in-brief/2026/06/25/"
                            "the-us-in-brief-old-update"
                        ),
                        guid="old-us",
                        published=format_datetime(base - timedelta(days=2)),
                    )
                )
                latest_us = store.upsert_feed_item(
                    FeedItem(
                        title="The US in Brief: latest update",
                        link=(
                            "https://www.economist.com/in-brief/2026/06/27/"
                            "the-us-in-brief-latest-update"
                        ),
                        guid="latest-us",
                        published=format_datetime(base - timedelta(hours=1)),
                    )
                )
                regular = store.upsert_feed_item(
                    FeedItem(
                        title="Regular story",
                        link="https://www.economist.com/business/2026/06/27/story",
                        guid="regular",
                        published=format_datetime(base - timedelta(hours=2)),
                    )
                )
                for article in (old_world, latest_world, old_us, latest_us, regular):
                    store.save_article_content(
                        article,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                titles = [item.title for item in store.feed_items(limit=None)]

                self.assertIn("The world in brief: latest update", titles)
                self.assertNotIn("The world in brief: old update", titles)
                self.assertIn("The US in Brief: latest update", titles)
                self.assertNotIn("The US in Brief: old update", titles)
                self.assertIn("Regular story", titles)

                stored_old_world = store.get_article(old_world.canonical_url)
                stored_old_us = store.get_article(old_us.canonical_url)
                self.assertIsNotNone(stored_old_world)
                self.assertIsNotNone(stored_old_us)
                assert stored_old_world is not None
                assert stored_old_us is not None
                self.assertEqual(stored_old_world.content_status, "ok")
                self.assertEqual(stored_old_us.content_status, "ok")

    def test_feed_items_limit_applies_after_stale_brief_suppression(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            base = datetime(2026, 6, 27, tzinfo=timezone.utc)
            with ArticleStore(path) as store:
                latest_world = store.upsert_feed_item(
                    FeedItem(
                        title="The world in brief: latest update",
                        link=(
                            "https://www.economist.com/the-world-in-brief/"
                            "2026/06/27/latest"
                        ),
                        guid="latest-world",
                        published=format_datetime(base),
                    )
                )
                stale_world = store.upsert_feed_item(
                    FeedItem(
                        title="The world in brief: stale update",
                        link=(
                            "https://www.economist.com/the-world-in-brief/"
                            "2026/06/26/stale"
                        ),
                        guid="stale-world",
                        published=format_datetime(base - timedelta(minutes=1)),
                    )
                )
                regular = store.upsert_feed_item(
                    FeedItem(
                        title="Regular story",
                        link="https://www.economist.com/business/2026/06/27/story",
                        guid="regular",
                        published=format_datetime(base - timedelta(minutes=2)),
                    )
                )
                for article in (latest_world, stale_world, regular):
                    store.save_article_content(
                        article,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                items = store.feed_items(limit=2)

                self.assertEqual(
                    [item.title for item in items],
                    ["The world in brief: latest update", "Regular story"],
                )

    def test_feed_items_preserve_sourced_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                article = store.upsert_feed_item(
                    FeedItem(
                        title="Story",
                        link="https://www.economist.com/essay/2026/06/23/story",
                        guid="story",
                        categories=["Essay", "Special coverage", "Essay", ""],
                    )
                )
                store.save_article_content(
                    article,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )

                items = store.feed_items(limit=10)

                self.assertEqual(items[0].categories, ["Essay", "Special coverage"])
                output = build_rss(items)
                root = ET.fromstring(output)
                categories = [category.text for category in root.findall(".//category")]
                self.assertEqual(categories, ["Essay", "Special coverage"])

    def test_current_issue_filter_includes_issue_articles_before_issue_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            issue_date = datetime(2026, 6, 27, tzinfo=timezone.utc)
            with ArticleStore(path) as store:
                store.set_state("current_issue_id", "2026-06-27")
                store.set_state("current_issue_date", "2026-06-27")
                store.set_state("current_issue_article_count", "1")
                article = store.upsert_current_issue_article(
                    FeedItem(
                        title="Issue story published early",
                        link="https://www.economist.com/leaders/2026/06/24/story",
                        guid="issue-story",
                        published=format_datetime(issue_date - timedelta(days=3)),
                    ),
                    issue_id="2026-06-27",
                    issue_date="2026-06-27",
                    issue_source="weeklyedition_page",
                )
                older = store.upsert_feed_item(
                    FeedItem(
                        title="Older story",
                        link="https://www.economist.com/leaders/2026/06/19/old",
                        guid="old-story",
                        published=format_datetime(issue_date - timedelta(days=8)),
                    )
                )
                for stored in (article, older):
                    store.save_article_content(
                        stored,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                items = store.feed_items(limit=None, current_issue_only=True)

                self.assertEqual(
                    [item.title for item in items],
                    ["Issue story published early"],
                )

    def test_current_issue_filter_includes_online_exclusives_after_issue_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.set_state("current_issue_id", "2026-06-27")
                store.set_state("current_issue_date", "2026-06-27")
                store.set_state("current_issue_article_count", "10")
                article = store.upsert_feed_item(
                    FeedItem(
                        title="Online exclusive",
                        link="https://www.economist.com/business/2026/06/28/online",
                        guid="online",
                        published="Sun, 28 Jun 2026 10:00:00 +0000",
                    )
                )
                store.save_article_content(
                    article,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )

                items = store.feed_items(limit=None, current_issue_only=True)

                self.assertEqual([item.title for item in items], ["Online exclusive"])

    def test_current_issue_filter_excludes_prior_issue_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.set_state("current_issue_id", "2026-06-27")
                store.set_state("current_issue_date", "2026-06-27")
                store.set_state("current_issue_article_count", "10")
                prior = store.upsert_current_issue_article(
                    FeedItem(
                        title="Prior issue story",
                        link="https://www.economist.com/leaders/2026/06/19/prior",
                        guid="prior",
                        published="Fri, 19 Jun 2026 10:00:00 +0000",
                    ),
                    issue_id="2026-06-20",
                    issue_date="2026-06-20",
                    issue_source="weeklyedition_page",
                )
                latest = store.upsert_current_issue_article(
                    FeedItem(
                        title="Latest issue story",
                        link="https://www.economist.com/leaders/2026/06/26/latest",
                        guid="latest",
                        published="Fri, 26 Jun 2026 10:00:00 +0000",
                    ),
                    issue_id="2026-06-27",
                    issue_date="2026-06-27",
                    issue_source="weeklyedition_page",
                )
                for stored in (prior, latest):
                    store.save_article_content(
                        stored,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                items = store.feed_items(limit=None, current_issue_only=True)

                self.assertEqual([item.title for item in items], ["Latest issue story"])

    def test_current_issue_filter_limit_applies_after_filtering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.set_state("current_issue_id", "2026-06-27")
                store.set_state("current_issue_date", "2026-06-27")
                store.set_state("current_issue_article_count", "1")
                old = store.upsert_feed_item(
                    FeedItem(
                        title="Old story",
                        link="https://www.economist.com/business/2026/06/18/old",
                        guid="old",
                        published="Thu, 18 Jun 2026 10:00:00 +0000",
                    )
                )
                latest_one = store.upsert_current_issue_article(
                    FeedItem(
                        title="Latest one",
                        link="https://www.economist.com/business/2026/06/26/one",
                        guid="one",
                        published="Fri, 26 Jun 2026 11:00:00 +0000",
                    ),
                    issue_id="2026-06-27",
                    issue_date="2026-06-27",
                    issue_source="weeklyedition_page",
                )
                latest_two = store.upsert_feed_item(
                    FeedItem(
                        title="Latest two",
                        link="https://www.economist.com/business/2026/06/28/two",
                        guid="two",
                        published="Sun, 28 Jun 2026 11:00:00 +0000",
                    )
                )
                for stored in (old, latest_one, latest_two):
                    store.save_article_content(
                        stored,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                items = store.feed_items(limit=2, current_issue_only=True)

                self.assertEqual(
                    [item.title for item in items],
                    ["Latest two", "Latest one"],
                )

    def test_current_issue_filter_works_with_category_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.set_state("current_issue_id", "2026-06-27")
                store.set_state("current_issue_date", "2026-06-27")
                store.set_state("current_issue_article_count", "2")
                items_to_store = [
                    FeedItem(
                        title="United States story one",
                        link="https://www.economist.com/united-states/2026/06/26/one",
                        guid="us-one",
                        published="Fri, 26 Jun 2026 11:00:00 +0000",
                    ),
                    FeedItem(
                        title="United States story two",
                        link="https://www.economist.com/united-states/2026/06/25/two",
                        guid="us-two",
                        published="Thu, 25 Jun 2026 11:00:00 +0000",
                    ),
                    FeedItem(
                        title="Business story",
                        link="https://www.economist.com/business/2026/06/26/business",
                        guid="business",
                        published="Fri, 26 Jun 2026 09:00:00 +0000",
                    ),
                    FeedItem(
                        title="Old United States story",
                        link="https://www.economist.com/united-states/2026/06/18/old",
                        guid="old-us",
                        published="Thu, 18 Jun 2026 09:00:00 +0000",
                    ),
                ]
                stored_articles = [
                    store.upsert_current_issue_article(
                        item,
                        issue_id="2026-06-27" if item.guid != "old-us" else "2026-06-20",
                        issue_date="2026-06-27" if item.guid != "old-us" else "2026-06-20",
                        issue_source="weeklyedition_page",
                    )
                    for item in items_to_store
                ]
                for stored in stored_articles:
                    store.save_article_content(
                        stored,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                current_items = store.feed_items(limit=None, current_issue_only=True)
                filtered = _filter_items_by_category(current_items, ["United States"])[:1]

                self.assertEqual([item.title for item in filtered], ["United States story one"])

    def test_current_issue_filter_has_calendar_fallback_without_issue_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.set_state("current_issue_id", "2026-06-27")
                store.set_state("current_issue_date", "2026-06-27")
                store.set_state("current_issue_article_count", "0")
                current_week = store.upsert_feed_item(
                    FeedItem(
                        title="Current week story",
                        link="https://www.economist.com/business/2026/06/24/current",
                        guid="current",
                        published="Wed, 24 Jun 2026 10:00:00 +0000",
                    )
                )
                prior_week = store.upsert_feed_item(
                    FeedItem(
                        title="Prior week story",
                        link="https://www.economist.com/business/2026/06/20/prior",
                        guid="prior",
                        published="Sat, 20 Jun 2026 10:00:00 +0000",
                    )
                )
                for stored in (current_week, prior_week):
                    store.save_article_content(
                        stored,
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                        content_source="test",
                    )

                items = store.feed_items(limit=None, current_issue_only=True)

                self.assertEqual([item.title for item in items], ["Current week story"])

    def test_empty_category_update_does_not_erase_sourced_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.upsert_feed_item(
                    FeedItem(
                        title="Original",
                        link="https://www.economist.com/asia/2026/06/23/story",
                        guid="story",
                        categories=["Asia"],
                    )
                )
                article = store.upsert_feed_item(
                    FeedItem(
                        title="Updated",
                        link="https://www.economist.com/asia/2026/06/23/story",
                        guid="story",
                    )
                )
                store.save_article_content(
                    article,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )

                items = store.feed_items(limit=10)

                self.assertEqual(items[0].title, "Updated")
                self.assertEqual(items[0].categories, ["Asia"])

    def test_feed_items_use_url_category_when_no_sourced_category_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                article = store.upsert_feed_item(
                    FeedItem(
                        title="Story",
                        link="https://www.economist.com/asia/2026/06/23/story",
                        guid="story",
                    )
                )
                store.save_article_content(
                    article,
                    content_html="<p>Full text</p>",
                    content_text="Full text",
                    content_source="test",
                )

                output = build_rss(store.feed_items(limit=10))
                root = ET.fromstring(output)
                categories = [category.text for category in root.findall(".//category")]
                self.assertEqual(categories, ["Asia"])

    def test_pending_articles_are_sorted_by_normalized_published_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.upsert_feed_item(
                    FeedItem(
                        title="Later",
                        link="https://www.economist.com/finance/2026/06/23/later",
                        guid="later",
                        published="Tue, 23 Jun 2026 09:00:00 +0000",
                    )
                )
                store.upsert_feed_item(
                    FeedItem(
                        title="Earlier",
                        link="https://www.economist.com/finance/2026/06/22/earlier",
                        guid="earlier",
                        published="Mon, 22 Jun 2026 23:00:00 +0000",
                    )
                )

                pending = store.pending_articles(
                    limit=10,
                    retry_failed_after_seconds=1,
                    exclude_url_patterns=[],
                )

                self.assertEqual([article.title for article in pending], ["Later", "Earlier"])

    def test_pending_articles_respects_zero_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                store.upsert_feed_item(
                    FeedItem(
                        title="Story",
                        link="https://www.economist.com/finance/2026/06/23/story",
                        guid="story",
                    )
                )

                self.assertEqual(
                    store.pending_articles(
                        limit=0,
                        retry_failed_after_seconds=1,
                        exclude_url_patterns=[],
                    ),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
