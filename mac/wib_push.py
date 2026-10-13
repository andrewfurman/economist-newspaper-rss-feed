#!/usr/bin/env python3
"""Load today's World in Brief in this Mac's signed-in Chrome and push it to the feed server.

The feed server's AWS address is blocked by economist.com (Oct 2026). This Mac loads
the page like a person would, in a throwaway Chrome window that reuses a copy of the
everyday Chrome profile's sign-in, then posts the page to
POST {ECONOMIST_INGEST_URL} on the feed server. One page load per run.

Config: ~/.config/economist-wib/env with ECONOMIST_INGEST_URL and ECONOMIST_INGEST_TOKEN.
Stdlib only, for the macOS system python3 (3.9).

  wib_push.py            fetch and push now
  wib_push.py --backup   skip if another Mac pushed in the last 30 minutes
"""
import argparse
import base64
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

WIB_URL = "https://www.economist.com/the-world-in-brief"
CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
PROFILE_FILES = ["Cookies", "Cookies-journal", "Network", "Preferences", "Local Storage"]
ENV_FILE = Path.home() / ".config/economist-wib/env"

# Ready when the page has redirected to the dated edition and shows several stories.
PAGE_STATE_JS = r"""JSON.stringify({
  url: location.href,
  title: document.title,
  paras: [...document.querySelectorAll('main p, article p')].filter(p => p.innerText.length > 80).length,
  challenge: /just a moment|verify you are human/i.test(document.title + ' ' + document.body.innerText.slice(0, 500)),
})"""
# The extractor needs the JSON-LD and the article markup, not the whole app shell.
PAGE_HTML_JS = r"""(() => {
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.outerHTML).join('');
  const main = document.querySelector('main') || document.body;
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
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {token}"})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


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

    def evaluate(self, expression):
        self.next_id += 1
        body = json.dumps({"id": self.next_id, "method": "Runtime.evaluate",
                           "params": {"expression": expression, "returnByValue": True}}).encode()
        mask = os.urandom(4)
        header = bytes([0x81])
        if len(body) < 126:
            header += bytes([0x80 | len(body)])
        elif len(body) < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", len(body))
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", len(body))
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(body)))
        while True:
            reply = json.loads(self._frame())
            if reply.get("id") == self.next_id:
                return reply["result"]["result"].get("value")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def load_page(timeout=90):
    """Return (final_url, html) from a throwaway Chrome window signed in like everyday Chrome."""
    profile = Path(tempfile.mkdtemp(prefix="economist-wib-"))
    chrome = None
    try:
        (profile / "Default").mkdir()
        shutil.copy2(CHROME_DIR / "Local State", profile / "Local State")
        for name in PROFILE_FILES:
            source = CHROME_DIR / "Default" / name
            if source.is_dir():
                shutil.copytree(source, profile / "Default" / name)
            elif source.exists():
                shutil.copy2(source, profile / "Default" / name)
        port = free_port()
        # Launch through LaunchServices so Chrome runs in the logged-in GUI session and can
        # read "Chrome Safe Storage" from the keychain; started directly from ssh it can't
        # decrypt the copied cookies and loads signed out.
        subprocess.run(
            ["open", "-n", "-g", "-a", "Google Chrome", "--args", f"--user-data-dir={profile}",
             f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check",
             "--window-size=1280,900", WIB_URL],
            check=True)
        chrome = True
        deadline = time.monotonic() + timeout
        state = {}
        while time.monotonic() < deadline:
            time.sleep(3)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
                    pages = [t for t in json.load(response) if t.get("type") == "page"]
                tools = DevTools(pages[0]["webSocketDebuggerUrl"])
                state = json.loads(tools.evaluate(PAGE_STATE_JS) or "{}")
            except (OSError, ValueError, IndexError, KeyError):
                continue
            if "/the-world-in-brief/20" in state.get("url", "") and state.get("paras", 0) >= 5:
                time.sleep(2)  # let late paragraphs render
                return state["url"], tools.evaluate(PAGE_HTML_JS)
        raise RuntimeError(f"World in Brief did not load in {timeout}s: {state}")
    finally:
        if chrome is not None:
            # Only the throwaway instance has this profile path on its command line.
            subprocess.run(["pkill", "-f", f"user-data-dir={profile}"], check=False)
            time.sleep(2)
        shutil.rmtree(profile, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backup", action="store_true",
                        help="run only if no Mac pushed in the last --backup-minutes")
    parser.add_argument("--backup-minutes", type=int, default=30)
    args = parser.parse_args()
    ingest_url, token = load_env()

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

    url, html = load_page()
    status, body = api(ingest_url, token, {"url": url, "html": html, "source_host": socket.gethostname()})
    log(f"push {status}: {body.get('status')} {body.get('title', body.get('message', ''))} {url}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
