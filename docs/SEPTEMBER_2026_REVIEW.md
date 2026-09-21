# Feed retention and request-volume review — 18 September 2026

Read-only checks of GitHub and the deployed service found production running
`c74bbf5`, including merged PRs #53, #54, and #55. No production settings were
changed during this review.

## Issue #50: partly fixed, keep open

PR #53 fixed the HTTP feed and statistics to include all cached current-issue
members even when their publication dates precede the rolling lookback cutoff.
PR #54 raised the reader's default result limit to 300. PR #55 introduced
edition metadata but broke two title-based retention assertions in main CI.

This follow-up makes CLI builds use the same validated issue-window policy as
HTTP and statistics. Missing or malformed issue dates retain the configured
lookback fallback. Regression coverage compares all three entry points and
checks membership by GUID, independently of display labels. Edition labels
now use separate RSS fields instead of decorating titles and categories.

Production at approximately 16:02 UTC had 215 default-feed items, rather than
two. However, its current issue was still `2026-09-12`, with zero discovered
members and `weeklyedition_calendar_fallback` as its source. Requests to the
weekly archive and edition page returned HTTP 403. All 215 items were therefore
classified by the existing code as online-only; this is not evidence that none
belong to the print edition. The last issue with explicit cached membership
was August 22 (74 articles).

A subsequent classification fix uses explicit publisher print-publication notes
already present in cached article text, as well as known issue IDs. Missing
evidence now yields `unknown`, displayed as Edition unverified, rather than
Online Only. This restores verified print labels without inventing issue dates;
it does not establish a complete weekly contents list.

Do not close #50 yet. First restore successful authorized weekly-edition
metadata discovery, confirm the actual latest edition and its members, and
verify that default output contains every cached member plus subsequent online
articles while older material remains searchable. A queued member also needs
its cached full text before it appears in this feed. The calendar fallback and
absence of an issue ID cannot prove that an article is online-only.

## Issue #51: reduce discovery polling, keep sequential downloads

Observed deployed settings:

- Timer: five minutes plus up to one minute of randomized delay; invokes
  `refresh --ignore-refresh-interval`.
- Reader-triggered refresh guard: 300 seconds.
- Each refresh polls all 26 configured RSS feeds, even when no new articles exist.
- Article bodies: at most five per refresh, sequential, with 75–180-second
  pauses between ordinary articles. World in Brief counts against this budget.
- Cached bodies are reused; failed articles have a six-hour base retry delay.
- A lock prevents overlapping refreshes. Stop signals end the current batch,
  but there is no persisted publisher-wide cooldown across subsequent runs.

The preceding 24-hour logs contained 257 completed refreshes; 224 reported no
new articles. Article fetch outcomes were 31 OK, three excerpt/login-required,
and five Cloudflare challenges. These signals do not establish that request
volume caused the challenges.

Five-minute discovery means a nominal 312 RSS requests per hour (7,488/day),
separate from article-page requests and their browser assets. Actual activity
varies with run duration, delays, failures, and reader-triggered refreshes.

Recommended target:

1. Discover RSS metadata every 30 minutes: nominal 52 RSS requests/hour,
   about 83% fewer than the current nominal cadence. Consider 15 minutes only
   if the extra freshness is valuable.
2. Drain the existing article backlog at most five articles per ten-minute
   cycle, still sequential with existing pauses. This caps scheduled body
   attempts at roughly 30/hour; 75 new articles take at least 2.5 hours, and
   longer with pauses, failures, or World in Brief work. The existing queue
   already prevents a whole edition being downloaded in one burst.
3. Add a shared, persisted cooldown for 429/403/challenge responses and honor
   Retry-After where available. Do not immediately retry a blocked discovery
   page with another user agent. Use conditional RSS requests with ETag and
   Last-Modified when supported; these reduce bytes but still count as requests.
4. Keep reader requests cache-only, or apply the same independent metadata and
   article schedules to both reader-triggered and timer-triggered work.

A low-effort interim option is a 10–15-minute timer plus a matching freshness
guard, keeping the five-article cap. This slows both discovery and backlog
work. Changing only the timer is insufficient because reader requests retain
an independent five-minute refresh trigger. Changing only the freshness guard
is insufficient because the timer bypasses it.

The 30-minute discovery / ten-minute queue target requires separating those
cadences in `refresh.py`, persisting their timestamps in the store, and updating
`deploy/economist-rss-refresh.timer` and the deployed configuration. Publisher-wide
cooldown needs to cover RSS discovery, issue discovery, World in Brief, and
ordinary article fetches. These are recommendations, not changes deployed by
this PR. No cadence guarantees acceptance by the publisher.
