import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from economist_rss.feed import FeedItem
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
