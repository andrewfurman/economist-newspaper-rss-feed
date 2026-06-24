import unittest

from economist_rss.browser import _article_from_rendered_text, minimum_text_length_for_url


class RenderedBrowserArticleTests(unittest.TestCase):
    def test_builds_article_from_rendered_text(self):
        paragraphs = "\n".join(
            f"Paragraph {index} has enough subscriber article text to prove extraction works."
            for index in range(18)
        )
        rendered_text = f"""
        Asia | A peaceful revolution
        Smartphones and AI are remaking rural India
        Villages have fallen in love with short-form videos and chatbots
        Save
        Share
        Jun 23rd 2026
        |
        6 min read
        Listen to this story
        ai narrated
        I
        t is a warm afternoon in Nagepur.
        {paragraphs}
        """

        article = _article_from_rendered_text(
            "Smartphones and AI are remaking rural India", rendered_text
        )

        self.assertIsNotNone(article)
        assert article is not None
        self.assertEqual(article.method, "rendered-browser-text")
        self.assertIn("It is a warm afternoon", article.text)
        self.assertNotIn("Save", article.text)
        self.assertNotIn("Listen to this story", article.text)

    def test_uses_shorter_threshold_for_indicator_pages(self):
        self.assertEqual(
            minimum_text_length_for_url(
                "https://www.economist.com/economic-and-financial-indicators/"
                "2026/06/18/economic-data-commodities-and-markets"
            ),
            100,
        )

    def test_keeps_long_threshold_for_standard_articles(self):
        self.assertEqual(
            minimum_text_length_for_url(
                "https://www.economist.com/finance-and-economics/2026/06/18/"
                "a-standard-article"
            ),
            700,
        )


if __name__ == "__main__":
    unittest.main()
