from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any

from .config import AppConfig
from .extract import ArticleContent, extract_article, is_cloudflare_challenge


@dataclass(frozen=True)
class BrowserResult:
    ok: bool
    status: str
    message: str
    url: str
    final_url: str
    article: ArticleContent | None = None


def fetch_article_with_browser(url: str, config: AppConfig) -> BrowserResult:
    sync_playwright = _sync_playwright()
    storage_state = Path(config.browser_storage_state)
    user_data_dir = Path(config.browser_user_data_dir)

    with sync_playwright() as playwright:
        context = None
        browser = None
        try:
            launch_options: dict[str, Any] = {
                "headless": config.browser_headless,
                "args": [
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            if os.environ.get("ECONOMIST_BROWSER_NO_SANDBOX", "true").lower() == "true":
                launch_options["args"].append("--no-sandbox")
            if config.browser_executable_path:
                launch_options["executable_path"] = config.browser_executable_path
            elif config.browser_channel:
                launch_options["channel"] = config.browser_channel

            if user_data_dir:
                user_data_dir.mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    str(user_data_dir),
                    **launch_options,
                    viewport={"width": 1365, "height": 900},
                    locale="en-US",
                    timezone_id=os.environ.get("TZ", "America/Los_Angeles"),
                )
            else:
                browser = playwright.chromium.launch(**launch_options)
                context_options: dict[str, Any] = {
                    "viewport": {"width": 1365, "height": 900},
                    "locale": "en-US",
                    "timezone_id": os.environ.get("TZ", "America/Los_Angeles"),
                }
                if storage_state.exists():
                    context_options["storage_state"] = str(storage_state)
                context = browser.new_context(**context_options)

            page = context.new_page()
            page.set_default_timeout(45_000)
            page.set_default_navigation_timeout(45_000)
            response = page.goto(url, wait_until="domcontentloaded")
            _wait_for_load_settled(page, timeout=5_000)
            page.wait_for_timeout(config.browser_wait_ms)
            html = page.content()
            final_url = page.url
            if storage_state:
                storage_state.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(storage_state))

            if is_cloudflare_challenge(html):
                return BrowserResult(
                    ok=False,
                    status="cloudflare_challenge",
                    message="The browser fetch returned a Cloudflare challenge page.",
                    url=url,
                    final_url=final_url,
                )

            article = extract_article(html)
            if article is None or len(article.text) < 700:
                http_status = response.status if response else 0
                return BrowserResult(
                    ok=False,
                    status="excerpt_or_login_required",
                    message=f"Only short article text was visible after browser fetch (HTTP {http_status}).",
                    url=url,
                    final_url=final_url,
                    article=article,
                )

            return BrowserResult(
                ok=True,
                status="ok",
                message="Fetched full article text with authenticated browser.",
                url=url,
                final_url=final_url,
                article=article,
            )
        except Exception as exc:  # noqa: BLE001 - Playwright wraps browser failures broadly.
            return BrowserResult(
                ok=False,
                status="browser_fetch_failed",
                message=str(exc),
                url=url,
                final_url=url,
            )
        finally:
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()


def authenticate_browser(config: AppConfig) -> BrowserResult:
    email = os.environ.get("ECONOMIST_EMAIL", "")
    password = os.environ.get("ECONOMIST_PASSWORD", "")
    if not email or not password:
        return BrowserResult(
            ok=False,
            status="missing_credentials",
            message="Set ECONOMIST_EMAIL and ECONOMIST_PASSWORD in real.env or the environment.",
            url=config.login_url,
            final_url=config.login_url,
        )

    sync_playwright = _sync_playwright()
    user_data_dir = Path(config.browser_user_data_dir)
    storage_state = Path(config.browser_storage_state)

    with sync_playwright() as playwright:
        context = None
        try:
            user_data_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=config.browser_headless,
                channel=config.browser_channel or None,
                executable_path=config.browser_executable_path or None,
                viewport={"width": 1365, "height": 900},
                locale="en-US",
                timezone_id=os.environ.get("TZ", "America/Los_Angeles"),
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--no-sandbox",
                ],
            )
            page = context.new_page()
            page.set_default_timeout(60_000)
            page.set_default_navigation_timeout(60_000)
            page.goto(config.login_url, wait_until="domcontentloaded")
            _wait_for_load_settled(page, timeout=5_000)
            _dismiss_cookie_prompts(page)
            _fill_login_form(page, email=email, password=password)
            result = _verify_login(page, config)
            storage_state.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_state))
            return result
        except Exception as exc:  # noqa: BLE001
            return BrowserResult(
                ok=False,
                status="economist_auth_failed",
                message=str(exc),
                url=config.login_url,
                final_url=config.login_url,
            )
        finally:
            if context is not None:
                context.close()


