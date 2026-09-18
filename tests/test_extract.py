import json
import unittest

from economist_rss.extract import extract_article, is_cloudflare_challenge, looks_like_paywall_excerpt


class ExtractArticleTests(unittest.TestCase):
    def test_extracts_json_ld_article_body(self):
        body = " ".join(["This is sentence one."] * 90)
        html = f"""
        <html>
          <head>
            <script type="application/ld+json">
              {json.dumps({"@type": "NewsArticle", "headline": "A headline", "articleBody": body})}
            </script>
          </head>
          <body></body>
        </html>
        """

        article = extract_article(html)

        self.assertIsNotNone(article)
        assert article is not None
        self.assertEqual(article.title, "A headline")
        self.assertEqual(article.method, "json-ld")
        self.assertIn("<p>", article.content_html)

    def test_extracts_article_markup(self):
        paragraphs = "\n".join(
            f"<p>Paragraph {index} has enough useful article text to count as content.</p>"
            for index in range(20)
        )
        html = f"""
        <html>
          <head><title>Fallback title</title></head>
          <body>
            <nav><p>Navigation should be ignored.</p></nav>
            <article>
              <h1>Visible article title</h1>
              {paragraphs}
            </article>
          </body>
        </html>
        """

        article = extract_article(html)

        self.assertIsNotNone(article)
        assert article is not None
        self.assertEqual(article.title, "Visible article title")
        self.assertEqual(article.method, "article-html")
        self.assertNotIn("Navigation should be ignored", article.text)

    def test_falls_back_to_meta_description(self):
        html = """
        <html>
          <head>
            <title>Short page</title>
            <meta name="description" content="A short article preview.">
          </head>
          <body><p>Too short.</p></body>
        </html>
        """

        article = extract_article(html)

        self.assertIsNotNone(article)
        assert article is not None
        self.assertEqual(article.title, "Short page")
        self.assertEqual(article.method, "meta-description")

    def test_cloudflare_detector_ignores_non_challenge_mentions(self):
        html = """
        <html>
          <head><script src="/cdn-cgi/rum/cloudflareinsights.js"></script></head>
          <body><h1>Normal page</h1></body>
        </html>
        """

        self.assertFalse(is_cloudflare_challenge(html))

    def test_cloudflare_detector_matches_human_verification(self):
        html = "<html><body>Verify you are human before continuing.</body></html>"

        self.assertTrue(is_cloudflare_challenge(html))


if __name__ == "__main__":
    unittest.main()


class PaywallExcerptTests(unittest.TestCase):
    def test_detects_free_trial_cta(self):
        text = (
            "Asia | Inside West Bengal\n\nA buffer zone has become a miserable limbo\n\n"
            "kolkata\n\nA makeshift checkpoint holds up bikes.\n\n"
            "Already have an account? Log in\n\nContinue with a free trial\n\n"
            "Get full access to our independent journalism for free\n\n"
            "Or create a free account to unlock just this article\n\nCreate account"
        )
        self.assertTrue(looks_like_paywall_excerpt(text))

    def test_allows_normal_subscriber_article(self):
        paragraphs = " ".join(
            f"Paragraph {i} explains the policy shift with enough detail for a full feature."
            for i in range(40)
        )
        self.assertFalse(looks_like_paywall_excerpt(paragraphs))
