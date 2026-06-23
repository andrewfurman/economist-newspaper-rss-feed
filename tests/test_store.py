import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