def _sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install with: python -m pip install -e '.[browser]'"
        ) from exc
    return sync_playwright


def _fill_login_form(page: Any, *, email: str, password: str) -> None:
    email_input = page.locator(
        'input[type="email"], input[name="username"], input[type="text"][name*="user" i], '
        'input[name*="email" i], input[id*="email" i], input[autocomplete="username"]'
    ).first
    email_input.wait_for(state="visible", timeout=60_000)
    email_input.fill(email)
    _click_first(
        page,
        [
            'button:has-text("Continue")',
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
            'button:has-text("Next")',
            'input[type="submit"]',
        ],
    )
    password_input = page.locator(
        'input[type="password"], input[name*="password" i], input[id*="password" i], '
        'input[autocomplete="current-password"]'
    ).first
    password_input.wait_for(state="visible", timeout=60_000)
    password_input.fill(password)
    _click_first(
        page,
        [
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
            'button:has-text("Continue")',
            'input[type="submit"]',
        ],
    )
    _wait_for_load_settled(page, timeout=5_000)


def _verify_login(page: Any, config: AppConfig) -> BrowserResult:
    deadline = time.monotonic() + max(1, config.auth_wait_seconds)
    last = BrowserResult(
        ok=False,
        status="not_checked",
        message="The verification page has not been checked yet.",
        url=config.verify_url,
        final_url=config.verify_url,
    )

    while time.monotonic() < deadline:
        page.goto(config.verify_url, wait_until="domcontentloaded")
        _wait_for_load_settled(page, timeout=5_000)
        _dismiss_cookie_prompts(page)
        page.wait_for_timeout(config.browser_wait_ms)
        last = _inspect_verification_page(page, config.verify_url)
        if last.ok:
            return last
        page.wait_for_timeout(5_000)

    return last


def _inspect_verification_page(page: Any, verify_url: str) -> BrowserResult:
    html = page.content()
    article = extract_article(html)
    if is_cloudflare_challenge(html):
        return BrowserResult(
            ok=False,
            status="blocked_by_cloudflare",
            message="The verification page is still behind a Cloudflare challenge.",
            url=verify_url,
            final_url=page.url,
            article=article,
        )
    if article is None or len(article.text) < 700:
        return BrowserResult(
            ok=False,
            status="excerpt_or_login_required",
            message="The verification page did not expose full subscriber text.",
            url=verify_url,
            final_url=page.url,
            article=article,
        )
    return BrowserResult(
        ok=True,
        status="authenticated_full_text_available",
        message="Full article text appears to be available.",
        url=verify_url,
        final_url=page.url,
        article=article,
    )


def _dismiss_cookie_prompts(page: Any) -> None:
    try:
        page.evaluate(
            """
            () => {
              const buttons = Array.from(document.querySelectorAll('button'));
              const match = buttons.find((button) =>
                /^(continue|accept|accept all|i agree)$/i.test((button.innerText || '').trim())
              );
              if (match) match.click();
            }
            """
        )
    except Exception:
        pass
    _click_first(
        page,
        [
            'button:has-text("Accept all")',
            'button:has-text("Accept All")',
            'button:has-text("Accept")',
            'button:has-text("I agree")',
            'button:has-text("Continue")',
        ],
        optional=True,
    )


def _click_first(page: Any, selectors: list[str], *, optional: bool = False) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count() == 0:
            continue
        try:
            locator.click(timeout=5_000)
            return True
        except Exception:
            continue
    if optional:
        return False
    raise RuntimeError(f"Could not click any selector: {', '.join(selectors)}")


def _wait_for_load_settled(page: Any, *, timeout: int) -> None:
    try:
        page.wait_for_load_state("load", timeout=timeout)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
