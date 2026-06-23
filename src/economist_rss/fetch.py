from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.request


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    text: str
    content_type: str | None
    headers: Message


class FetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        body_preview: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.body_preview = body_preview


class Fetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        cookie_header: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.user_agent = user_agent
        self.cookie_header = cookie_header
        self.timeout_seconds = timeout_seconds

    def fetch_text(self, url: str) -> FetchResponse:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": self.user_agent,
        }
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                headers_message = response.headers
                charset = _charset(headers_message) or "utf-8"
                text = body.decode(charset, errors="replace")
                return FetchResponse(
                    url=response.geturl(),
                    status=response.status,
                    text=text,
                    content_type=headers_message.get("Content-Type"),
                    headers=headers_message,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
            raise FetchError(
                f"HTTP {exc.code} while fetching {url}",
                status_code=exc.code,
                retry_after_seconds=retry_after,
                body_preview=body[:500],
            ) from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"Network error while fetching {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise FetchError(f"Timed out fetching {url}") from exc


def _charset(headers: Message) -> str | None:
    content_type = headers.get_content_type()
    if not content_type:
        return None
    return headers.get_param("charset")


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    from datetime import datetime, timezone

    return max(0, int((retry_at - datetime.now(timezone.utc)).total_seconds()))
