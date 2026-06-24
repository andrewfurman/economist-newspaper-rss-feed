from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

from .browser import authenticate_browser
from .config import AppConfig, FeedConfig, load_config
from .env import load_env_file
from .feed import build_rss
from .refresh import refresh_if_stale
from .server import EconomistRssServer
from .store import ArticleStore
from .util import cutoff_datetime


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "build"

    if args.env_file:
        load_env_file(args.env_file)
    _configure_logging()

    try:
        config = load_config(args.config)
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.feed_url:
        config = _with_cli_feeds(config, args.feed_url, args.limit)
    if args.headed or args.auth_wait_seconds is not None:
        config = _with_browser_overrides(
            config,
            headed=args.headed,
            auth_wait_seconds=args.auth_wait_seconds,
        )

    if args.command == "auth":
        result = authenticate_browser(config, manual_login=args.manual_login)
        print(f"{result.status}: {result.message}", file=sys.stderr)
        return 0 if result.ok else 1

    if args.command == "refresh":
        if not config.feeds:
            print("No feeds configured. Add feeds.toml or pass --feed-url.", file=sys.stderr)
            return 2
        summary = refresh_if_stale(
            config,
            force=args.force,
            ignore_refresh_interval=args.ignore_refresh_interval,
        )
        _print_summary(summary)
        return 0 if summary.status in {"ok", "skipped"} else 1

    if args.command == "serve":
        host = args.host or os.environ.get("ECONOMIST_HOST", "127.0.0.1")
        port = int(args.port or os.environ.get("ECONOMIST_PORT", "8080"))
        print(f"Serving Economist RSS on http://{host}:{port}/rss.xml", file=sys.stderr)
        EconomistRssServer(config, host=host, port=port).serve_forever()
        return 0

    if args.command != "build":
        parser.error(f"unknown command: {args.command}")

    if not config.feeds:
        print("No feeds configured. Add feeds.toml or pass --feed-url.", file=sys.stderr)
        return 2

    output_path = Path(args.output or config.output_path)
    if not args.no_refresh:
        summary = refresh_if_stale(
            config,
            force=args.force,
            ignore_refresh_interval=args.ignore_refresh_interval,
        )
        _print_summary(summary)

    with ArticleStore(config.database_path) as store:
        items = store.feed_items(
            limit=args.output_limit
            if args.output_limit is not None
            else config.rss_item_limit,
            published_after=cutoff_datetime(config.article_lookback_days),
        )

    if not items:
        print("No full-text feed items are cached yet.", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_rss(items), encoding="utf-8")
    print(f"Wrote {len(items)} cached full-text items to {output_path}", file=sys.stderr)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a private full-text RSS feed from authorized article fetches."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["auth", "refresh", "build", "serve"],
        help="Command to run. Defaults to build.",
    )
    parser.add_argument(
        "--config",
        default="feeds.toml",
        help="Path to TOML config. Defaults to feeds.toml.",
    )
    parser.add_argument(
        "--env-file",
        default="real.env",
        help="Optional env file to load before running. Defaults to real.env.",
    )
    parser.add_argument(
        "--feed-url",
        action="append",
        default=[],
        help="RSS/Atom feed URL to include. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum items per CLI-provided feed URL.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output RSS path. Defaults to output_path from config.",
    )
    parser.add_argument(
        "--output-limit",
        type=int,
        default=None,
        help=(
            "Maximum cached full-text articles to include in generated RSS. "
            "Defaults to rss_item_limit from the config."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh even if refresh_interval_seconds has not elapsed.",
    )
    parser.add_argument(
        "--ignore-refresh-interval",
        action="store_true",
        help=(
            "Bypass only the cache-age interval. Failed article retry backoff still "
            "applies. Intended for the scheduled systemd refresh timer."
        ),
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Build RSS from cache without checking upstream feeds.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host for serve command. Defaults to ECONOMIST_HOST or 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for serve command. Defaults to ECONOMIST_PORT or 8080.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser auth/fetch with a visible browser window.",
    )
    parser.add_argument(
        "--auth-wait-seconds",
        type=int,
        default=None,
        help="How long auth should wait for subscriber full-text verification.",
    )
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="Open a visible browser and wait while you complete Economist login manually.",
    )
    return parser


