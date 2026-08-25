# Economist RSS Reader

Small React Router app for inspecting the private RSS feed.

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

In production, keep the private feed token on the server. The browser calls
`GET /api/feed?limit=50&category=United%20States`; the reverse proxy should
rewrite that request to the authenticated backend RSS route and inject the
private token or authorization header server-side.

The current EC2 deployment serves this app from Caddy and proxies `/api/feed`
to the local Economist RSS service on `127.0.0.1:8080`. The client can also
still use a manually entered feed URL, in which case the Vite development proxy
uses `POST /api/feed` and never exposes the configured local `.env` value in
the bundle.
