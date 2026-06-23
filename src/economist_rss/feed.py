from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from html import unescape
import xml.etree.ElementTree as ET


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
    return FeedItem(
        title=unescape(title),
        link=link,
        guid=guid,
        published=_child_text(item, "pubDate"),
        summary=summary,
        content_html=content,
        source=source_name,
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
    return FeedItem(
        title=unescape(title),
        link=link,
        guid=guid,
        published=published,
        summary=summary,
        content_html=content,
        source=source_name,
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
