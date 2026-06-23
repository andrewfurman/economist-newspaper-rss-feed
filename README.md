# Economist Newspaper RSS Feed

Private, subscriber-only tooling for producing a standard RSS feed of
The Economist articles with full text for use in a personal RSS reader.

This repository is for individuals who already subscribe to the digital or
print edition of The Economist and want to read the articles they are
authorized to access in their preferred RSS reader. It is not a public
mirror, scraper service, redistribution feed, or paywall bypass.

## Guardrails

- Use only with your own active Economist subscription.
- Do not publish generated full-text feeds.
- Do not commit credentials, browser state, article caches, or generated feeds.
- Do not use this to train models, bulk archive publisher content, or share
  subscriber-only articles with other people.
- The browser fetcher performs normal authenticated page loads. It does not
  bypass CAPTCHAs, Cloudflare challenges, subscription checks, or access
  controls.

## How It Works

The service is cache-first.

1. It polls configured Economist RSS feeds such as
   `https://www.economist.com/latest/rss.xml`.
2. It records article URLs in a local SQLite database.
3. It fetches full article text only for articles that are not already cached.
4. It fetches articles sequentially, with a randomized delay between requests.
5. It writes or serves a normal RSS 2.0 feed with `content:encoded` article
   bodies.

By default, refreshes are limited to once every hour and at most 12 new
article fetches per refresh. If your RSS reader asks for `/rss.xml` repeatedly
within that window, it receives the cached feed without touching The Economist.

## Files

- `sample.env`: tracked example of required environment variables.
- `real.env`: ignored local secrets file.
- `feeds.example.toml`: tracked example configuration.
- `data/economist-rss.sqlite3`: ignored article/cache database.
- `.cache/economist-browser-*`: ignored authenticated browser profile/state.

## Quick Start

Requires Python 3.11 or newer.

```bash
cd economist-newspaper-rss-feed
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[browser]'
python -m playwright install chromium
cp feeds.example.toml feeds.toml
cp sample.env real.env
```

Edit `real.env`:

```env
ECONOMIST_EMAIL=you@example.com
ECONOMIST_PASSWORD=your-password
ECONOMIST_BROWSER_FETCH_ENABLED=true
ECONOMIST_FEED_TOKEN=long-random-token-for-rss-reader
```

Authenticate and save browser state:

```bash
economist-rss auth --env-file real.env --config feeds.toml
```

If The Economist or Cloudflare requires human verification, use a visible
browser window:

```bash
economist-rss auth --env-file real.env --config feeds.toml --headed --auth-wait-seconds 600
```

Click any Cloudflare `Verify you are human` challenge yourself. The script will
continue waiting for subscriber full-text access and save the resulting browser
state when verification succeeds.

If the automatic username/password form fill does not land in a subscribed
session, use manual login mode:

```bash
economist-rss auth --env-file real.env --config feeds.toml --headed --manual-login --auth-wait-seconds 900
```

In the visible browser window, click `Log in`, complete The Economist login,
clear any Cloudflare challenge yourself, and return to the verification article
if needed. The script saves the private browser state after it can see full
subscriber article text.

Refresh the cache and build a feed:

```bash
economist-rss refresh --env-file real.env --config feeds.toml --force
economist-rss build --env-file real.env --config feeds.toml --output dist/economist-fulltext.xml
```

Serve the private RSS feed locally:

```bash
economist-rss serve --env-file real.env --config feeds.toml --host 127.0.0.1 --port 8080
```

The feed will be available at:

```text
http://127.0.0.1:8080/rss.xml?token=long-random-token-for-rss-reader
```

When `ECONOMIST_FEED_TOKEN` is set, `GET /rss.xml` requires either
`?token=...` in the URL or an `Authorization: Bearer ...` header.

## Refresh Strategy

The recommended production model is a separate small EC2 instance just for this
service.

- Run the HTTP RSS server continuously.
- Add a systemd timer every hour to refresh in the background.
- Keep the RSS endpoint private behind a long random `ECONOMIST_FEED_TOKEN`,
  VPN, Tailscale, basic auth, or a private reverse proxy.
- Keep `real.env`, SQLite data, and browser state on the EC2 volume, never in
  GitHub.

See [docs/EC2_DEPLOYMENT.md](docs/EC2_DEPLOYMENT.md).

## Rate-Limit Avoidance

The defaults intentionally behave like a patient human subscriber:

- one feed refresh every hour
- one article request at a time
- randomized 75-180 second delay between article fetches
- maximum 12 new article downloads per refresh
- no repeat download after an article is successfully cached
- exponential retry delay for failures
- stop treating Cloudflare/403/login pages as article text

See [docs/RATE_LIMITING.md](docs/RATE_LIMITING.md).

## Development

## Change Control

All changes to this repository should be merged through GitHub pull requests so
there is an auditable trail of what changed, why it changed, and how it was
tested.

- Do not push directly to `main`.
- Create a branch for each change.
- Open a pull request with a summary, rationale, and validation notes.
- Run the relevant tests before merging.
- Keep credentials, browser state, generated feeds, and SQLite cache files out
  of every branch and pull request.
- Merge only after the PR diff has been reviewed for secrets and unintended
  subscriber-content artifacts.

```bash
python -m unittest discover -s tests
```

Use fast test settings locally:

```toml
min_article_delay_seconds = 0
max_article_delay_seconds = 0
max_articles_per_refresh = 1
```
