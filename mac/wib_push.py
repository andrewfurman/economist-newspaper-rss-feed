#!/usr/bin/env python3
"""Load today's World in Brief and US in Brief in this Mac's signed-in Chrome and push them.

The feed server's AWS address is blocked by economist.com (Oct 2026). This Mac loads
the page like a person would, in a background Chrome window with its own profile
(~/Library/Application Support/economist-wib/chrome-profile), then posts the page to
POST {ECONOMIST_INGEST_URL} on the feed server. It then follows the page's "Next: US in
Brief" link, as a person would, and posts that page to /api/us-in-brief/ingest. On the article runs it also pushes a few
section feeds and loads the handful of new articles the server asks for.

The profile is seeded once from the everyday Chrome profile's sign-in (--seed, over ssh:
a LaunchAgent may not read Chrome's folder under macOS privacy rules). It is kept between
runs, so Chrome keeps its session and its Cloudflare check like an everyday browser.

Config: ~/.config/economist-wib/env with ECONOMIST_INGEST_URL and ECONOMIST_INGEST_TOKEN.
Stdlib only, for the macOS system python3 (3.9).

  wib_push.py              fetch and push now (plus articles at ARTICLE_HOURS_ET)
  wib_push.py --backup     skip if another Mac pushed in the last 30 minutes
  wib_push.py --articles 4 also push feeds and up to 4 articles now
  wib_push.py --seed       copy the everyday Chrome sign-in into the job's profile, then exit
"""
import argparse
import base64
import fcntl
import random
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

WIB_URL = "https://www.economist.com/the-world-in-brief"
CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
PROFILE_FILES = ["Cookies", "Cookies-journal", "Network", "Preferences", "Local Storage"]
HOME_DIR = Path.home() / "Library/Application Support/economist-wib"
PROFILE = HOME_DIR / "chrome-profile"
ENV_FILE = Path.home() / ".config/economist-wib/env"
# Section feeds to keep fresh (must match the server's [[feeds]] URLs) and when to do it.
FEEDS = [f"https://www.economist.com/{s}/rss.xml" for s in
         ("leaders", "briefing", "united-states", "in-brief", "business", "finance-and-economics",
          "science-and-technology", "international")]
ARTICLE_HOURS_ET = {2, 14}  # the 02:50 and 14:30 runs
ARTICLES_PER_RUN = 4

# Ready when the page has redirected to the dated edition and shows several stories.
PAGE_STATE_JS = r"""JSON.stringify({
  url: location.href,
  title: document.title,
  paras: [...document.querySelectorAll('main p, article p')].filter(p => p.innerText.length > 80).length,
  challenge: /just a moment|verify you are human/i.test(document.title + ' ' + document.body.innerText.slice(0, 500)),
})"""
# The World in Brief page ends with a "Next" pager link to the day's US in Brief.
US_LINK_JS = r"""(() => {
  const links = [...document.querySelectorAll('a[href]')].filter(a =>
    /\b(US|United States) in Brief\b/i.test(a.innerText) && !/the-world-in-brief/.test(a.href));
  return links.length ? links[links.length - 1].href : null;
})()"""
# The extractor needs the JSON-LD and the article markup, not the whole app shell.
PAGE_HTML_JS = r"""(() => {
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.outerHTML).join('');
  const main = (document.querySelector('main') || document.body).cloneNode(true);
  main.querySelectorAll('audio, video, script, style, noscript').forEach(n => n.remove());
  const title = document.createElement('title'); title.textContent = document.title;
  return '<html><head>' + title.outerHTML + ld + '</head><body>' + main.outerHTML + '</body></html>';
})()"""


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def load_env():
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values["ECONOMIST_INGEST_URL"], values["ECONOMIST_INGEST_TOKEN"]


def api(url, token, payload=None):
    """HTTP via macOS curl: the system python3's LibreSSL 2.8 can't finish a TLS
    handshake with the server's Caddy. The token goes to curl on stdin, not argv."""
    with tempfile.TemporaryDirectory(prefix="economist-wib-http-") as tmp:
        config = f'header = "Authorization: Bearer {token}"\n'
        if payload is not None:
            body = Path(tmp) / "body.json"
            body.write_text(json.dumps(payload))
            config += f'header = "Content-Type: application/json"\ndata-binary = "@{body}"\n'
        result = subprocess.run(
            ["curl", "-sS", "--max-time", "60", "-K", "-", "-w", "\n%{http_code}", url],
            input=config, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"curl failed: {result.stderr.strip()}")
    text, _, status = result.stdout.rpartition("\n")
    try:
        return int(status), json.loads(text or "{}")
    except ValueError:
        return int(status), {"message": text[:200]}


