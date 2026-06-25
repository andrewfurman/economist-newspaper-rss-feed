import unittest
import xml.etree.ElementTree as ET

from economist_rss.feed import (
    CONTENT_NS,
    DESCRIPTION_PREVIEW_CHARS,
    FeedItem,
    build_rss,
    categories_for_item,
    categories_for_title,
    categories_for_url,
    category_for_slug,
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

    def test_build_rss_omits_full_html_and_source(self):
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
        source = root.find(".//source")

        self.assertIsNone(encoded)
        self.assertIsNone(source)

    def test_build_rss_truncates_description_preview(self):
        output = build_rss(
            [
                FeedItem(
                    title="Story",
                    link="https://example.com/story",
                    guid="story-1",
                    summary=" ".join(["word"] * 100),
                )
            ]
        )

        root = ET.fromstring(output)
        description = root.find("./channel/item/description")

        self.assertIsNotNone(description)
        assert description is not None
        self.assertLessEqual(len(description.text or ""), DESCRIPTION_PREVIEW_CHARS)
        self.assertTrue((description.text or "").endswith("..."))

    def test_build_rss_uses_full_text_description_for_world_in_brief(self):
        full_text = "\n".join(
            [
                "World item one has a full plain-text paragraph.",
                "",
                "World item two should also remain visible in the feed.",
            ]
        )
        output = build_rss(
            [
                FeedItem(
                    title="The world in brief",
                    link="https://www.economist.com/the-world-in-brief/2026/06/24/id",
                    guid="world-in-brief",
                    summary="Short preview",
                    content_html="<p>Not used</p>",
                    content_text=full_text,
                )
            ]
        )

        root = ET.fromstring(output)
        description = root.find("./channel/item/description")
        link = root.find("./channel/item/link")
        encoded = root.find(f".//{{{CONTENT_NS}}}encoded")

        self.assertIsNone(encoded)
        self.assertIsNone(link)
        self.assertIsNotNone(description)
        assert description is not None
        self.assertEqual(description.text, full_text)

    def test_build_rss_uses_full_text_description_for_us_in_brief(self):
        full_text = " ".join(["United States briefing item."] * 40)
        output = build_rss(
            [
                FeedItem(
                    title="The US in Brief: A big night",
                    link=(
                        "https://www.economist.com/in-brief/2026/06/24/"
                        "the-us-in-brief-a-big-night"
                    ),
                    guid="us-in-brief",
                    summary="Short preview",
                    content_text=full_text,
                )
            ]
        )

        root = ET.fromstring(output)
        description = root.find("./channel/item/description")
        link = root.find("./channel/item/link")

        self.assertIsNone(link)
        self.assertIsNotNone(description)
        assert description is not None
        self.assertEqual(description.text, full_text)
        self.assertGreater(len(description.text or ""), DESCRIPTION_PREVIEW_CHARS)

    def test_build_rss_keeps_regular_articles_as_summary_preview(self):
        full_text = " ".join(["Regular article full text."] * 40)
        output = build_rss(
            [
                FeedItem(
                    title="A normal United States story",
                    link="https://www.economist.com/united-states/2026/06/24/story",
                    guid="regular-us-story",
                    summary="Short preview",
                    content_text=full_text,
                )
            ]
        )

        root = ET.fromstring(output)
        description = root.find("./channel/item/description")
        link = root.find("./channel/item/link")

        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(
            link.text,
            "https://www.economist.com/united-states/2026/06/24/story",
        )
        self.assertIsNotNone(description)
        assert description is not None
        self.assertEqual(description.text, "Short preview")

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

    def test_us_in_brief_adds_united_states_category(self):
        item = FeedItem(
            title="The US in Brief: A big night for Zohran Mamdani",
            link=(
                "https://www.economist.com/in-brief/2026/06/24/"
                "the-us-in-brief-a-big-night-for-zohran-mamdani"
            ),
            guid="story-1",
        )

        self.assertEqual(categories_for_item(item), ["In Brief", "United States"])

    def test_world_in_brief_title_adds_world_in_brief_category(self):
        self.assertEqual(
            categories_for_title("The World in Brief"),
            ["The World in Brief"],
        )

    def test_title_categories_accept_common_in_brief_variants(self):
        self.assertEqual(
            categories_for_title("United States in Brief: Primary night"),
            ["United States"],
        )
        self.assertEqual(
            categories_for_title("World in Brief: Wednesday update"),
            ["The World in Brief"],
        )

    def test_category_for_slug_uses_known_section_names(self):
        self.assertEqual(category_for_slug("united-states"), "United States")
        self.assertEqual(
            category_for_slug("science-and-technology"),
            "Science and Technology",
        )
        self.assertEqual(category_for_slug("the-world-in-brief"), "The World in Brief")

    def test_category_for_slug_accepts_in_brief_aliases(self):
        self.assertEqual(category_for_slug("us-in-brief"), "United States")
        self.assertEqual(category_for_slug("world-in-brief"), "The World in Brief")

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

    def test_categories_for_numeric_section_url(self):
        self.assertEqual(
            categories_for_url(
                "https://www.economist.com/1843/2026/06/05/nike-cant-just-do-it"
            ),
            ["1843"],
        )


if __name__ == "__main__":
    unittest.main()
