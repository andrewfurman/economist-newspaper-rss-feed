from datetime import date, datetime, timezone
import unittest

from economist_rss.fetch import FetchResponse
from economist_rss.issue import (
    _latest_issue_date,
    parse_weekly_edition_articles,
    resolve_current_issue,
)


class IssueResolverTests(unittest.TestCase):
    def test_parse_weekly_edition_articles_extracts_article_links(self):
        html = """
        <html>
          <body>
            <a href="/weeklyedition/2026-06-27">Weekly edition</a>
            <a href="/leaders/2026/06/26/how-to-deal-with-the-ai-backlash">
              How to deal with the AI backlash
            </a>
            <a href="https://www.economist.com/business/2026/06/25/story?utm_source=x">
              Business story
            </a>
            <a href="/topics/artificial-intelligence">AI topic</a>
            <a href="/leaders/2026/06/26/how-to-deal-with-the-ai-backlash">
              Duplicate
            </a>
          </body>
        </html>
        """

        articles = parse_weekly_edition_articles(
            html,
            "https://www.economist.com/weeklyedition/2026-06-27",
        )

        self.assertEqual(
            [(article.title, article.url) for article in articles],
            [
                (
                    "How to deal with the AI backlash",
                    (
                        "https://www.economist.com/leaders/2026/06/26/"
                        "how-to-deal-with-the-ai-backlash"
                    ),
                ),
                (
                    "Business story",
                    "https://www.economist.com/business/2026/06/25/story",
                ),
            ],
        )

    def test_latest_issue_date_prefers_archive_with_lookahead(self):
        self.assertEqual(
            _latest_issue_date(
                [date(2026, 6, 20), date(2026, 6, 27)],
                now=datetime(2026, 6, 25, tzinfo=timezone.utc),
                lookahead_days=2,
            ),
            date(2026, 6, 27),
        )

    def test_latest_issue_date_falls_back_to_latest_saturday(self):
        self.assertEqual(
            _latest_issue_date(
                [],
                now=datetime(2026, 6, 29, tzinfo=timezone.utc),
                lookahead_days=2,
            ),
            date(2026, 6, 27),
        )

    def test_resolve_current_issue_reads_archive_and_issue_page(self):
        class FakeFetcher:
            def fetch_text(self, url):
                if url == "https://www.economist.com/weeklyedition/archive":
                    return FetchResponse(
                        url=url,
                        status=200,
                        text=(
                            '<a href="/weeklyedition/2026-06-20">June 20</a>'
                            '<a href="/weeklyedition/2026-06-27">June 27</a>'
                        ),
                        content_type="text/html",
                        headers={},
                    )
                if url == "https://www.economist.com/weeklyedition/2026-06-27":
                    return FetchResponse(
                        url=url,
                        status=200,
                        text=(
                            '<a href="/leaders/2026/06/26/story">'
                            "Issue story</a>"
                        ),
                        content_type="text/html",
                        headers={},
                    )
                raise AssertionError(f"unexpected URL: {url}")

        issue = resolve_current_issue(
            FakeFetcher(),
            now=datetime(2026, 6, 29, tzinfo=timezone.utc),
        )

        self.assertEqual(issue.issue_id, "2026-06-27")
        self.assertEqual(issue.source, "weeklyedition_page")
        self.assertEqual(len(issue.articles), 1)
        self.assertEqual(issue.articles[0].title, "Issue story")


if __name__ == "__main__":
    unittest.main()
