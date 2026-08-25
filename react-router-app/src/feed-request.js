const FEED_QUERY_KEYS = ["q", "start_date", "end_date", "category", "limit"];

export function buildApiFeedUrl(request, origin = window.location.origin) {
  const url = new URL("/api/feed", origin);
  for (const key of FEED_QUERY_KEYS) {
    const value = String(request[key] || "").trim();
    if (value) {
      url.searchParams.set(key, value);
    }
  }
  return url;
}

export function searchRequestFromParams(searchParams, defaultLimit = 100) {
  return {
    q: searchParams.get("q") || "",
    start_date: searchParams.get("start_date") || "",
    end_date: searchParams.get("end_date") || "",
    category: searchParams.get("category") || "",
    limit: searchParams.get("limit") || String(defaultLimit),
  };
}

export function searchParamsFromRequest(request) {
  const params = new URLSearchParams();
  for (const key of FEED_QUERY_KEYS) {
    const value = String(request[key] || "").trim();
    if (value) {
      params.set(key, value);
    }
  }
  return params;
}

export function hasCatalogSearch(request) {
  return Boolean(
    String(request.q || "").trim() ||
      String(request.start_date || "").trim() ||
      String(request.end_date || "").trim()
  );
}
