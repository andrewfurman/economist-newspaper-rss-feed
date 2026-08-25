import assert from "node:assert/strict";
import test from "node:test";

import {
  buildArticleTextUrl,
  buildApiFeedUrl,
  buildStatsUrl,
  hasCatalogSearch,
  searchParamsFromRequest,
  searchRequestFromParams,
} from "./feed-request.js";

test("buildApiFeedUrl forwards catalog and feed filters", () => {
  const url = buildApiFeedUrl(
    {
      q: "Iran",
      start_date: "1990-01-01",
      end_date: "1999-12-31",
      category: "Finance and Economics",
      limit: 50,
    },
    "https://reader.example"
  );

  assert.equal(url.pathname, "/api/feed");
  assert.equal(url.searchParams.get("q"), "Iran");
  assert.equal(url.searchParams.get("start_date"), "1990-01-01");
  assert.equal(url.searchParams.get("end_date"), "1999-12-31");
  assert.equal(url.searchParams.get("category"), "Finance and Economics");
  assert.equal(url.searchParams.get("limit"), "50");
});

test("search request state round-trips through bookmark parameters", () => {
  const request = {
    q: "central banking",
    start_date: "2001-01-01",
    end_date: "2005-12-31",
    category: "Finance and Economics",
    limit: "75",
  };

  const params = searchParamsFromRequest(request);

  assert.deepEqual(searchRequestFromParams(params), request);
  assert.equal(hasCatalogSearch(request), true);
  assert.equal(hasCatalogSearch({ category: "Asia", limit: 20 }), true);
});

test("article and stats URLs stay on the reader origin", () => {
  const article = buildArticleTextUrl(
    {
      link: "https://www.economist.com/asia/2026/08/25/story",
      guid: "story-guid",
    },
    "https://reader.example"
  );
  const stats = buildStatsUrl("https://reader.example");

  assert.equal(article.pathname, "/api/article-text");
  assert.equal(
    article.searchParams.get("url"),
    "https://www.economist.com/asia/2026/08/25/story"
  );
  assert.equal(article.searchParams.get("guid"), null);
  assert.equal(stats.href, "https://reader.example/api/stats");
});
