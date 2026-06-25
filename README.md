# Economist Newspaper RSS Feed

Private, subscriber-only tooling for producing a lightweight standard RSS feed
of The Economist articles for use in a personal RSS reader or other private
tools.

This repository is for individuals who already subscribe to the digital or
print edition of The Economist and want a private article list for the articles
they are authorized to access. It is not a public mirror, scraper service,
redistribution feed, or paywall bypass.

## Guardrails

- Use only with your own active Economist subscription.
- Do not publish generated feeds or cached full-text articles.
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
5. It writes or serves a lightweight RSS 2.0 feed with article metadata and
   preview descriptions.
6. It emits RSS `<category>` tags for Economist sections so readers can filter
   or search by section.

By default, RSS reader requests are limited by a 5-minute freshness guard,
discover articles from the last 30 days, serve up to 500 summary RSS items,
and fetch at most five new article bodies per refresh. The systemd timer uses
`--ignore-refresh-interval` so each scheduled 5-minute tick can try to backfill
five uncached articles without using `--force`; failed article retry backoff
still applies. That sets the normal trial ceiling at about 60 article fetches
per hour while still backfilling
incrementally. If your RSS reader asks for `/rss.xml` repeatedly within the
freshness window, it receives the cached feed without touching The Economist.

The default source list combines `latest/rss.xml` with section feeds because
`latest/rss.xml` alone is capped at 300 items and may not reach a full 30 days.
The `Essay` and `In Brief` feeds are included, so essays and `The US in Brief`
entries are normal RSS items. Podcast feed entries are also included as text
pages/episode notes; the
generated RSS does not include audio enclosures. `The World in Brief` is not a
normal dated RSS item, so the service fetches
`https://www.economist.com/the-world-in-brief` with the authenticated browser no
more than once per hour and saves the resolved dated page as a text RSS item.
Economic data and market-indicator pages are accepted as shorter table/data
items, rather than treated as login failures just because they have less prose
than a standard article.

## RSS Structure

The generated feed is RSS 2.0 and is intentionally lightweight. Each item
includes `title`, `link`, `guid`, `pubDate`, a short `description`, and one or
more `category` values. The feed does not embed full article HTML in
`content:encoded`; callers should use `link` to open the original Economist
article or a text endpoint when full article text is needed.

Items also include RSS `<category>` elements for section-level filtering in RSS
readers. The service stores upstream RSS/Atom category tags when The Economist
provides them, then falls back to Economist URL paths when source categories are
missing. URL fallback examples:

- `https://www.economist.com/finance-and-economics/...` becomes
  `Finance and Economics`
- `https://www.economist.com/essay/...` becomes `Essay`
- `https://www.economist.com/united-states/...` becomes `United States`
- `https://www.economist.com/in-brief/...` becomes `In Brief`
- `https://www.economist.com/the-world-in-brief/...` becomes
  `The World in Brief`

Some items also get title-derived category tags when the URL section alone is
too broad. For example, `The US in Brief: ...` emits both `In Brief` and
`United States`, and `The World in Brief` emits `The World in Brief`.

Interactive URLs can include both the underlying section and format, such as
`Europe` and `Interactive`. Use `<category>` for reader filtering by newspaper
section.

The HTTP server also supports optional category filtering while still returning
standard RSS 2.0 output:

```text
GET /rss.xml?token=long-random-token-for-rss-reader&category=United%20States
```

For RSS readers that work better with distinct feed URLs, use the category-feed
route:

```text
GET /rss/category/united-states.xml?token=long-random-token-for-rss-reader
GET /rss/category/the-world-in-brief.xml?token=long-random-token-for-rss-reader
```

Use repeated `category` parameters or comma-separated values to match any of
several categories. Matching is case-insensitive and uses the same `<category>`
values emitted in the RSS items.

The default `/rss.xml` response is standard RSS 2.0. The optional HTTP
category-filtering interface (`category=...` and `/rss/category/*.xml`) is the
project's intentional extension beyond RSS 2.0, added so other projects and RSS
readers can subscribe to section-specific feeds. The filtered responses
themselves are still standard RSS 2.0 documents.

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
economist-rss refresh --env-file real.env --config feeds.toml
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
Add `&category=United%20States` to return only items with that RSS category.
You can also subscribe directly to
`/rss/category/united-states.xml?token=...` for a United States-only feed.

## Refresh Strategy

The recommended production model is a separate small EC2 instance just for this
service.

- Run the HTTP RSS server continuously.
- Add a systemd timer every 5 minutes to refresh in the background.
- Keep the RSS endpoint private behind a long random `ECONOMIST_FEED_TOKEN`,
  VPN, Tailscale, basic auth, or a private reverse proxy.
- Keep `real.env`, SQLite data, and browser state on the EC2 volume, never in
  GitHub.

See [docs/EC2_DEPLOYMENT.md](docs/EC2_DEPLOYMENT.md).

## Rate-Limit Avoidance

The defaults intentionally behave like a patient human subscriber:

- RSS reads serve cache unless the 5-minute freshness guard has elapsed
- scheduled timer refresh every 5 minutes with `--ignore-refresh-interval`
- latest and section-feed discovery for articles published in the last 30 days
- generated RSS output limit of 500 summary items backed by cached full text
- one article request at a time
- randomized 75-180 second delay between article fetches
- maximum five new article downloads per refresh
- hard 180-second timeout around each browser article fetch
- maximum 60 article-page fetches per hour during this trial
- World in Brief browser refresh at most once per hour
- no repeat download after an article is successfully cached
- exponential retry delay for failures
- stop the current refresh batch when The Economist returns a rate-limit,
  Cloudflare, login, or short-excerpt response
- structured `article_fetch` log events for every article fetch attempt

Observed live signal: during the June 23, 2026 cache fill, an article fetch from
The Economist returned HTTP `403`. This project records that as
`content_status = 'rate_limited'` and treats HTTP `403`, HTTP `429`,
Cloudflare challenge pages, login pages, and excerpt-only pages as stop signs.
When one appears, the refresh exits instead of trying the remaining articles in
the same run.
Browser fetches also have a hard timeout so one stuck rendered page cannot block
the refresh timer indefinitely. A timeout is recorded as
`content_status = 'browser_fetch_timeout'` and the failed-article retry backoff
applies before that URL is tried again.

Do not run parallel catch-up jobs, tight manual loops, or forced refreshes
against the same database. For normal operation, let the 5-minute timer fetch
at most five uncached articles sequentially. Use `--ignore-refresh-interval`
only for that scheduled timer; use `--force` only for deliberate debugging
because it also bypasses failed-article retry backoff. If the telemetry shows
HTTP `403`, HTTP `429`, or Cloudflare challenges, switch back to one article
per 5-minute refresh, the previous four-article budget, the previous
three-article budget, the previous two-article budget, or the previous
10-minute cadence.

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
