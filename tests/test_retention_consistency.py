import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from economist_rss.cli import main
from economist_rss.config import AppConfig, FeedConfig
from economist_rss.feed import FeedItem
from economist_rss.server import _api_stats_response, _rss_response
from economist_rss.store import ArticleStore


class RetentionConsistencyTests(unittest.TestCase):
    def test_cli_http_and_stats_agree_for_known_missing_and_invalid_issue(self):
        for issue_date in ("2026-06-27", "", "not-a-date"):
            with self.subTest(issue_date=issue_date), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "feed.xml"
                config = AppConfig(
                    feeds=[FeedConfig(name="test", url="https://example.test/rss")],
                    database_path=str(Path(directory) / "articles.sqlite3"),
                    output_path=str(output), article_lookback_days=1,
                )
                with ArticleStore(config.database_path) as store:
                    store.set_state("current_issue_id", "2026-06-27")
                    store.set_state("current_issue_date", issue_date)
                    store.set_state("current_issue_article_count", "1")
                    old = store.upsert_current_issue_article(
                        FeedItem(title="Issue member", link="https://example.test/old", guid="old",
                                 published="Fri, 01 Jan 1999 12:00:00 +0000"),
                        issue_id="2026-06-27", issue_date="2026-06-27", issue_source="test",
                    )
                    recent = store.upsert_feed_item(FeedItem(
                        title="Recent online article", link="https://example.test/recent", guid="recent",
                        published=format_datetime(datetime.now(timezone.utc) - timedelta(hours=1)),
                    ))
                    for article in (old, recent):
                        store.save_article_content(article, content_html="<p>Cached</p>",
                                                   content_text="Cached", content_source="test")
                with patch("economist_rss.cli.load_config", return_value=config), patch("economist_rss.cli.refresh_if_stale") as refresh:
                    self.assertEqual(main(["build", "--no-refresh"]), 0)
                    refresh.assert_not_called()
                with patch("economist_rss.server.refresh_if_stale"):
                    served = _rss_response(config, "")
                expected = {"old", "recent"} if issue_date == "2026-06-27" else {"recent"}
                for xml in (output.read_text(), served):
                    self.assertEqual({node.text for node in ET.fromstring(xml).findall("./channel/item/guid")}, expected)
                stats = _api_stats_response(config)
                self.assertEqual(stats["refresh"]["default_feed_article_count"], len(expected))
