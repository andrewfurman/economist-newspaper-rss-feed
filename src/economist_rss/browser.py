from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from pathlib import Path
import re
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
    user_data_dir = Path(config.browser_user_data_dir) if config.browser_user_data_dir else None

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

            if user_data_dir is not None:
                user_data_dir.mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    str(user_data_dir),
                    **launch_options,
                    viewport={"width": 1365, "height": 900},
                    locale="en-US",
                    timezone_id=os.environ.get("TZ", "America/Los_Angeles"),
                )
                _close_existing_pages(context)
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

            http_status = response.status if response else 0
            if http_status in {403, 429}:
                return BrowserResult(
                    ok=False,
                    status="rate_limited",
                    message=f"The browser fetch returned HTTP {http_status}.",
                    url=url,
                    final_url=final_url,
                )

            article = extract_article(html)
            if article is None or len(article.text) < 700:
                article = _extract_rendered_article(page)
            if article is None or len(article.text) < 700:
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


def authenticate_browser(config: AppConfig, *, manual_login: bool = False) -> BrowserResult:
    email = os.environ.get("ECONOMIST_EMAIL", "")
    password = os.environ.get("ECONOMIST_PASSWORD", "")
    if not manual_login and (not email or not password):
        return BrowserResult(
            ok=False,
            status="missing_credentials",
            message="Set ECONOMIST_EMAIL and ECONOMIST_PASSWORD in real.env or the environment.",
            url=config.login_url,
            final_url=config.login_url,
        )

    sync_playwright = _sync_playwright()
    user_data_dir = Path(config.browser_user_data_dir) if config.browser_user_data_dir else None
    storage_state = Path(config.browser_storage_state)

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
                    "--no-sandbox",
                ],
            }
            if config.browser_executable_path:
                launch_options["executable_path"] = config.browser_executable_path
            elif config.browser_channel:
                launch_options["channel"] = config.browser_channel

            if user_data_dir is not None:
                user_data_dir.mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    str(user_data_dir),
                    **launch_options,
                    viewport={"width": 1365, "height": 900},
                    locale="en-US",
                    timezone_id=os.environ.get("TZ", "America/Los_Angeles"),
                )
                _close_existing_pages(context)
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
            page.set_default_timeout(60_000)
            page.set_default_navigation_timeout(60_000)
            page.goto(config.verify_url if manual_login else config.login_url, wait_until="domcontentloaded")
            _wait_for_load_settled(page, timeout=5_000)
            _dismiss_cookie_prompts(page)
            if not manual_login:
                _fill_login_form(page, email=email, password=password, login_url=config.login_url)
                result = _verify_login(page, config)
            else:
                result = _verify_manual_login(page, config)
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
            if browser is not None:
                browser.close()


def _sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install with: python -m pip install -e '.[browser]'"
        ) from exc
    return sync_playwright


def _close_existing_pages(context: Any) -> None:
    for page in list(context.pages):
        try:
            page.close()
        except Exception:
            pass


EMAIL_INPUT_SELECTORS = [
    'input[type="email"]',
    'input[name="username"]',
    'input[type="text"][name*="user" i]',
    'input[name*="email" i]',
    'input[id*="email" i]',
    'input[autocomplete="username"]',
]

PASSWORD_INPUT_SELECTORS = [
    'input[type="password"]',
    'input[name*="password" i]',
    'input[id*="password" i]',
    'input[autocomplete="current-password"]',
]


def _fill_login_form(page: Any, *, email: str, password: str, login_url: str) -> None:
    email_input = _wait_for_labeled_input(
        page, "Email address", EMAIL_INPUT_SELECTORS, timeout=5_000
    )
    if email_input is None:
        _click_first(
            page,
            [
                'a[href*="/api/auth/login"]',
                'a:has-text("Log in")',
                'button:has-text("Log in")',
                'a:has-text("Sign in")',
                'button:has-text("Sign in")',
            ],
            optional=True,
        )
        _wait_for_load_settled(page, timeout=5_000)
        email_input = _wait_for_labeled_input(
            page, "Email address", EMAIL_INPUT_SELECTORS, timeout=10_000
        )

    if email_input is None and getattr(page, "url", "") != login_url:
        page.goto(login_url, wait_until="domcontentloaded")
        _wait_for_load_settled(page, timeout=5_000)
        _dismiss_cookie_prompts(page)
        email_input = _wait_for_labeled_input(
            page, "Email address", EMAIL_INPUT_SELECTORS, timeout=60_000
        )

    if email_input is None:
        raise RuntimeError("Could not find a visible Economist email input.")

    email_input.fill(email)
    password_input = _wait_for_labeled_input(
        page, "Password", PASSWORD_INPUT_SELECTORS, timeout=3_000
    )
    if password_input is None:
        _click_form_button(
            page,
            ["Continue", "Log in", "Sign in", "Next"],
            [
                'button:has-text("Continue")',
                'button:has-text("Log in")',
                'button:has-text("Sign in")',
                'button:has-text("Next")',
                'input[type="submit"]',
            ],
        )
        password_input = _wait_for_labeled_input(
            page, "Password", PASSWORD_INPUT_SELECTORS, timeout=60_000
        )
    if password_input is None:
        raise RuntimeError("Could not find a visible Economist password input.")

    password_input.fill(password)
    _click_form_button(
        page,
        ["Log in", "Sign in", "Continue"],
        [
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
            'button:has-text("Continue")',
            'input[type="submit"]',
        ],
    )
    _wait_for_load_settled(page, timeout=5_000)


def _wait_for_labeled_input(
    page: Any, label: str, selectors: list[str], *, timeout: int
) -> Any | None:
    try:
        locator = page.get_by_label(label).first
        locator.wait_for(state="visible", timeout=timeout)
        return locator
    except Exception:
        return _wait_for_first_visible(page, selectors, timeout=timeout)


