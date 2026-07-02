# Economist RSS Viewer

Small React Router app for inspecting the private RSS feed locally.

## Run

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
