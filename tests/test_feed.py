import unittest
import xml.etree.ElementTree as ET

from economist_rss.feed import CONTENT_NS, FeedItem, build_rss, parse_feed


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
            </item>
          </channel>
        </rss>
        """

        items = parse_feed(xml, "Example")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Story")
        self.assertEqual(items[0].link, "https://example.com/story")
        self.assertEqual(items[0].source, "Example")

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


if __name__ == "__main__":
    unittest.main()
