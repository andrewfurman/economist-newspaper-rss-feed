import os
import unittest

from economist_rss.server import _authorized


class ServerAuthTests(unittest.TestCase):
    def test_allows_when_token_is_not_configured(self):
        old_value = os.environ.pop("ECONOMIST_FEED_TOKEN", None)
        try:
            self.assertTrue(_authorized("", "", "ECONOMIST_FEED_TOKEN"))
        finally:
            if old_value is not None:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value

    def test_allows_bearer_token(self):
        old_value = os.environ.get("ECONOMIST_FEED_TOKEN")
        os.environ["ECONOMIST_FEED_TOKEN"] = "secret-token"
        try:
            self.assertTrue(
                _authorized("Bearer secret-token", "", "ECONOMIST_FEED_TOKEN")
            )
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_FEED_TOKEN", None)
            else:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value

    def test_allows_query_token_for_rss_readers(self):
        old_value = os.environ.get("ECONOMIST_FEED_TOKEN")
        os.environ["ECONOMIST_FEED_TOKEN"] = "secret-token"
        try:
            self.assertTrue(_authorized("", "token=secret-token", "ECONOMIST_FEED_TOKEN"))
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_FEED_TOKEN", None)
            else:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value

    def test_rejects_wrong_token(self):
        old_value = os.environ.get("ECONOMIST_FEED_TOKEN")
        os.environ["ECONOMIST_FEED_TOKEN"] = "secret-token"
        try:
            self.assertFalse(_authorized("", "token=wrong", "ECONOMIST_FEED_TOKEN"))
        finally:
            if old_value is None:
                os.environ.pop("ECONOMIST_FEED_TOKEN", None)
            else:
                os.environ["ECONOMIST_FEED_TOKEN"] = old_value


if __name__ == "__main__":
    unittest.main()
