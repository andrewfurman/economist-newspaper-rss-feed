import unittest
import xml.etree.ElementTree as ET

from economist_rss.feed import (
    CONTENT_NS,
    FeedItem,
    build_rss,
    categories_for_url,
    parse_feed,
)


class FeedTests(unittest.TestCase):
    def test_parse_rss_feed(self):
        xml = """
        <rss version="2.0">
          <channel>
            <title>Example</title>
            <item>
              <title>Story</title>
              <link>https://example.com/story</link>
              <guid>story-1</guid>
              <description>Summary</description>
              <category>Business</category>
            </item>
          </channel>
        </rss>
        """

        items = parse_feed(xml, "Example")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Story")
        self.assertEqual(items[0].link, "https://example.com/story")
        self.assertEqual(items[0].source, "Example")
        self.assertEqual(items[0].categories, ["Business"])

    def test_build_rss_includes_content_encoded(self):
        output = build_rss(
            [
                FeedItem(
                    title="Story",
                    link="https://example.com/story",
                    guid="story-1",
                    summary="Summary",
                    content_html="<p>Full text</p>",
                    source="Example",
                )
            ]
        )

        root = ET.fromstring(output)
        encoded = root.find(f".//{{{CONTENT_NS}}}encoded")

        self.assertIsNotNone(encoded)
        assert encoded is not None
        self.assertEqual(encoded.text, "<p>Full text</p>")

    def test_build_rss_includes_section_category_from_url(self):
        output = build_rss(
            [
                FeedItem(
                    title="Story",
                    link=(
                        "https://www.economist.com/finance-and-economics/"
                        "2026/06/23/story"
                    ),
                    guid="story-1",
                    content_html="<p>Full text</p>",
                )
            ]
        )

        root = ET.fromstring(output)
        category = root.find(".//category")

        self.assertIsNotNone(category)
        assert category is not None
        self.assertEqual(category.text, "Finance and Economics")

    def test_build_rss_preserves_explicit_categories(self):
        output = build_rss(
            [
                FeedItem(
                    title="Story",
                    link="https://www.economist.com/business/2026/06/23/story",
                    guid="story-1",
                    categories=["Companies", "Business"],
                )
            ]
        )

        root = ET.fromstring(output)
        categories = [category.text for category in root.findall(".//category")]

        self.assertEqual(categories, ["Companies", "Business"])

    def test_categories_for_interactive_section_url(self):
        self.assertEqual(
            categories_for_url(
                "https://www.economist.com/interactive/europe/2026/06/23/story"
            ),
            ["Europe", "Interactive"],
        )

    def test_categories_for_world_in_brief_url(self):
        self.assertEqual(
            categories_for_url(
                "https://www.economist.com/the-world-in-brief/2026/06/24/id"
            ),
            ["The World in Brief"],
        )

    def test_categories_for_essay_url(self):
        self.assertEqual(
            categories_for_url(
                "https://www.economist.com/essay/2026/06/24/why-a-topic-matters"
            ),
            ["Essay"],
        )


if __name__ == "__main__":
    unittest.main()
