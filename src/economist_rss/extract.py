from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import json
import re
from typing import Any


ARTICLE_TYPES = {"article", "newsarticle", "reportagenewsarticle"}
BLOCK_TAGS = {"h1", "h2", "h3", "p", "li", "blockquote"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form"}


@dataclass(frozen=True)
class ArticleContent:
    title: str | None
    content_html: str
    text: str
    method: str


@dataclass(frozen=True)
class TextBlock:
    tag: str
    text: str


def extract_article(html: str) -> ArticleContent | None:
    if is_cloudflare_challenge(html):
        return None

    metadata = MetadataParser()
    metadata.feed(html)

    from_json_ld = _extract_from_json_ld(metadata.json_ld)
    if from_json_ld is not None:
        return from_json_ld

    for target in ("article", "main", None):
        collector = BlockCollector(target)
        collector.feed(html)
        blocks = _clean_blocks(collector.blocks)
        if _word_count(block.text for block in blocks) >= 80:
            title = _title_from_blocks(blocks) or metadata.title
            body_blocks = _drop_duplicate_title(blocks, title)
            body_blocks = _strip_economist_boilerplate(body_blocks)
            content_html = _blocks_to_html(body_blocks)
            text = "\n\n".join(block.text for block in body_blocks)
            method = f"{target or 'document'}-html"
            return ArticleContent(title=title, content_html=content_html, text=text, method=method)

    if metadata.description:
        content_html = f"<p>{escape(metadata.description)}</p>"
        return ArticleContent(
            title=metadata.title,
            content_html=content_html,
            text=metadata.description,
            method="meta-description",
        )
    return None


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.description: str | None = None
        self.json_ld: list[str] = []
        self._in_title = False
        self._title_chunks: list[str] = []
        self._script_type: str | None = None
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag == "title":
            self._in_title = True
            self._title_chunks = []
        elif tag == "meta":
            key = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            content = attrs_dict.get("content", "").strip()
            if key in {"description", "og:description"} and content and not self.description:
                self.description = _squash_space(content)
            elif key in {"og:title", "twitter:title"} and content and not self.title:
                self.title = _squash_space(content)
        elif tag == "script":
            script_type = attrs_dict.get("type", "").lower()
            if "application/ld+json" in script_type:
                self._script_type = script_type
                self._script_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            title = _squash_space("".join(self._title_chunks))
            if title and not self.title:
                self.title = title
        elif tag == "script" and self._script_type is not None:
            script_text = "".join(self._script_chunks).strip()
            if script_text:
                self.json_ld.append(script_text)
            self._script_type = None
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_chunks.append(data)
        elif self._script_type is not None:
            self._script_chunks.append(data)


class BlockCollector(HTMLParser):
    def __init__(self, target_tag: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.target_tag = target_tag
        self.blocks: list[TextBlock] = []
        self._target_depth = 0
        self._document_depth = 0
        self._skip_depth = 0
        self._current_tag: str | None = None
        self._current_chunks: list[str] = []

    @property
    def _inside_target(self) -> bool:
        return self.target_tag is None or self._target_depth > 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self._document_depth += 1

        if self._skip_depth > 0:
            self._skip_depth += 1
            return
        if tag in SKIP_TAGS:
            self._skip_depth = 1
            return

        if self.target_tag is not None and tag == self.target_tag:
            self._target_depth = 1
            return
        if self._target_depth > 0:
            self._target_depth += 1

        if self._inside_target and tag in BLOCK_TAGS:
            self._flush()
            self._current_tag = tag
            self._current_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if self._inside_target and tag in BLOCK_TAGS and self._current_tag == tag:
            self._flush()

        if self._target_depth > 0:
            self._target_depth -= 1
        self._document_depth = max(0, self._document_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._inside_target and self._current_tag:
            self._current_chunks.append(data)

    def close(self) -> None:
        self._flush()
        super().close()

    def _flush(self) -> None:
        if not self._current_tag:
            return
        text = _squash_space("".join(self._current_chunks))
        if text:
            self.blocks.append(TextBlock(tag=self._current_tag, text=text))
        self._current_tag = None
        self._current_chunks = []


def _extract_from_json_ld(scripts: list[str]) -> ArticleContent | None:
    for script in scripts:
        try:
            decoded = json.loads(script)
        except json.JSONDecodeError:
            continue
        article = _find_article_object(decoded)
        if article is None:
            continue
        body = article.get("articleBody")
        if not isinstance(body, str) or _word_count([body]) < 80:
            continue
        title = article.get("headline")
        paragraphs = [_squash_space(part) for part in re.split(r"\n{2,}", body) if part.strip()]
        if len(paragraphs) == 1:
            paragraphs = _split_long_text(paragraphs[0])
        paragraphs = [line for line in paragraphs if not _is_economist_boilerplate(line)]
        content_html = "\n".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
        return ArticleContent(
            title=_squash_space(title) if isinstance(title, str) else None,
            content_html=content_html,
            text="\n\n".join(paragraphs),
            method="json-ld",
        )
    return None


def _find_article_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        raw_type = value.get("@type")
        types = {raw_type.lower()} if isinstance(raw_type, str) else set()
        if isinstance(raw_type, list):
            types = {item.lower() for item in raw_type if isinstance(item, str)}
        if ARTICLE_TYPES.intersection(types) and "articleBody" in value:
            return value
        for child in value.values():
            found = _find_article_object(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_article_object(child)
            if found is not None:
                return found
    return None


def _blocks_to_html(blocks: list[TextBlock]) -> str:
    html_blocks: list[str] = []
    for block in blocks:
        tag = "p"
        if block.tag in {"h1", "h2", "h3"}:
            tag = "h2"
        elif block.tag == "blockquote":
            tag = "blockquote"
        html_blocks.append(f"<{tag}>{escape(block.text)}</{tag}>")
    return "\n".join(html_blocks)


def _clean_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    cleaned: list[TextBlock] = []
    seen: set[str] = set()
    for block in blocks:
        text = _squash_space(block.text)
        if not text or len(text) < 2:
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(TextBlock(tag=block.tag, text=text))
    return cleaned


def _drop_duplicate_title(blocks: list[TextBlock], title: str | None) -> list[TextBlock]:
    if not title or not blocks:
        return blocks
    first = blocks[0]
    if first.tag in {"h1", "h2", "h3"} and _squash_space(first.text) == _squash_space(title):
        return blocks[1:]
    return blocks


def _strip_economist_boilerplate(blocks: list[TextBlock]) -> list[TextBlock]:
    return [block for block in blocks if not _is_economist_boilerplate(block.text)]


def _is_economist_boilerplate(value: str) -> bool:
    normalized = _squash_space(value).lower()
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    if compact in {
        "alreadyhaveanaccountlogin",
        "continuewithafreetrial",
        "freetrial",
    }:
        return True
    if re.fullmatch(
        r"(log in|sign in|subscribe|start your free trial|continue with a free trial|"
        r"free trial|share|save)",
        normalized,
    ):
        return True
    return False


def _title_from_blocks(blocks: list[TextBlock]) -> str | None:
    for block in blocks[:3]:
        if block.tag in {"h1", "h2"}:
            return block.text
    return None


def _split_long_text(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    paragraphs: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        words = sentence.split()
        current.append(sentence)
        current_words += len(words)
        if current_words >= 90:
            paragraphs.append(" ".join(current))
            current = []
            current_words = 0
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _squash_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _word_count(parts: list[str] | tuple[str, ...] | Any) -> int:
    return sum(len(str(part).split()) for part in parts)


def is_cloudflare_challenge(html: str) -> bool:
    text = html or ""
    return bool(
        re.search(r"cf-chl|cf_chl|Cloudflare|Enable JavaScript and cookies", text, re.I)
    )
