from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib
from typing import Any


DEFAULT_USER_AGENT = (
    "economist-newspaper-rss-feed/0.1 "
    "(+https://github.com/andrewfurman/economist-newspaper-rss-feed)"
)


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    limit: int | None = None


@dataclass(frozen=True)
class AppConfig:
    feeds: list[FeedConfig] = field(default_factory=list)
    output_path: str = "dist/economist-fulltext.xml"
    database_path: str = "data/economist-rss.sqlite3"
    timeout_seconds: float = 20.0
    refresh_interval_seconds: float = 7200.0
    min_article_delay_seconds: float = 75.0
    max_article_delay_seconds: float = 180.0
    max_articles_per_refresh: int = 12
    retry_failed_after_seconds: float = 21600.0
    user_agent: str = DEFAULT_USER_AGENT
    browser_fetch_enabled: bool = False
    browser_headless: bool = True
    browser_channel: str = "chrome"
    browser_executable_path: str = ""
    browser_wait_ms: int = 3000
    auth_wait_seconds: int = 600
    browser_user_data_dir: str = ".cache/economist-browser-profile"
    browser_storage_state: str = ".cache/economist-browser-state.json"
    login_url: str = "https://www.economist.com/api/auth/login"
    verify_url: str = (
        "https://www.economist.com/culture/2026/06/19/"
        "plot-twist-newsletter-the-art-of-adolescence"
    )
    exclude_url_patterns: list[str] = field(
        default_factory=lambda: ["/podcasts/", "/audio-edition-podcast/"]
    )


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    feeds = [_read_feed(feed) for feed in raw.get("feeds", [])]
    browser_enabled = _bool_value(raw, "browser_fetch_enabled", False)
    env_browser_enabled = os.environ.get("ECONOMIST_BROWSER_FETCH_ENABLED")
    if env_browser_enabled is not None:
        browser_enabled = _parse_bool(env_browser_enabled, default=browser_enabled)

    return AppConfig(
        feeds=feeds,
        output_path=_string_value(raw, "output_path", "dist/economist-fulltext.xml"),
        database_path=_string_value(raw, "database_path", "data/economist-rss.sqlite3"),
        timeout_seconds=_float_value(raw, "timeout_seconds", 20.0),
        refresh_interval_seconds=_float_value(raw, "refresh_interval_seconds", 7200.0),
        min_article_delay_seconds=_float_value(raw, "min_article_delay_seconds", 75.0),
        max_article_delay_seconds=_float_value(raw, "max_article_delay_seconds", 180.0),
        max_articles_per_refresh=_int_value(raw, "max_articles_per_refresh", 12),
        retry_failed_after_seconds=_float_value(raw, "retry_failed_after_seconds", 21600.0),
        user_agent=_string_value(raw, "user_agent", DEFAULT_USER_AGENT),
        browser_fetch_enabled=browser_enabled,
        browser_headless=_parse_bool(
            os.environ.get("ECONOMIST_BROWSER_HEADLESS", ""),
            default=_bool_value(raw, "browser_headless", True),
        ),
        browser_channel=os.environ.get(
            "ECONOMIST_BROWSER_CHANNEL",
            _string_value(raw, "browser_channel", "chrome"),
        ),
        browser_executable_path=os.environ.get(
            "ECONOMIST_BROWSER_EXECUTABLE_PATH",
            _string_value(raw, "browser_executable_path", ""),
        ),
        browser_wait_ms=_int_value(raw, "browser_wait_ms", 3000),
        auth_wait_seconds=_int_value(raw, "auth_wait_seconds", 600),
        browser_user_data_dir=os.environ.get(
            "ECONOMIST_BROWSER_USER_DATA_DIR",
            _string_value(raw, "browser_user_data_dir", ".cache/economist-browser-profile"),
        ),
        browser_storage_state=os.environ.get(
            "ECONOMIST_BROWSER_STORAGE_STATE",
            _string_value(raw, "browser_storage_state", ".cache/economist-browser-state.json"),
        ),
        login_url=_string_value(raw, "login_url", "https://www.economist.com/api/auth/login"),
        verify_url=_string_value(
            raw,
            "verify_url",
            "https://www.economist.com/culture/2026/06/19/"
            "plot-twist-newsletter-the-art-of-adolescence",
        ),
        exclude_url_patterns=_string_list_value(
            raw, "exclude_url_patterns", ["/podcasts/", "/audio-edition-podcast/"]
        ),
    )


def _read_feed(raw: Any) -> FeedConfig:
    if not isinstance(raw, dict):
        raise ValueError("Each [[feeds]] entry must be a table.")

    name = raw.get("name")
    url = raw.get("url")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Each feed needs a non-empty name.")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"Feed {name!r} needs a non-empty url.")

    limit = raw.get("limit")
    if limit is not None and not isinstance(limit, int):
        raise ValueError(f"Feed {name!r} limit must be an integer.")
    return FeedConfig(name=name.strip(), url=url.strip(), limit=limit)


def _string_value(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value


def _float_value(raw: dict[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number.")
    return float(value)


def _int_value(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _bool_value(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean.")
    return value


def _string_list_value(raw: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = raw.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")
    return value


def _parse_bool(value: str, *, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
