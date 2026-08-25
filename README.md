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
7. It limits RSS output to the latest weekly print issue plus online-only
   articles published after that issue date.

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

## Current Issue Filtering

By default, the RSS output is scoped to the current magazine issue rather than
every cached article from the last 30 days. The cache still keeps older articles
for history and direct lookup, but `/rss.xml`, `/rss/category/*.xml`, and
generated RSS files emit only:

- articles discovered on the latest first-party weekly edition page, such as
  `https://www.economist.com/weeklyedition/2026-06-27`
- articles not assigned to an older issue whose published date is on or after
  the latest issue date, treated as online exclusives
- the compact brief exceptions described below, still limited to only the
  latest World in Brief and latest United States/US in Brief item

The resolver checks `https://www.economist.com/weeklyedition/archive` for the
newest issue URL, allowing a two-day lookahead so a newly published Saturday
issue can be recognized near publication time. It then parses article links
from that issue page and stores `issue_id`, `issue_date`, and `issue_source`
metadata in SQLite. Current-issue discovery is throttled separately from
article fetching with `current_issue_refresh_interval_seconds`, which defaults
to six hours.

If the weekly-edition page cannot be read because The Economist or Cloudflare
blocks the request, the feed does not fail closed. It records the error in
SQLite state, keeps any previously resolved issue metadata for the same issue,
and otherwise falls back to the expected Saturday issue cadence. In that
fallback mode it includes cached articles published after the previous issue
date and excludes rows already marked as older issues. Strict print-issue
membership is available again as soon as the weekly-edition page can be fetched.

## RSS Structure

The generated feed is RSS 2.0 and is intentionally lightweight. Each item
includes `title`, `guid`, `pubDate`, a `description`, and one or more
`category` values. Regular article items also include `link`. The feed does not
embed full article HTML in `content:encoded`; callers should use `link` to open
the original Economist article or a text endpoint when full article text is
needed.

The compact brief formats are the exception to the short-description rule.
When cached full text is available, `The World in Brief` and United States/US
in Brief items put their full plain text in the item `description` and omit the
item `link`, because the full article text is already present in the feed.
Regular articles still emit only the short preview description. The feed never
embeds full article HTML or images.

Only the latest `The World in Brief` item and the latest United States/US in
Brief item are emitted in RSS output. Older cached brief items remain in the
SQLite database for history and direct lookup, but they are suppressed from
default, limited, and category-filtered RSS feeds.

Full cached article text is available from an authenticated companion endpoint:

```text
GET /article.txt?token=long-random-token-for-rss-reader&url=https%3A%2F%2Fwww.economist.com%2Fbriefing%2F2026%2F06%2F25%2Fexample
```

The article endpoint also accepts `link` or `guid` instead of `url`. It returns
`text/plain; charset=utf-8` with the cached `content_text` value only. It does
not return HTML, images, or embedded media, and it does not fetch The Economist
on demand. If the article has not already been cached successfully, the endpoint
returns `404`.

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
GET /rss.xml?token=long-random-token-for-rss-reader&limit=50
GET /rss.xml?token=long-random-token-for-rss-reader&category=Business&limit=20
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

Use `limit` to request a smaller number of items, such as `limit=20` or
`limit=50`. `count` is accepted as an alias. Requested limits are capped by the
configured `rss_item_limit`, which defaults to `500`.

The default `/rss.xml` response is standard RSS 2.0. The optional HTTP
query interface (`q=...`, `start_date=...`, `end_date=...`, `category=...`,
`limit=...`, and `/rss/category/*.xml`) is the project's intentional extension
beyond RSS 2.0. It lets other projects request searches, date ranges,
section-specific feeds, or shorter feeds. Every successful response is still a
standard RSS 2.0 document.

The `/article.txt` route is a separate authenticated HTTP endpoint, not part of
the RSS payload. It exists so downstream tools can fetch the full cached article
text only when needed while keeping `/rss.xml` small and RSS-reader compliant.

## Searchable Catalog

Supplying `q`, `start_date`, or `end_date` switches `/rss.xml` from the current
issue view to local catalog search. Search covers article titles, descriptions,
stored categories, and cached full text. Results are deterministic and newest
first. Date boundaries are inclusive and use `YYYY-MM-DD`.

```text
GET /rss.xml?token=long-random-token-for-rss-reader&q=Iran
GET /rss.xml?token=long-random-token-for-rss-reader&q=Iran&start_date=1997-01-01&end_date=1999-12-31&limit=100
GET /rss.xml?token=long-random-token-for-rss-reader&start_date=2000-01-01&end_date=2009-12-31&category=Finance%20and%20Economics
```

`category` combines with keyword and date filters using AND. Repeated or
comma-separated categories match any requested section. Multi-word keyword
search requires all normalized terms. Invalid dates, inverted ranges, invalid
limits, and keyword strings longer than 200 characters return HTTP 400.

Catalog searches read SQLite only. They never refresh source feeds, open an
Economist page, or consume the article-fetch budget. A metadata-only result can
appear before its full text has been fetched; its original article `link` still
works, while `/article.txt` returns 404 until `content_status` is `ok`.

