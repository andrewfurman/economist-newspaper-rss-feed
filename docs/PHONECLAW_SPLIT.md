# Split Economist logic out of Phoneclaw and support configurable RSS feeds

## Background

Phoneclaw currently has a lot of Economist-specific RSS logic mixed into the
voice-agent bridge: Economist route names, RSS-Bridge topic inference,
Miniflux category assumptions, browser fallback handling, and subscriber
full-text extraction details.

That should move into a separate repository:

`andrewfurman/economist-newspaper-rss-feed`

Phoneclaw should become a generic RSS consumer/tool surface. It should support
multiple configured RSS feeds, but it should not know how to log into The
Economist, scrape article pages, manage Economist cookies/browser state, or
interpret Economist-specific access failures.

## Goal

Let a user configure several RSS sources, for example their five favorite
feeds, and have Phoneclaw list/search/read/refresh those feeds through one
generic interface.

The Economist should become just one configured RSS source, backed by the
separate private Economist RSS server.

## Proposed Phoneclaw Configuration

Add a generic feed config shape, either in env JSON, a local config file, or a
database-backed setting:

```json
[
  {
    "id": "economist-fulltext",
    "title": "The Economist",
    "feed_url": "https://private.example.com/economist/rss.xml",
    "refresh_url": "https://private.example.com/economist/refresh",
    "refresh_token_env": "ECONOMIST_REFRESH_TOKEN",
    "refresh_ttl_seconds": 300,
    "enabled": true
  },
  {
    "id": "example-feed",
    "title": "Example Feed",
    "feed_url": "https://example.com/feed.xml",
    "refresh_ttl_seconds": 3600,
    "enabled": true
  }
]
```

`refresh_url` should be optional. Plain RSS feeds may only have `feed_url`.
Backend-backed feeds, such as the private Economist server, can expose
`refresh_url` for an explicit refresh trigger.

## Generic RSS Capabilities

Replace Economist-specific tool names and endpoints with generic RSS tools:

- `rss_list_feeds`: return configured feed ids/titles/status.
- `rss_recent_entries`: return recent entries across all enabled feeds or a
  selected feed id.
- `rss_search_entries`: search titles, descriptions, and cached content across
  feeds.
- `rss_get_entry`: return one entry by generic entry id, canonical URL, or
  source feed id plus guid.
- `rss_refresh_feeds`: refresh all enabled feeds or a selected feed id.

Suggested HTTP routes:

- `POST /cli/rss/feeds`
- `POST /cli/rss/recent`
- `POST /cli/rss/search`
- `POST /cli/rss/entry`
- `POST /cli/rss/refresh`

The old `/cli/rss/economist/*` routes can remain temporarily as compatibility
wrappers, but should delegate into the generic implementation and be removed
after the agent/tool schema migration.

## Refresh and Rate-Limit Behavior

Phoneclaw should not scrape publisher article pages. It should only:

- fetch configured RSS URLs;
- call optional configured refresh URLs;
- cache RSS metadata/content returned by those feeds.

Refresh should be debounce/cache-first:

- Each feed records `last_refresh_at`, `last_success_at`, and
  `last_failure_at`.
- If `rss_refresh_feeds` is called within `refresh_ttl_seconds`, return cached
  status instead of hitting upstream again unless `force=true`.
- Refresh feeds sequentially by default.
- Add small jitter between feed refreshes when refreshing multiple feeds.
- Respect `Retry-After` for `429` or `503` responses.
- Mark failures clearly without deleting the last good cached entries.

For the Economist specifically, the separate Economist RSS server should handle
article-level throttling and caching:

- default refresh interval: 5 minutes;
- sequential full article fetches only;
- randomized 75-180 second delay between article fetches;
- maximum five new article fetches per 5-minute refresh during the monitored
  trial;
- hard 180-second timeout around each browser article fetch;
- default article lookback: 30 days;
- podcast entries included as text pages or episode notes, without audio
  enclosures;
- World in Brief included through the separate Economist RSS server's
  authenticated browser fetch;
- never download the same successfully cached article twice;
- exponential retry delay for failed article fetches;
- persistent SQLite cache on the separate EC2 instance.

Phoneclaw should simply call the Economist server's private RSS URL and
optional refresh endpoint.

## Data Model

Use feed-agnostic identifiers and canonical URLs:

- `feed_id`
- `feed_title`
- `entry_id`
- `guid`
- `canonical_url`
- `title`
- `summary`
- `content_html`
- `content_text`
- `published_at`
- `fetched_at`
- `source_url`

De-duplicate entries by canonical URL first, then by `(feed_id, guid)`, then by
normalized title as a last resort.

## Remove From Phoneclaw

After the migration, Phoneclaw should not contain:

- `ECONOMIST_EMAIL`
- `ECONOMIST_PASSWORD`
- Economist browser profile/storage-state paths
- Economist section/topic lists
- RSS-Bridge-specific Economist URL builders
- Economist login/free-trial boilerplate stripping
- Economist Cloudflare/paywall detection logic
- Miniflux category assumptions hardcoded to `Economist`

Any remaining environment variables should describe generic RSS behavior or
the private feed endpoint, not publisher credentials.

## Migration Steps

1. Add generic RSS feed configuration.
2. Add generic RSS fetch/cache/search module.
3. Add generic CLI endpoints and agent tool schemas.
4. Reimplement existing Economist routes as compatibility wrappers around the
   generic RSS module.
5. Configure the private Economist RSS server as one generic feed.
6. Move any remaining Economist-specific auth/fetch/extraction code into
   `andrewfurman/economist-newspaper-rss-feed`.
7. Update ElevenLabs prompt/tool guidance to use generic RSS terms.
8. Remove old Economist-specific route names after compatibility testing.

## Acceptance Criteria

- A user can configure at least five arbitrary RSS feed URLs.
- The voice agent can list configured feeds.
- The voice agent can return recent entries for all feeds or one feed.
- The voice agent can search entries across all feeds or one feed.
- The voice agent can trigger a refresh without causing repeated upstream
  requests inside the feed TTL.
- Phoneclaw no longer requires Economist subscriber credentials.
- The Economist feed works through a configured private RSS URL from the
  separate `economist-newspaper-rss-feed` service.
- Existing Economist voice tests are replaced or wrapped by generic RSS tests.
- Tests cover feed de-duplication, refresh TTL behavior, stale-cache behavior,
  and graceful upstream failures.
