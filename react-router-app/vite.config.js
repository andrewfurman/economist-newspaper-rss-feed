import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const FEED_QUERY_KEYS = ["q", "start_date", "end_date", "category", "limit"];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    base: normalizeBase(process.env.VITE_BASE_PATH || env.VITE_BASE_PATH || "/"),
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
      server.middlewares.use("/api/stats", async (request, response) => {
        await proxyConfiguredGet(request, response, env, "/api/stats", "application/json");
      });

      server.middlewares.use("/api/article-text", async (request, response) => {
        await proxyConfiguredGet(
          request,
          response,
          env,
          "/article.txt",
          "text/plain, application/json;q=0.8"
        );
      });

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

async function proxyConfiguredGet(request, response, env, upstreamPath, accept) {
  if (request.method !== "GET") {
    sendJson(response, 405, { error: "Use GET." });
    return;
  }

  const configuredFeedUrl =
    process.env.RSS_VIEWER_FEED_URL || env.RSS_VIEWER_FEED_URL || "";
  if (!configuredFeedUrl) {
    sendJson(response, 400, { error: "Set RSS_VIEWER_FEED_URL." });
    return;
  }

  let feedUrl;
  try {
    feedUrl = new URL(configuredFeedUrl);
  } catch {
    sendJson(response, 400, { error: "Configured feed URL is invalid." });
    return;
  }

  const upstreamUrl = new URL(upstreamPath, feedUrl.origin);
  const configuredToken = feedUrl.searchParams.get("token");
  if (configuredToken) {
    upstreamUrl.searchParams.set("token", configuredToken);
  }
  const requestUrl = new URL(request.url || "/", "http://localhost");
  for (const [key, value] of requestUrl.searchParams) {
    if (key !== "token") {
      upstreamUrl.searchParams.append(key, value);
    }
  }

  try {
    const upstreamResponse = await fetch(upstreamUrl, {
      headers: {
        Accept: accept,
        "User-Agent": "economist-rss-viewer/0.1",
      },
    });
    const body = Buffer.from(await upstreamResponse.arrayBuffer());
    response.statusCode = upstreamResponse.status;
    response.setHeader(
      "Content-Type",
      upstreamResponse.headers.get("content-type") || "text/plain; charset=utf-8"
    );
    response.setHeader("Content-Length", body.length);
    response.end(body);
  } catch (error) {
    sendJson(response, 502, {
      error: error instanceof Error ? error.message : "Could not fetch data.",
    });
  }
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

function normalizeBase(value) {
  const normalized = `/${String(value || "").replace(/^\/+|\/+$/g, "")}/`;
  return normalized === "//" ? "/" : normalized;
}
