import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET
import re

from economist_rss.article_links import public_base_url, unwrap_article_url
from economist_rss.feed import FeedItem, build_rss


class ArticleLinksTests(unittest.TestCase):
    def test_static_feed_replaces_links_without_changing_article_identity(self):
        original = "https://www.economist.com/business/2026/09/14/story"
        item = FeedItem(title="Story", link=original, guid="stable-story")
        xml = build_rss(
            [item],
            article_base_url="https://feed.example.test/private/",
            article_signing_key="test-feed-secret",
        )
        node = ET.fromstring(xml).find("./channel/item")
        link = urlparse(node.findtext("link"))
        self.assertEqual(link.netloc, "feed.example.test")
        self.assertEqual(link.path, "/private/article.txt")
        self.assertEqual(parse_qs(link.query)["url"], [original])
        self.assertTrue(parse_qs(link.query)["key"])
        self.assertEqual(node.findtext("guid"), "stable-story")
        self.assertEqual(node.find("guid").get("isPermaLink"), "false")
        self.assertEqual(node.findtext("category"), "Business")
        self.assertEqual(item.link, original)
        self.assertNotIn("test-feed-secret", xml)

    def test_article_without_source_link_uses_guid_lookup(self):
        xml = build_rss([FeedItem(title="Story", link="", guid="id & plus+")])
        link = ET.fromstring(xml).findtext("./channel/item/link")
        self.assertEqual(urlparse(link).path, "/article.txt")
        self.assertEqual(parse_qs(urlparse(link).query), {"guid": ["id & plus+"]})

    def test_malformed_url_like_identifiers_do_not_raise(self):
        for value in ("https://[", "plain-guid", "https://example.test/story"):
            with self.subTest(value=value):
                self.assertEqual(unwrap_article_url(value), value)

    def test_public_base_rejects_unsafe_or_malformed_addresses(self):
        for address in (
            "javascript:alert(1)",
            "https://user:secret@feed.example.test",
            "https://feed.example.test?token=secret",
            "https://feed.example.test#fragment",
            "https://feed.example.test:invalid",
            "https://feed.example.test:65536",
            "https://feed.example.test\\other",
            "https://[",
        ):
            with self.subTest(address=address), patch.dict(
                os.environ, {"ECONOMIST_PUBLIC_BASE_URL": address}
            ):
                with self.assertRaises(ValueError):
                    public_base_url()

    def test_host_fallback_rejects_invalid_authorities(self):
        with patch.dict(os.environ, {"ECONOMIST_PUBLIC_BASE_URL": ""}):
            for host in (
                "", "host/path", "user@host", "host:invalid", "host:65536",
                "host\\other", "host name", "[",
            ):
                with self.subTest(host=host), self.assertRaises(ValueError):
                    public_base_url({"Host": host})
            self.assertEqual(
                public_base_url({"Host": "[::1]:8080", "X-Forwarded-Host": "ignored"}),
                "http://[::1]:8080",
            )

    def test_rss_emits_cdata_link_with_literal_amp_key(self):
        item = FeedItem(
            title="Story",
            link="https://www.economist.com/business/2026/09/14/story",
            guid="stable-story",
        )
        xml = build_rss([item], article_signing_key="test-feed-secret")
        # Raw XML should include CDATA link text with a literal &key=
        self.assertIn("<link><![CDATA[", xml)
        match = re.search(r"<item>.*?<link>(.*?)</link>", xml, re.DOTALL)
        self.assertIsNotNone(match, "link element not found in raw XML")
        assert match is not None
        raw_link_fragment = match.group(1)
        self.assertIn("&key=", raw_link_fragment)
        # Parsing the XML should yield a link whose query contains key=
        link_text = ET.fromstring(xml).findtext("./channel/item/link")
        self.assertTrue(link_text)
        assert link_text is not None
        self.assertEqual(urlparse(link_text).path, "/article.txt")
        self.assertTrue(parse_qs(urlparse(link_text).query).get("key"))


if __name__ == "__main__":
    unittest.main()