class DevTools:
    """Just enough of the Chrome DevTools websocket protocol for Runtime.evaluate."""

    def __init__(self, ws_url):
        hostport, path = ws_url[len("ws://"):].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += self.sock.recv(1)
        self.next_id = 0

    def _read(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("DevTools socket closed")
            data += chunk
        return data

    def _frame(self):
        message = b""
        while True:
            first, second = self._read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            message += self._read(size)
            if first & 0x80:  # FIN
                return message

    def call_raw(self, body):
        """Send one Runtime.evaluate request (JSON text) and return its value."""
        data = body.encode()
        mask = os.urandom(4)
        header = bytes([0x81])
        if len(data) < 126:
            header += bytes([0x80 | len(data)])
        elif len(data) < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", len(data))
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", len(data))
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        wanted = json.loads(body)["id"]
        while True:
            reply = json.loads(self._frame())
            if reply.get("id") == wanted:
                result = reply["result"]
                if "exceptionDetails" in result:
                    raise RuntimeError(str(result["exceptionDetails"].get("exception", {}).get("value")
                                           or result["exceptionDetails"].get("text")))
                return result["result"].get("value")

    def evaluate(self, expression, await_promise=False):
        self.next_id += 1
        return self.call_raw(json.dumps({
            "id": self.next_id, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "awaitPromise": await_promise}}))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def seed_profile():
    """Copy the everyday Chrome profile's sign-in into the job's own profile."""
    if PROFILE.exists():
        shutil.rmtree(PROFILE)
    (PROFILE / "Default").mkdir(parents=True)
    shutil.copy2(CHROME_DIR / "Local State", PROFILE / "Local State")
    for name in PROFILE_FILES:
        source = CHROME_DIR / "Default" / name
        if source.is_dir():
            shutil.copytree(source, PROFILE / "Default" / name)
        elif source.exists():
            shutil.copy2(source, PROFILE / "Default" / name)


class Browser:
    """One background Chrome window on the job's profile, driven over DevTools."""

    def __enter__(self):
        if not (PROFILE / "Default" / "Cookies").exists():
            try:
                seed_profile()
            except PermissionError as exc:
                raise RuntimeError("no Chrome profile yet and macOS won't let this job read "
                                   "Chrome's folder; run `wib_push.py --seed` over ssh") from exc
        self.lock = open(HOME_DIR / "chrome.lock", "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)  # one Chrome per profile
        self.port = free_port()
        # Launch through LaunchServices so Chrome runs in the logged-in GUI session and can
        # read "Chrome Safe Storage" from the keychain; started directly from ssh it can't
        # decrypt the copied cookies and loads signed out.
        subprocess.run(
            ["open", "-n", "-g", "-a", "Google Chrome", "--args", f"--user-data-dir={PROFILE}",
             f"--remote-debugging-port={self.port}", "--no-first-run", "--no-default-browser-check",
             "--window-size=1280,900", "about:blank"],
            check=True)
        self.tools = None
        return self

    def __exit__(self, *exc):
        # Only this job's instance has this profile path on its command line. SIGTERM lets
        # Chrome save its cookies to the profile for the next run.
        subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], check=False)
        time.sleep(3)
        self.lock.close()

    def _connect(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=5) as response:
            pages = [t for t in json.load(response) if t.get("type") == "page"]
        self.tools = DevTools(pages[0]["webSocketDebuggerUrl"])

    def evaluate(self, expression):
        for _ in range(20):
            try:
                if self.tools is None:
                    self._connect()
                return self.tools.evaluate(expression)
            except (OSError, ValueError, IndexError, KeyError):
                self.tools = None
                time.sleep(3)
        raise RuntimeError("Chrome DevTools did not answer")

    def load(self, url, ready, timeout=120):
        """Open url and wait until ready(state); return (final_url, reduced html)."""
        self.evaluate(f"location.href = {json.dumps(url)}")
        deadline = time.monotonic() + timeout
        state = {}
        while time.monotonic() < deadline:
            time.sleep(3)
            try:
                state = json.loads(self.evaluate(PAGE_STATE_JS) or "{}")
            except (RuntimeError, ValueError):
                continue
            if ready(state):
                time.sleep(2)  # let late paragraphs render
                return state["url"], self.evaluate(PAGE_HTML_JS)
        raise RuntimeError(f"{url} did not load in {timeout}s: {state}")

    def fetch_text(self, url):
        """GET url from inside the economist.com page, with the browser's own session."""
        self.evaluate("1")  # connected
        return self.tools.evaluate(
            f"fetch({json.dumps(url)}, {{credentials: 'include'}})"
            ".then(r => r.ok ? r.text() : Promise.reject('HTTP ' + r.status))", await_promise=True)


def wib_ready(state):
    return "/the-world-in-brief/20" in state.get("url", "") and state.get("paras", 0) >= 5


