from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from .fetch import FetchError, Fetcher
from .util import canonical_url


WEEKLY_EDITION_BASE_URL = "https://www.economist.com/weeklyedition"
WEEKLY_EDITION_DATE_RE = re.compile(r"/weeklyedition/(\d{4}-\d{2}-\d{2})(?:[/?#]|$)")
DATE_PATH_RE = re.compile(r"/\d{4}/\d{2}/\d{2}/")


@dataclass(frozen=True)
class IssueArticle:
    title: str
    url: str


@dataclass(frozen=True)
class CurrentIssue:
    issue_id: str
    issue_date: str
    issue_url: str
    articles: list[IssueArticle]
    source: str
    error: str = ""


def resolve_current_issue(
    fetcher: Fetcher,
    *,
    base_url: str = WEEKLY_EDITION_BASE_URL,
    lookahead_days: int = 2,
    now: datetime | None = None,
) -> CurrentIssue:
    base_url = base_url.rstrip("/")
    resolved_now = now or datetime.now(timezone.utc)
    archive_dates, archive_error = _issue_dates_from_archive(fetcher, base_url)
    issue_date = _latest_issue_date(
        archive_dates,
        now=resolved_now,
        lookahead_days=lookahead_days,
    )
    issue_id = issue_date.isoformat()
    issue_url = f"{base_url}/{issue_id}"

    try:
        response = fetcher.fetch_text(issue_url)
    except FetchError as exc:
        source = "weeklyedition_calendar_fallback"
        error = str(exc)
        if archive_error:
            error = f"{archive_error}; {error}"
        return CurrentIssue(
            issue_id=issue_id,
            issue_date=issue_id,
            issue_url=issue_url,
            articles=[],
            source=source,
            error=error,
        )

    articles = parse_weekly_edition_articles(response.text, issue_url)
    return CurrentIssue(
        issue_id=issue_id,
        issue_date=issue_id,
        issue_url=issue_url,
        articles=articles,
        source="weeklyedition_page",
        error=archive_error,
    )


def parse_weekly_edition_articles(html: str, issue_url: str) -> list[IssueArticle]:
    parser = _LinkParser(issue_url)
    parser.feed(html)
    articles: list[IssueArticle] = []
    seen_urls: set[str] = set()
    for href, text in parser.links:
        article_url = _article_url(href, issue_url)
        if not article_url:
            continue
        if article_url in seen_urls:
            continue
        seen_urls.add(article_url)
        articles.append(
            IssueArticle(
                title=_clean_title(text) or "Untitled",
                url=article_url,
            )
        )
    return articles


def _issue_dates_from_archive(fetcher: Fetcher, base_url: str) -> tuple[list[date], str]:
    try:
        response = fetcher.fetch_text(f"{base_url}/archive")
    except FetchError as exc:
        return [], str(exc)

    dates: list[date] = []
    for match in WEEKLY_EDITION_DATE_RE.finditer(response.text):
        try:
            dates.append(date.fromisoformat(match.group(1)))
        except ValueError:
            continue
    return dates, ""


def _latest_issue_date(
    archive_dates: list[date],
    *,
    now: datetime,
    lookahead_days: int,
) -> date:
    today = now.astimezone(timezone.utc).date()
    latest_allowed = today + timedelta(days=max(0, lookahead_days))
    eligible = [issue_date for issue_date in archive_dates if issue_date <= latest_allowed]
    if eligible:
        return max(eligible)
    return _latest_saturday(today)


def _latest_saturday(today: date) -> date:
    days_since_saturday = (today.weekday() - 5) % 7
    return today - timedelta(days=days_since_saturday)


def _article_url(href: str, issue_url: str) -> str:
    absolute = urljoin(issue_url, href)
    parts = urlsplit(absolute)
    if parts.netloc not in {"www.economist.com", "economist.com"}:
        return ""
    if parts.path.startswith("/weeklyedition"):
        return ""
    if not DATE_PATH_RE.search(parts.path):
        return ""
    return canonical_url(absolute)


def _clean_title(text: str) -> str:
    return " ".join(unescape(text).split())


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._stack: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        attrs_by_name = {name.casefold(): value for name, value in attrs}
        href = attrs_by_name.get("href")
        if href:
            self._stack.append((href, []))

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        self._stack[-1][1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or not self._stack:
            return
        href, text_parts = self._stack.pop()
        self.links.append((href, "".join(text_parts)))