SQLite FTS5 indexes titles, descriptions, categories, and article text. The
service keeps that index synchronized with article writes and falls back to a
case-insensitive local scan when SQLite lacks FTS5. Rebuild it explicitly after
database maintenance:

```bash
economist-rss rebuild-index --env-file real.env --config feeds.toml
```

The default feed remains limited to the latest issue plus newer online
exclusives when none of `q`, `start_date`, or `end_date` is present.

## JSON API

The HTTP server exposes a private JSON API alongside the RSS routes. Read
requests accept the same `ECONOMIST_FEED_TOKEN` bearer header or `token` query
parameter as the RSS feed. API responses contain metadata and at most a
320-character plain-text snippet; they never include `content_html` or the full
`content_text` field.

All API searches are cache-only. The service does not contact The Economist in
response to a search or list request. Here, "feed metadata" means the cached
union populated by the configured Economist RSS feeds and weekly-edition
catalog discovery, not a live publisher search endpoint.

Search the local full-text index first, then fill the remaining result limit
from title, description, and category matches in cached feed metadata:

```text
GET /api/search?q=Iran&start_date=1997-01-01&end_date=1999-12-31&category=Asia&limit=50
```

`scope=all` is the default. It returns local full-text matches first and feed
metadata matches second, with newest-first ordering inside each group. Use
`scope=local` for cached full-text matches only, or `scope=feed` (also accepted
as `scope=rss`) to search only titles, descriptions, and categories. Even local
matches return snippets rather than article bodies.

List or search all cached article metadata with offset pagination:

```text
GET /api/articles?limit=100&offset=0
GET /api/articles?q=Iran&start_date=1997-01-01&end_date=1999-12-31&limit=100&offset=0
```

Results are newest first. Responses include `has_more` and `next_offset`; both
`limit` and search results are capped at 500 records per response. Keyword,
date, and category rules match the searchable RSS catalog, except this endpoint
searches snippet metadata rather than full article bodies.

Each result reports `full_text_available`, a token-free relative `status_url`,
and, when ready, a token-free relative `full_text_url`. Supply authentication
when following either URL. Poll an article without returning its body:

```text
GET /api/articles/status?guid=article-guid
```

Request a full-text fetch for one metadata record already known to SQLite:

```bash
curl -X POST http://127.0.0.1:8080/api/articles/fetch \
  -H 'Authorization: Bearer your-separate-refresh-token' \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://www.economist.com/example-section/1999/01/01/example"}'
```

The write route requires `ECONOMIST_REFRESH_TOKEN` as a bearer header. It does
not accept that token in the URL. Unknown URLs and non-Economist URLs are
rejected, which prevents the endpoint from becoming an arbitrary URL fetcher.
A cached article returns HTTP 200 with `status: ready`; an uncached article
returns HTTP 202 with `status: queued`.

Queued requests are durable SQLite state. The existing sequential refresh
worker tries requested historical articles before ordinary recent backfill,
while keeping the same total `max_articles_per_refresh`, randomized delay,
failure backoff, challenge detection, and stop rules. Repeated requests for the
same canonical URL increment an audit counter but do not create duplicate queue
rows. A successful fetch clears the pending request and makes the plain text
available through `/article.txt`.

The API cannot fetch an article that has not first been discovered through an
RSS feed or weekly-edition catalog discovery. Use the catalog discovery command
below to extend the searchable historical metadata set gradually.

## Catalog Backfill

The Economist [currently describes its searchable digital archive](https://www.economist.com/pro/features)
as containing articles published since 1997. This project therefore defaults catalog
discovery to `1997-01-04`; a 1990s request can only return locally discovered
records from 1997 onward. Earlier date parameters are accepted, but this tool
does not claim coverage that the subscriber archive does not expose.

Metadata discovery is explicit and separate from full-text retrieval. It is
not part of the normal five-minute refresh:

```bash
# Discover one not-yet-completed weekly edition, newest first.
economist-rss catalog-discover --env-file real.env --config feeds.toml

# Bound discovery to a period. One issue is still the default per-run budget.
economist-rss catalog-discover --env-file real.env --config feeds.toml \
  --start-date 1997-01-04 --end-date 1999-12-31 --max-issues 1

# Fetch a small batch of article bodies already discovered in that period.
economist-rss catalog-fetch --env-file real.env --config feeds.toml \
  --start-date 1997-01-04 --end-date 1999-12-31 --max-articles 2

economist-rss catalog-stats --env-file real.env --config feeds.toml
```

Discovery records every attempted issue in `catalog_issues`, including source,
status, article count, attempt timestamps, completion time, and error. Completed
issues are skipped on later runs, failed issues respect the configured retry
backoff, and canonical article URLs remain unique in `articles`. A Cloudflare
challenge or HTTP 403/429 stops the run immediately. `--max-issues` is capped at
10; article-body backfill is capped by `max_articles_per_refresh` and uses the
same sequential delays, retry policy, logging, browser timeout, and stop rules
as normal refreshes. Do not schedule catalog discovery on the five-minute
latest-news timer.

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
ECONOMIST_REFRESH_TOKEN=different-long-random-token-for-write-requests
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
- requested historical articles consume that same five-article budget and are
  attempted before ordinary recent backfill
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
