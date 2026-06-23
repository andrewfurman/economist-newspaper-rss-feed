import unittest

from economist_rss.browser import _article_from_rendered_text


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


if __name__ == "__main__":
    unittest.main()
