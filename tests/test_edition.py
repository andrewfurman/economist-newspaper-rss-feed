import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from economist_rss.feed import EDITION_NS, FeedItem, build_rss, parse_feed, classify_edition
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

    def test_missing_issue_membership_requires_positive_print_evidence(self):
        self.assertEqual(classify_edition(None), "unknown")
        self.assertEqual(classify_edition("  ", "An article about the print edition."), "unknown")
        self.assertEqual(classify_edition(None, "This article did not appear in the print edition."), "unknown")
        self.assertEqual(classify_edition("2026-09-19"), "print_edition")
        self.assertEqual(classify_edition(None, "Body.\n\nThis article appeared in the The world this week section of the print edition under the headline “Politics”"), "print_edition")
        self.assertEqual(classify_edition(None, "This article appeared in the print edition."), "print_edition")

    def test_rss_api_and_search_agree_when_issue_discovery_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            with ArticleStore(Path(directory) / "articles.sqlite3") as store:
                store.set_state("current_issue_error", "HTTP 403")
                store.set_state("current_issue_article_count", "0")
                for guid, text in (
                    ("politics", "Cached text.\n\nThis article appeared in the The world this week section of the print edition under the headline “Politics”"),
                    ("unverified", "Cached text without edition metadata."),
                ):
                    article = store.upsert_feed_item(FeedItem(
                        title=guid, link=f"https://www.economist.com/the-world-this-week/2026/09/17/{guid}", guid=guid,
                    ))
                    store.save_article_content(article, content_html="<p>Cached text</p>", content_text=text, content_source="test")
                expected = {"politics": "print_edition", "unverified": "unknown"}
                for items in (store.feed_items(), store.search_items()):
                    self.assertEqual({i.guid: i.edition_kind for i in items}, expected)
                    root = ET.fromstring(build_rss(items))
                    self.assertEqual({i.findtext("guid"): i.findtext(f"{{{EDITION_NS}}}edition_kind") for i in root.findall("./channel/item")}, expected)
                for guid, kind in expected.items():
                    article = store.get_article(guid)
                    item = _article_api_item(article, match_source="local_full_text")
                    self.assertEqual(item["edition_kind"], kind)
                    self.assertIsNone(item["issue_id"])

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