def _configured_feeds(
    config: AppConfig,
    cli_feed_urls: list[str],
    cli_limit: int | None,
) -> list[FeedConfig]:
    feeds = list(config.feeds)
    feeds.extend(
        FeedConfig(name=f"Feed {index}", url=url, limit=cli_limit)
        for index, url in enumerate(cli_feed_urls, start=1)
    )
    return feeds


def _with_cli_feeds(config: AppConfig, feed_urls: list[str], limit: int | None) -> AppConfig:
    return AppConfig(
        feeds=_configured_feeds(config, feed_urls, limit),
        output_path=config.output_path,
        database_path=config.database_path,
        rss_item_limit=config.rss_item_limit,
        timeout_seconds=config.timeout_seconds,
        refresh_interval_seconds=config.refresh_interval_seconds,
        article_lookback_days=config.article_lookback_days,
        min_article_delay_seconds=config.min_article_delay_seconds,
        max_article_delay_seconds=config.max_article_delay_seconds,
        max_articles_per_refresh=config.max_articles_per_refresh,
        retry_failed_after_seconds=config.retry_failed_after_seconds,
        user_agent=config.user_agent,
        browser_fetch_enabled=config.browser_fetch_enabled,
        browser_headless=config.browser_headless,
        browser_channel=config.browser_channel,
        browser_executable_path=config.browser_executable_path,
        browser_wait_ms=config.browser_wait_ms,
        browser_fetch_timeout_seconds=config.browser_fetch_timeout_seconds,
        auth_wait_seconds=config.auth_wait_seconds,
        browser_user_data_dir=config.browser_user_data_dir,
        browser_storage_state=config.browser_storage_state,
        login_url=config.login_url,
        verify_url=config.verify_url,
        exclude_url_patterns=config.exclude_url_patterns,
        world_in_brief_enabled=config.world_in_brief_enabled,
        world_in_brief_url=config.world_in_brief_url,
        world_in_brief_refresh_interval_seconds=(
            config.world_in_brief_refresh_interval_seconds
        ),
    )


def _with_browser_overrides(
    config: AppConfig,
    *,
    headed: bool = False,
    auth_wait_seconds: int | None = None,
) -> AppConfig:
    return AppConfig(
        feeds=config.feeds,
        output_path=config.output_path,
        database_path=config.database_path,
        rss_item_limit=config.rss_item_limit,
        timeout_seconds=config.timeout_seconds,
        refresh_interval_seconds=config.refresh_interval_seconds,
        article_lookback_days=config.article_lookback_days,
        min_article_delay_seconds=config.min_article_delay_seconds,
        max_article_delay_seconds=config.max_article_delay_seconds,
        max_articles_per_refresh=config.max_articles_per_refresh,
        retry_failed_after_seconds=config.retry_failed_after_seconds,
        user_agent=config.user_agent,
        browser_fetch_enabled=config.browser_fetch_enabled,
        browser_headless=False if headed else config.browser_headless,
        browser_channel=config.browser_channel,
        browser_executable_path=config.browser_executable_path,
        browser_wait_ms=config.browser_wait_ms,
        browser_fetch_timeout_seconds=config.browser_fetch_timeout_seconds,
        auth_wait_seconds=auth_wait_seconds or config.auth_wait_seconds,
        browser_user_data_dir=config.browser_user_data_dir,
        browser_storage_state=config.browser_storage_state,
        login_url=config.login_url,
        verify_url=config.verify_url,
        exclude_url_patterns=config.exclude_url_patterns,
        world_in_brief_enabled=config.world_in_brief_enabled,
        world_in_brief_url=config.world_in_brief_url,
        world_in_brief_refresh_interval_seconds=(
            config.world_in_brief_refresh_interval_seconds
        ),
    )


def _print_summary(summary: object) -> None:
    print(
        (
            f"Refresh {summary.status}: feeds={summary.feeds_checked}, "
            f"items={summary.feed_items_seen}, fetched={summary.articles_fetched}, "
            f"failed={summary.articles_failed}"
            f"{f', stopped={summary.stop_reason}' if summary.stop_reason else ''}"
        ),
        file=sys.stderr,
    )


def _configure_logging() -> None:
    level_name = os.environ.get("ECONOMIST_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
