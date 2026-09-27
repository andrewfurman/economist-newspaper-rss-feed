"""Requests never wait on a refresh once a cache exists (phone-claw#148)."""
from threading import Lock
from unittest.mock import MagicMock, patch

from economist_rss import server


def _store(last_refresh):
    store = MagicMock()
    store.get_state.return_value = last_refresh
    ctx = MagicMock()
    ctx.__enter__.return_value = store
    return ctx


def test_cold_store_refreshes_inline():
    with patch.object(server, "ArticleStore", return_value=_store(None)), \
         patch.object(server, "refresh_if_stale") as refresh, \
         patch.object(server, "Thread") as thread:
        server._refresh_for_request(MagicMock(database_path="x"), Lock())
    refresh.assert_called_once()
    thread.assert_not_called()


def test_warm_store_refreshes_in_background():
    with patch.object(server, "ArticleStore", return_value=_store("2026-09-26T10:19:09+00:00")), \
         patch.object(server, "refresh_if_stale") as refresh, \
         patch.object(server, "Thread") as thread:
        server._refresh_for_request(MagicMock(database_path="x"), Lock())
    refresh.assert_not_called()  # not inline
    thread.assert_called_once()
    assert thread.call_args.kwargs["target"] is server._background_refresh
    thread.return_value.start.assert_called_once()


def test_background_refresh_skips_when_busy_and_swallows_errors():
    lock = Lock()
    lock.acquire()
    with patch.object(server, "refresh_if_stale") as refresh:
        server._background_refresh(MagicMock(), lock)  # another refresh holds the lock
    refresh.assert_not_called()
    lock.release()
    with patch.object(server, "refresh_if_stale", side_effect=RuntimeError("boom")):
        server._background_refresh(MagicMock(), lock)  # must not raise
    assert not lock.locked()
