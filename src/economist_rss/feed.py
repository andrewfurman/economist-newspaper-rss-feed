from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from html import unescape
import xml.etree.ElementTree as ET
from urllib.parse import urlparse


CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS = "http://www.w3.org/2005/Atom"

ET.register_namespace("content", CONTENT_NS)


@dataclass
class FeedItem:
    title: str
    link: str
    guid: str
    published: str | None = None
    summary: str | None = None
    content_html: str | None = None
    source: str | None = None
    categories: list[str] = field(default_factory=list)


def parse_feed(xml_text: str, source_name: str) -> list[FeedItem]:
    root = ET.fromstring(xml_text)
    if _local_name(root.tag) == "rss":
        return _parse_rss(root, source_name)
    if _local_name(root.tag) == "feed":
        return _parse_atom(root, source_name)

    rss_items = root.findall(".//item")
    if rss_items:
        return [_rss_item(item, source_name) for item in rss_items]
    atom_entries = root.findall(f".//{{{ATOM_NS}}}entry")
    return [_atom_entry(entry, source_name) for entry in atom_entries]


def build_rss(
    items: list[FeedItem],
    *,
    title: str = "The Economist full-text private feed",
    link: str = "https://www.economist.com/",
    description: str = "Private RSS feed generated from authorized article fetches.",
) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = link
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc), usegmt=True
    )
    ET.SubElement(channel, "generator").text = "economist-newspaper-rss-feed"

    for feed_item in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = feed_item.title
        ET.SubElement(item, "link").text = feed_item.link
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = feed_item.guid
        if feed_item.published:
            ET.SubElement(item, "pubDate").text = feed_item.published
        if feed_item.summary:
            ET.SubElement(item, "description").text = feed_item.summary
        if feed_item.source:
            ET.SubElement(item, "source").text = feed_item.source
        for category in categories_for_item(feed_item):
            ET.SubElement(item, "category").text = category
        if feed_item.content_html:
            ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = feed_item.content_html

    xml_body = ET.tostring(rss, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n"


def _parse_rss(root: ET.Element, source_name: str) -> list[FeedItem]:
    channel = root.find("channel")
    if channel is None:
        return []
    return [_rss_item(item, source_name) for item in channel.findall("item")]


def _rss_item(item: ET.Element, source_name: str) -> FeedItem:
    title = _child_text(item, "title") or "Untitled"
    link = _child_text(item, "link") or ""
    guid = _child_text(item, "guid") or link or title
    summary = _child_text(item, "description")
    content = _child_text_by_local_name(item, "encoded")
    categories = [
        text
        for category in item.findall("category")
        if (text := (category.text or "").strip())
    ]
    return FeedItem(
        title=unescape(title),
        link=link,
        guid=guid,
        published=_child_text(item, "pubDate"),
        summary=summary,
        content_html=content,
        source=source_name,
        categories=categories,
    )


def _parse_atom(root: ET.Element, source_name: str) -> list[FeedItem]:
    return [_atom_entry(entry, source_name) for entry in root.findall(f"{{{ATOM_NS}}}entry")]


def _atom_entry(entry: ET.Element, source_name: str) -> FeedItem:
    title = _child_text_ns(entry, "title", ATOM_NS) or "Untitled"
    link = _atom_link(entry)
    guid = _child_text_ns(entry, "id", ATOM_NS) or link or title
    summary = _child_text_ns(entry, "summary", ATOM_NS)
    content = _child_text_ns(entry, "content", ATOM_NS)
    published = _child_text_ns(entry, "published", ATOM_NS) or _child_text_ns(
        entry, "updated", ATOM_NS
    )
    categories = [
        category.attrib.get("label") or category.attrib.get("term", "")
        for category in entry.findall(f"{{{ATOM_NS}}}category")
    ]
    return FeedItem(
        title=unescape(title),
        link=link,
        guid=guid,
        published=published,
        summary=summary,
        content_html=content,
        source=source_name,
        categories=[category.strip() for category in categories if category.strip()],
    )


def _atom_link(entry: ET.Element) -> str:
    links = entry.findall(f"{{{ATOM_NS}}}link")
    if not links:
        return ""
    for link in links:
        if link.attrib.get("rel", "alternate") == "alternate" and link.attrib.get("href"):
            return link.attrib["href"]
    return links[0].attrib.get("href", "")


def _child_text(parent: ET.Element, tag: str) -> str | None:
    child = parent.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _child_text_ns(parent: ET.Element, tag: str, namespace: str) -> str | None:
    child = parent.find(f"{{{namespace}}}{tag}")
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _child_text_by_local_name(parent: ET.Element, local_name: str) -> str | None:
    for child in parent:
        if _local_name(child.tag) == local_name and child.text:
            return child.text.strip()
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


SECTION_CATEGORIES = {
    "1843": "1843",
    "asia": "Asia",
    "briefing": "Briefing",
    "britain": "Britain",
    "business": "Business",
    "by-invitation": "By Invitation",
    "china": "China",
    "culture": "Culture",
    "economic-and-financial-indicators": "Economic and Financial Indicators",
    "essay": "Essay",
    "europe": "Europe",
    "finance-and-economics": "Finance and Economics",
    "graphic-detail": "Graphic Detail",
    "in-brief": "In Brief",
    "international": "International",
    "leaders": "Leaders",
    "letters": "Letters",
    "middle-east-and-africa": "Middle East and Africa",
    "obituary": "Obituary",
    "podcasts": "Podcasts",
    "science-and-technology": "Science and Technology",
    "special-report": "Special Report",
    "the-americas": "The Americas",
    "the-world-in-brief": "The World in Brief",
    "the-world-this-week": "The World This Week",
    "united-states": "United States",
}

FORMAT_CATEGORIES = {
    "audio": "Audio",
    "interactive": "Interactive",
}


def categories_for_item(item: FeedItem) -> list[str]:
    categories = list(item.categories)
    categories.extend(categories_for_url(item.link))
    return _unique_nonempty(categories)


def categories_for_url(url: str) -> list[str]:
    path_parts = [
        part
        for part in urlparse(url).path.split("/")
        if part and not part.isdigit()
    ]
    if not path_parts:
        return []

    categories: list[str] = []
    first = path_parts[0]
    if first in FORMAT_CATEGORIES:
        if len(path_parts) > 1 and path_parts[1] in SECTION_CATEGORIES:
            categories.append(SECTION_CATEGORIES[path_parts[1]])
        categories.append(FORMAT_CATEGORIES[first])
    elif first in SECTION_CATEGORIES:
        categories.append(SECTION_CATEGORIES[first])
    return _unique_nonempty(categories)


def _unique_nonempty(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
