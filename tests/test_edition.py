import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from economist_rss.feed import EDITION_NS, FeedItem, build_rss, parse_feed
from economist_rss.server import _article_api_item
from economist_rss.store import ArticleStore, StoredArticle


class EditionLabelTests(unittest.TestCase):
    def test_rss_print_metadata_is_separate_from_title_and_sections(self):
        output = build_rss(
            [
                FeedItem(
                    title="Leaders: A timely warning",
                    link="https://www.economist.com/leaders/2026/08/22/timely-warning",
                    guid="leaders-2026-08-22",
                    published="Sat, 22 Aug 2026 00:00:00 +0000",
                    content_html="<p>Full text</p>",
                    edition_kind="print_edition",
                    issue_id="2026-08-22",
                    issue_date="2026-08-22",
                )
            ]
        )
        root = ET.fromstring(output)
        title = root.find("./channel/item/title")
        categories = [category.text for category in root.findall(".//category")]
        assert title is not None
        self.assertEqual(title.text, "Leaders: A timely warning")
        self.assertEqual(categories, ["Leaders"])
        self.assertEqual(root.findtext(f"./channel/item/{{{EDITION_NS}}}edition_kind"), "print_edition")
        parsed = parse_feed(output, "test")[0]
        self.assertEqual((parsed.edition_kind, parsed.issue_id, parsed.issue_date),
                         ("print_edition", "2026-08-22", "2026-08-22"))

    def test_rss_online_metadata_is_separate_from_title_and_sections(self):
        output = build_rss(
            [
                FeedItem(
                    title="Online-only analysis",
                    link="https://www.economist.com/asia/2026/08/23/online-only",
                    guid="online-only-2026-08-23",
                    published="Sun, 23 Aug 2026 00:00:00 +0000",
                    content_html="<p>Full text</p>",
                    # Explicitly mark as online-only for RSS labeling
                    edition_kind="online_only",
                    categories=["Online Only", "Asia"],
                )
            ]
        )
        root = ET.fromstring(output)
        title = root.find("./channel/item/title")
        categories = [category.text for category in root.findall(".//category")]
        assert title is not None
        self.assertEqual(title.text, "Online-only analysis")
        self.assertEqual(categories, ["Asia"])
        self.assertEqual(root.findtext(f"./channel/item/{{{EDITION_NS}}}edition_kind"), "online_only")

    def test_api_item_includes_edition_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "articles.sqlite3"
            with ArticleStore(database_path) as store:
                stamped = store.upsert_issue_article(
                    FeedItem(
                        title="Issue story",
                        link="https://www.economist.com/asia/2026/08/22/issue-story",
                        guid="issue-story",
                        published="Sat, 22 Aug 2026 00:00:00 +0000",
                        content_html="<p>Full text</p>",
                        content_text="Full text",
                    ),
                    issue_id="2026-08-22",
                    issue_date="2026-08-22",
                    issue_source="test",
                )
                item = _article_api_item(stamped, match_source="local_full_text")
                self.assertEqual(item["issue_id"], "2026-08-22")
                self.assertEqual(item["issue_date"], "2026-08-22")
                self.assertEqual(item["edition_kind"], "print_edition")


if __name__ == "__main__":
    unittest.main()

