# Rate-Limiting Notes

This project should avoid looking like a crawler. The goal is to refresh a
single subscriber's private RSS cache, not to mirror The Economist.

## Current Signals

As of June 23, 2026:

- `https://www.economist.com/robots.txt` blocks several AI/crawler user agents
  but does not publish a `crawl-delay` for ordinary user agents.
- `https://www.economist.com/latest/rss.xml` returns RSS XML from this
  environment.
- `https://www.economist.com/rss` returned a Cloudflare challenge from this
  environment.
- The article pages are protected by subscriber access and may also be
  challenged by Cloudflare.
- A live cache-fill run for this service saw an Economist article fetch return
  HTTP `403`. The service records this as `content_status = 'rate_limited'`.

Relevant references:

- The Economist robots file: https://www.economist.com/robots.txt
- Cloudflare rate limiting docs:
  https://developers.cloudflare.com/waf/rate-limiting-rules/
- Cloudflare challenge response docs:
  https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/

## Recommended Defaults

Use these defaults in production:

```toml
refresh_interval_seconds = 300
article_lookback_days = 30
rss_item_limit = 500
min_article_delay_seconds = 75
max_article_delay_seconds = 180
max_articles_per_refresh = 4
retry_failed_after_seconds = 21600
world_in_brief_refresh_interval_seconds = 3600
```

This means:

- RSS readers can poll the private feed often, but upstream Economist refreshes
  from reader requests happen only after the 5-minute freshness guard elapses.
- The systemd timer runs the scheduled refresh with
  `--ignore-refresh-interval`, so each 5-minute timer tick can attempt a small
  backfill batch without using `--force`.
- Discovery is limited to configured RSS items published in the last 30 days.
- The default config uses section RSS feeds because `latest/rss.xml` alone is
  capped at 300 items and may not reach 30 days.
- The served RSS output is capped at 500 full-text items by default, which is
  independent of upstream article fetch volume.
- New article fetches happen sequentially.
- A normal refresh fetches at most four article bodies.
- The normal scheduled trial ceiling is about 48 article-page fetches per hour,
  though the randomized inter-article delay usually keeps the actual rate lower.
- The World in Brief special fetch runs at most once per hour and counts
  against the article-fetch budget.
- Successfully cached articles are not downloaded again.
- Failed articles wait at least six hours before retry, multiplied by the
  attempt count.

## Observed Stop Signal

The important operational lesson from the June 23, 2026 live run is that an
article HTTP `403` should be treated conservatively. Do not keep fetching other
articles after seeing it.

The refresh code treats these as stop signs:

- HTTP `403`
- HTTP `429`
- Cloudflare challenge pages
- login/subscription pages
- excerpt-only article pages
- RSS-Bridge-style placeholders such as `resulted in 403 Forbidden`

When a stop sign appears, the service records the article error, writes
`last_refresh_stop_reason`, and exits the current refresh batch. A later
scheduled refresh can retry after the configured backoff window. This is
intentionally slower than a catch-up loop because avoiding rate limits is more
important than populating every article immediately.

Manual backfills should stay one-at-a-time and sequential. Do not run parallel
refresh processes, tight `--force` loops, or multiple hosts against the same
Economist account.

The 5-minute/four-article setting is intentionally a monitored trial. The
scheduled service should use `--ignore-refresh-interval`, not `--force`, because
`--force` also bypasses failed-article retry backoff. If telemetry shows HTTP
`403`, HTTP `429`, Cloudflare challenges, or repeated excerpt/login responses,
reduce `max_articles_per_refresh` to `3`, reduce it further to `2` or `1`, or
restore a 10-minute timer.

## Why Not Fetch On Every RSS Request?

RSS readers vary widely. Some poll every few minutes, some retry aggressively
after network failures, and some make parallel requests from multiple clients.
If every RSS read triggered upstream article fetches, a normal reader could
accidentally create a burst.

The server therefore separates reading from refreshing:

- `GET /rss.xml` serves cached RSS.
- `GET /rss.xml` also triggers refresh only when the cache is stale.
- `POST /refresh` can force a refresh when protected by
  `ECONOMIST_REFRESH_TOKEN`.
- A systemd timer can refresh every 5 minutes independent of reader behavior
  with `--ignore-refresh-interval`.

## Fetch Telemetry

Every article fetch attempt logs two structured events to stdout/stderr, which
systemd captures in journald:

- `article_fetch_start`
- `article_fetch_result`

Each event includes:

- `run_id`
- `queue_index` and `queue_size`
- `title`
- `url` and `canonical_url`
- `attempt_count_before`
- `status`
- `source`
- `http_status`
- `retry_after_seconds`
- `elapsed_ms`
- `stop_refresh`
- `stop_reason`

Use these logs to estimate the practical rate limit over time:

```bash
journalctl -u economist-rss-refresh.service --since "24 hours ago" \
  | grep 'article_fetch'
```

To inspect only rate-limit and challenge signals:

```bash
journalctl -u economist-rss-refresh.service --since "7 days ago" \
  | grep 'article_fetch' \
  | grep -E '"status":"rate_limited"|"http_status":403|"http_status":429|"cloudflare"'
```

These logs intentionally do not include credentials, feed tokens, browser state,
or article body text.

## Source Coverage

The default config includes normal article sections, `Essay`, `In Brief`,
`Podcasts`, and a special World in Brief source.

- Essays are discovered from `https://www.economist.com/essay/rss.xml` and
  tagged with the `Essay` RSS category.
- `The US in Brief` entries are discovered from
  `https://www.economist.com/in-brief/rss.xml`.
- Podcast entries are discovered from
  `https://www.economist.com/podcasts/rss.xml` and fetched as text pages or
  episode notes. Audio enclosures are not added to the generated RSS.
- `The World in Brief` did not appear as a normal dated article item in the RSS
  feeds checked on June 24, 2026. It is fetched through the authenticated
  browser from `https://www.economist.com/the-world-in-brief`; the resolved
  dated page is cached as a text RSS item.

## Backoff Behavior

The service should treat these responses as stop signs, not article bodies:

- HTTP `429`
- HTTP `403`
- Cloudflare challenge pages
- login/subscription pages
- RSS-Bridge-style placeholders such as `resulted in 403 Forbidden`

For failures, the article record is kept in SQLite with `content_status` and
`error`, then retried later. The successful full-text cache is permanent unless
you explicitly delete the database or force a retry.