def _wait_for_first_visible(page: Any, selectors: list[str], *, timeout: int) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout)
            return locator
        except Exception:
            continue
    return None


def _click_form_button(
    page: Any, button_names: list[str], selectors: list[str], *, optional: bool = False
) -> bool:
    for name in button_names:
        locator = page.get_by_role("button", name=name, exact=True).first
        try:
            locator.click(timeout=5_000)
            return True
        except Exception:
            continue
    return _click_first(page, selectors, optional=optional)


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
        try:
            page.goto(config.verify_url, wait_until="domcontentloaded")
        except Exception as exc:
            last = BrowserResult(
                ok=False,
                status="verification_navigation_failed",
                message=str(exc),
                url=config.verify_url,
                final_url=getattr(page, "url", config.verify_url),
            )
            page.wait_for_timeout(5_000)
            continue
        _wait_for_load_settled(page, timeout=5_000)
        _dismiss_cookie_prompts(page)
        page.wait_for_timeout(config.browser_wait_ms)
        last = _inspect_verification_page(page, config.verify_url)
        if last.ok:
            return last
        page.wait_for_timeout(5_000)

    return last


def _verify_manual_login(page: Any, config: AppConfig) -> BrowserResult:
    deadline = time.monotonic() + max(1, config.auth_wait_seconds)
    last = BrowserResult(
        ok=False,
        status="not_checked",
        message="Waiting for manual Economist login in the visible browser.",
        url=config.verify_url,
        final_url=getattr(page, "url", config.verify_url),
    )
    navigated_to_verify_at = 0.0

    while time.monotonic() < deadline:
        _wait_for_load_settled(page, timeout=3_000)
        _dismiss_cookie_prompts(page)
        page.wait_for_timeout(config.browser_wait_ms)
        last = _inspect_verification_page(page, config.verify_url)
        if last.ok:
            return last

        current_url = getattr(page, "url", "")
        lower_url = current_url.lower()
        on_login_flow = any(
            part in lower_url for part in ("login", "account", "auth", "signin", "sign-in")
        )
        if not on_login_flow and time.monotonic() - navigated_to_verify_at > 45:
            try:
                page.goto(config.verify_url, wait_until="domcontentloaded")
                navigated_to_verify_at = time.monotonic()
            except Exception as exc:
                last = BrowserResult(
                    ok=False,
                    status="verification_navigation_failed",
                    message=str(exc),
                    url=config.verify_url,
                    final_url=getattr(page, "url", config.verify_url),
                )
        page.wait_for_timeout(5_000)

    return last


def _inspect_verification_page(page: Any, verify_url: str) -> BrowserResult:
    html = page.content()
    article = extract_article(html)
    if article is None or len(article.text) < 700:
        article = _extract_rendered_article(page)
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


def _extract_rendered_article(page: Any) -> ArticleContent | None:
    title = _inner_text(page, "article h1, main h1, h1")
    for selector in (
        "article",
        "main",
        '[data-testid*="article" i]',
        '[class*="article" i]',
    ):
        rendered_text = _inner_text(page, selector)
        article = _article_from_rendered_text(title, rendered_text)
        if article is not None and len(article.text) >= 700:
            return article
    return None


def _inner_text(page: Any, selector: str) -> str:
    try:
        return page.locator(selector).first.inner_text(timeout=5_000)
    except Exception:
        return ""


def _article_from_rendered_text(title: str | None, rendered_text: str) -> ArticleContent | None:
    lines = _clean_rendered_lines(rendered_text)
    if not lines:
        return None
    article_title = _squash_space(title or "")
    if not article_title:
        article_title = next((line for line in lines[:4] if len(line.split()) >= 4), "")

    blocks: list[tuple[str, str]] = []
    title_seen = False
    for line in _merge_drop_caps(lines):
        if _is_rendered_boilerplate(line):
            continue
        tag = "p"
        if article_title and _squash_space(line) == article_title and not title_seen:
            tag = "h2"
            title_seen = True
        blocks.append((tag, line))

    text_lines = [line for _, line in blocks]
    if len(" ".join(text_lines).split()) < 80:
        return None

    content_html = "\n".join(
        f"<{tag}>{escape(line)}</{tag}>" for tag, line in blocks if line.strip()
    )
    return ArticleContent(
        title=article_title or None,
        content_html=content_html,
        text="\n\n".join(text_lines),
        method="rendered-browser-text",
    )


def _clean_rendered_lines(rendered_text: str) -> list[str]:
    seen: set[str] = set()
    lines: list[str] = []
    for raw_line in rendered_text.splitlines():
        line = _squash_space(raw_line)
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def _merge_drop_caps(lines: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if (
            len(line) == 1
            and line.isalpha()
            and next_line
            and next_line[0].islower()
        ):
            merged.append(f"{line}{next_line}")
            index += 2
            continue
        merged.append(line)
        index += 1
    return merged


def _is_rendered_boilerplate(value: str) -> bool:
    normalized = _squash_space(value).lower()
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    if compact in {
        "save",
        "share",
        "advertisement",
        "listentothisstory",
        "ainarrated",
        "reusegivefeedback",
        "reuse",
        "givefeedback",
    }:
        return True
    if normalized == "|":
        return True
    if re.fullmatch(r"\d+\s+min\s+read", normalized):
        return True
    if re.fullmatch(r"[a-z]{3}\s+\d{1,2}(st|nd|rd|th)\s+\d{4}", normalized):
        return True
    if re.fullmatch(r"(image|photograph|photo|source|chart|map):.*", normalized):
        return True
    return False


def _squash_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
