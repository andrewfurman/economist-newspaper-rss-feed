from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import parse_qs, urlencode, urlparse


def article_link_key(lookup_key: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        ("article-text:v1:" + lookup_key).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def article_text_url(
    link: str, guid: str, *, base_url: str = "", signing_key: str = ""
) -> str:
    lookup_key = link.strip() or guid.strip()
    parameters = {"url" if link.strip() else "guid": lookup_key}
    if signing_key:
        parameters["key"] = article_link_key(lookup_key, signing_key)
    return f"{base_url.rstrip('/')}/article.txt?{urlencode(parameters)}"


def unwrap_article_url(value: str) -> str:
    """Accept the single nested text URL used by existing feed consumers."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return value
    if parsed.path.rstrip("/").endswith("/article.txt"):
        parameters = parse_qs(parsed.query)
        for name in ("url", "link", "guid"):
            for candidate in parameters.get(name, []):
                if candidate.strip():
                    return candidate.strip()
    return value


def public_base_url(headers=None) -> str:
    """Prefer an explicit public address over reverse-proxy request metadata."""
    configured = os.environ.get("ECONOMIST_PUBLIC_BASE_URL", "").strip()
    if configured:
        parsed = urlparse(configured)
        parsed.port  # Validate malformed or out-of-range ports.
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in configured
            or any(character.isspace() for character in configured)
        ):
            raise ValueError("ECONOMIST_PUBLIC_BASE_URL must be an HTTP(S) base URL")
        return configured.rstrip("/")
    if headers is None:
        return ""
    host = headers.get("Host", "").strip()
    parsed = urlparse(f"//{host}")
    parsed.port  # Validate malformed or out-of-range ports.
    if (
        not host
        or not parsed.hostname
        or parsed.netloc != host
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in host
        or any(character.isspace() for character in host)
    ):
        raise ValueError("Invalid Host header")
    scheme = headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip()
    if scheme not in {"http", "https"}:
        raise ValueError("Invalid forwarded protocol")
    return f"{scheme}://{host}"