def us_in_brief_url(href):
    """The pager link, if it is a plain https economist.com page; else None."""
    parsed = urlparse(href or "")
    if parsed.scheme == "https" and parsed.hostname in ("www.economist.com", "economist.com"):
        return href
    return None


def push_us_in_brief(browser, href, ingest_url, token):
    """Load the US in Brief page the pager pointed at and push it. Returns True on success.

    One extra page load, a few seconds after World in Brief. A Cloudflare check is never
    clicked or solved: the page just never becomes ready, and this logs and gives up.
    """
    url = us_in_brief_url(href)
    if not url:
        log(f"us-in-brief: no usable Next link on the World in Brief page ({href!r})")
        return False
    time.sleep(random.uniform(5, 15))
    try:
        final_url, html = browser.load(url, article_ready, timeout=90)
    except RuntimeError as exc:
        log(f"us-in-brief: {exc}")
        return False
    status, body = api(ingest_url.split("/api/")[0] + "/api/us-in-brief/ingest", token,
                       {"url": final_url, "html": html, "source_host": socket.gethostname()})
    log(f"us-in-brief {status}: {body.get('status')} {body.get('title', body.get('message', ''))} {final_url}")
    return status == 200


def article_ready(state):
    return state.get("paras", 0) >= 4 and not state.get("challenge")


def push_articles(browser, ingest_url, token, limit):
    """Push the section feeds, then load and push the few articles the server wants."""
    base = ingest_url.split("/api/")[0]
    feeds = []
    for url in FEEDS:
        try:
            feeds.append({"url": url, "xml": browser.fetch_text(url)})
        except RuntimeError as exc:
            log(f"feed {url}: {exc}")
        time.sleep(random.uniform(2, 5))
    if not feeds:
        return 1
    status, body = api(f"{base}/api/feeds/ingest", token,
                       {"feeds": feeds, "source_host": socket.gethostname(), "max_articles": limit})
    wanted = body.get("wanted") or []
    log(f"feeds {status}: {[(f.get('url', '').split('/')[3], f.get('items'), f.get('status')) for f in body.get('feeds', [])]}; "
        f"{len(wanted)} wanted")
    failures = 0
    for url in wanted[:limit]:
        time.sleep(random.uniform(20, 45))  # read like a person, not a crawler
        try:
            final_url, html = browser.load(url, article_ready, timeout=90)
        except RuntimeError as exc:
            log(f"article: {exc}")
            failures += 1
            continue
        status, body = api(f"{base}/api/articles/ingest", token,
                           {"url": url, "html": html, "source_host": socket.gethostname()})
        log(f"article {status}: {body.get('status')} {body.get('chars', '')} {url}")
        failures += status != 200
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backup", action="store_true",
                        help="run only if no Mac pushed in the last --backup-minutes")
    parser.add_argument("--backup-minutes", type=int, default=30)
    parser.add_argument("--articles", type=int, default=None,
                        help="also push feeds and up to N articles (default: ARTICLES_PER_RUN "
                             "at ARTICLE_HOURS_ET, else 0)")
    parser.add_argument("--seed", action="store_true",
                        help="copy the everyday Chrome sign-in into the job's profile and exit")
    args = parser.parse_args()
    if args.seed:
        seed_profile()
        log(f"seeded {PROFILE}")
        return 0
    ingest_url, token = load_env()
    articles = args.articles
    if articles is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        hour = datetime.now(ZoneInfo("America/New_York")).hour
        articles = ARTICLES_PER_RUN if hour in ARTICLE_HOURS_ET else 0

    if args.backup:
        status, body = api(ingest_url, token)
        last = (body or {}).get("last_push") or {}
        if status == 200 and last.get("at"):
            from datetime import datetime, timezone
            pushed = datetime.fromisoformat(last["at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - pushed).total_seconds() / 60
            if age < args.backup_minutes:
                log(f"skip: {last.get('source_host')} pushed {age:.0f} min ago")
                return 0

    with Browser() as browser:
        url, html = browser.load(WIB_URL, wib_ready)
        status, body = api(ingest_url, token, {"url": url, "html": html, "source_host": socket.gethostname()})
        log(f"push {status}: {body.get('status')} {body.get('title', body.get('message', ''))} {url}")
        result = 0 if status == 200 else 1
        if status == 200:
            # A US in Brief failure is logged but does not fail the run (World in Brief did land).
            try:
                href = browser.evaluate(US_LINK_JS)
            except RuntimeError as exc:
                href = None
                log(f"us-in-brief: {exc}")
            push_us_in_brief(browser, href, ingest_url, token)
        if articles:
            result |= push_articles(browser, ingest_url, token, articles)
    return result


if __name__ == "__main__":
    sys.exit(main())
