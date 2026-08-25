import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const FEED_QUERY_KEYS = ["q", "start_date", "end_date", "category", "limit"];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), rssProxyPlugin(env)],
    server: {
      port: 5173,
      strictPort: false,
    },
  };
});

function rssProxyPlugin(env) {
  return {
    name: "economist-rss-viewer-proxy",
    configureServer(server) {
      server.middlewares.use("/api/feed", async (request, response) => {
        if (!["GET", "POST"].includes(request.method || "")) {
          sendJson(response, 405, { error: "Use GET or POST." });
          return;
        }

        let payload;
        try {
          payload = await readFeedRequest(request);
        } catch {
          sendJson(response, 400, { error: "Invalid JSON request." });
          return;
        }

        const configuredFeedUrl =
          process.env.RSS_VIEWER_FEED_URL || env.RSS_VIEWER_FEED_URL || "";
        const requestedFeedUrl = String(payload.feedUrl || "").trim();
        const feedUrl = requestedFeedUrl || configuredFeedUrl;
        if (!feedUrl) {
          sendJson(response, 400, {
            error: "Set RSS_VIEWER_FEED_URL or enter a feed URL.",
          });
          return;
        }

        let upstreamUrl;
        try {
          upstreamUrl = new URL(feedUrl);
        } catch {
          sendJson(response, 400, { error: "Feed URL is invalid." });
          return;
        }

        if (!["http:", "https:"].includes(upstreamUrl.protocol)) {
          sendJson(response, 400, { error: "Feed URL must use HTTP or HTTPS." });
          return;
        }

        for (const key of FEED_QUERY_KEYS) {
          const value = String(payload[key] || "").trim();
          if (value) {
            upstreamUrl.searchParams.set(key, value);
          }
        }

        try {
          const upstreamResponse = await fetch(upstreamUrl, {
            headers: {
              Accept: "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
              "User-Agent": "economist-rss-viewer/0.1",
            },
          });
          const xml = await upstreamResponse.text();
          if (!upstreamResponse.ok) {
            sendJson(response, upstreamResponse.status, {
              error: `Feed returned HTTP ${upstreamResponse.status}.`,
              status: upstreamResponse.status,
              sourceLabel: requestedFeedUrl
                ? publicSourceLabel(upstreamUrl)
                : "Configured feed",
            });
            return;
          }

          sendJson(response, 200, {
            xml,
            fetchedAt: new Date().toISOString(),
            status: upstreamResponse.status,
            sourceLabel: requestedFeedUrl
              ? publicSourceLabel(upstreamUrl)
              : "Configured feed",
          });
        } catch (error) {
          sendJson(response, 502, {
            error: error instanceof Error ? error.message : "Could not fetch feed.",
          });
        }
      });
    },
  };
}

async function readFeedRequest(request) {
  const requestUrl = new URL(request.url || "/api/feed", "http://localhost");
  if (request.method === "GET") {
    return feedQueryFromSearchParams(requestUrl.searchParams);
  }
  const payload = await readJsonBody(request);
  const query = feedQueryFromSearchParams(requestUrl.searchParams);
  return {
    feedUrl: payload.feedUrl || "",
    ...Object.fromEntries(
      FEED_QUERY_KEYS.map((key) => [key, payload[key] || query[key] || ""])
    ),
  };
}

function feedQueryFromSearchParams(searchParams) {
  return Object.fromEntries(
    FEED_QUERY_KEYS.map((key) => [key, searchParams.get(key) || ""])
  );
}

function readJsonBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 250_000) {
        reject(new Error("Request body is too large."));
        request.destroy();
      }
    });
    request.on("end", () => {
      if (!body.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(body));
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function sendJson(response, statusCode, payload) {
  const body = JSON.stringify(payload);
  response.statusCode = statusCode;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Content-Length", Buffer.byteLength(body));
  response.end(body);
}

function publicSourceLabel(url) {
  return `${url.origin}${url.pathname}`;
}
