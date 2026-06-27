import unittest

from economist_rss.browser import (
    _article_from_rendered_text,
    minimum_text_length_for_url,
    minimum_word_count_for_url,
)


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
        self.assertEqual(
            minimum_word_count_for_url(
                "https://www.economist.com/economic-and-financial-indicators/"
                "2026/06/18/economic-data-commodities-and-markets"
            ),
            20,
        )

    def test_keeps_long_threshold_for_standard_articles(self):
        self.assertEqual(
            minimum_text_length_for_url(
                "https://www.economist.com/finance-and-economics/2026/06/18/"
                "a-standard-article"
            ),
            700,
        )
        self.assertEqual(
            minimum_word_count_for_url(
                "https://www.economist.com/finance-and-economics/2026/06/18/"
                "a-standard-article"
            ),
            80,
        )

    def test_rendered_short_indicator_text_can_be_extracted_with_lower_word_count(self):
        rendered_text = """
        Economic data, commodities and markets
        This week's economic data contain short table-like market notes.
        GDP
        Inflation
        Interest rates
        Commodities
        Markets
        America
        China
        Euro area
        The latest figures are compact but still useful as a data item.
        """

        article = _article_from_rendered_text(
            "Economic data, commodities and markets",
            rendered_text,
            minimum_word_count=20,
        )

        self.assertIsNotNone(article)
        assert article is not None
        self.assertEqual(article.method, "rendered-browser-text")
        self.assertIn("economic data", article.text.lower())

    def test_rendered_short_text_still_fails_standard_article_word_count(self):
        rendered_text = """
        Economic data, commodities and markets
        This week's economic data contain short table-like market notes.
        GDP
        Inflation
        Interest rates
        Commodities
        Markets
        """

        article = _article_from_rendered_text(
            "Economic data, commodities and markets",
            rendered_text,
        )

        self.assertIsNone(article)


if __name__ == "__main__":
    unittest.main()
