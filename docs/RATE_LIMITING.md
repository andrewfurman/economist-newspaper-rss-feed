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

Relevant references:

- The Economist robots file: https://www.economist.com/robots.txt
- Cloudflare rate limiting docs:
  https://developers.cloudflare.com/waf/rate-limiting-rules/
- Cloudflare challenge response docs:
  https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/

## Recommended Defaults

Use these defaults in production:

```toml
refresh_interval_seconds = 3600
min_article_delay_seconds = 75
max_article_delay_seconds = 180
max_articles_per_refresh = 12
retry_failed_after_seconds = 21600
```

This means:

- RSS readers can poll the private feed often, but upstream Economist refreshes
  happen at most every hour.
- New article fetches happen sequentially.
- A normal refresh takes a slow drip approach instead of a burst.
- Successfully cached articles are not downloaded again.
- Failed articles wait at least six hours before retry, multiplied by the
  attempt count.

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
- A systemd timer can refresh every hour independent of reader behavior.

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
