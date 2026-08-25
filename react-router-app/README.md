# Economist RSS Reader

Small React Router app for inspecting the private RSS feed.

The reader has four top-level views:

- **Raw RSS** shows the latest RSS document as expandable sections and items.
- **Recent articles** shows the default current-issue feed in a sortable,
  locally filterable table. Open a title to read the cached plain-text article.
- **Search** queries the local back catalog by keywords, inclusive start/end
  dates, section, and result limit. Search state remains in the browser URL so
  it can be bookmarked without exposing feed credentials.
- **Database stats** reports article and section totals, full-text coverage,
  refresh state, catalog coverage, and content fetch statuses from SQLite.

## Local Development

```bash
npm install
RSS_VIEWER_FEED_URL="https://example.com/rss.xml?token=..." npm run dev
```

You can also put the private URL in `react-router-app/.env.local`:

```env
RSS_VIEWER_FEED_URL=https://example.com/rss.xml?token=...
```

The feed URL is used only by the local Vite proxy endpoint. It is not bundled
into the React client and `.env.local` is ignored by Git.

## Production

Build the static app and serve `dist/` from the web server:

```bash
npm run build
```

For a subpath deployment, set the base path only at build time:

```bash
VITE_BASE_PATH=/reader/ npm run build
```

In production, keep the private feed token on the server. The browser calls
`GET /api/feed?limit=50&category=United%20States`; the reverse proxy should
rewrite that request to the authenticated backend RSS route and inject the
private token or authorization header server-side.

The browser also calls `GET /api/stats` for database-level statistics and
`GET /api/article-text?url=...` for a selected article. The reverse proxy maps
the latter to the backend `/article.txt` route. Both routes must receive the
same server-side feed authentication; credentials are never bundled into the
client.

Catalog searches use the same proxy, for example:

```text
GET /api/feed?q=Iran&start_date=1997-01-01&end_date=1999-12-31&limit=100
```

The proxy forwards only the supported feed parameters. Search requests are
served entirely from the backend SQLite index and do not trigger an Economist
refresh.

The current EC2 deployment serves this app from Caddy and proxies `/api/feed`
to the local Economist RSS service on `127.0.0.1:8080`. The client can also
still use a manually entered feed URL, in which case the Vite development proxy
uses `POST /api/feed` and never exposes the configured local `.env` value in
the bundle.
