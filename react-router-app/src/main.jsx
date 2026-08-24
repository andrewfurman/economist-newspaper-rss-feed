import React, { createContext, useContext, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Link,
  Outlet,
  RouterProvider,
  createBrowserRouter,
  useParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpDown,
  ExternalLink,
  RefreshCw,
  Search,
} from "lucide-react";
import "./styles.css";

const FeedContext = createContext(null);

const DEFAULT_LIMIT = 100;

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <FeedTablePage /> },
      { path: "stories/:storyId", element: <StoryDetailPage /> },
    ],
  },
]);

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <FeedProvider>
      <RouterProvider router={router} />
    </FeedProvider>
  </React.StrictMode>
);

function FeedProvider({ children }) {
  const [items, setItems] = useState([]);
  const [channel, setChannel] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sourceLabel, setSourceLabel] = useState("");
  const [fetchedAt, setFetchedAt] = useState("");
  const [request, setRequest] = useState({
    feedUrl: "",
    category: "",
    limit: DEFAULT_LIMIT,
  });

  async function loadFeed(nextRequest = request) {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchFeedPayload(nextRequest);
      const parsed = parseRss(payload.xml);
      setItems(parsed.items);
      setChannel(parsed.channel);
      setSourceLabel(payload.sourceLabel || "");
      setFetchedAt(payload.fetchedAt || "");
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Could not fetch feed.");
    } finally {
      setLoading(false);
    }
  }

  const value = useMemo(
    () => ({
      channel,
      error,
      fetchedAt,
      items,
      loadFeed,
      loading,
      request,
      setRequest,
      sourceLabel,
    }),
    [channel, error, fetchedAt, items, loading, request, sourceLabel]
  );

  return <FeedContext.Provider value={value}>{children}</FeedContext.Provider>;
}

async function fetchFeedPayload(request) {
  const explicitFeedUrl = String(request.feedUrl || "").trim();
  const url = new URL("/api/feed", window.location.origin);
  if (request.category) {
    url.searchParams.set("category", request.category);
  }
  if (request.limit) {
    url.searchParams.set("limit", request.limit);
  }

  const response = explicitFeedUrl
    ? await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
    : await fetch(url);
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not fetch feed.");
    }
    return payload;
  }

  const xml = await response.text();
  if (!response.ok) {
    throw new Error(`Feed returned HTTP ${response.status}.`);
  }
  return {
    xml,
    fetchedAt: new Date().toISOString(),
    sourceLabel: "Configured feed",
  };
}

function useFeed() {
  const context = useContext(FeedContext);
  if (!context) {
    throw new Error("FeedContext is missing.");
  }
  return context;
}

function AppShell() {
  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">RSS inspector</p>
          <h1>Economist feed viewer</h1>
        </div>
      </header>
      <div className="page-shell">
        <Outlet />
      </div>
    </main>
  );
}

function FeedTablePage() {
  const {
    channel,
    error,
    fetchedAt,
    items,
    loadFeed,
    loading,
    request,
    setRequest,
    sourceLabel,
  } = useFeed();
  const [query, setQuery] = useState("");
  const [localCategory, setLocalCategory] = useState("");
  const [sort, setSort] = useState({ key: "publishedAt", direction: "desc" });

  React.useEffect(() => {
    if (!items.length && !loading && !error) {
      loadFeed();
    }
  }, []);

  const categories = useMemo(() => uniqueCategories(items), [items]);
  const visibleItems = useMemo(
    () => sortItems(filterItems(items, query, localCategory), sort),
    [items, localCategory, query, sort]
  );

  function updateRequest(key, value) {
    setRequest((current) => ({ ...current, [key]: value }));
  }

  function toggleSort(key) {
    setSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  }

  return (
    <>
      <section className="toolbar" aria-label="Feed request controls">
        <label>
          <span>Feed URL</span>
          <input
            type="password"
            value={request.feedUrl}
            onChange={(event) => updateRequest("feedUrl", event.target.value)}
            placeholder="Configured feed"
          />
        </label>
        <label>
          <span>RSS category query</span>
          <input
            value={request.category}
            onChange={(event) => updateRequest("category", event.target.value)}
            placeholder="United States"
          />
        </label>
        <label className="small-field">
          <span>Limit</span>
          <input
            type="number"
            min="1"
            max="500"
            value={request.limit}
            onChange={(event) => updateRequest("limit", event.target.value)}
          />
        </label>
        <button
          className="icon-button primary"
          type="button"
          onClick={() => loadFeed(request)}
          disabled={loading}
          title="Reload feed"
          aria-label="Reload feed"
        >
          <RefreshCw size={18} />
        </button>
      </section>

      <section className="summary-band" aria-label="Feed summary">
        <div>
          <span className="metric-value">{visibleItems.length}</span>
          <span className="metric-label">shown</span>
        </div>
        <div>
          <span className="metric-value">{items.length}</span>
          <span className="metric-label">loaded</span>
        </div>
        <div>
          <span className="metric-value">{categories.length}</span>
          <span className="metric-label">categories</span>
        </div>
        <div className="summary-copy">
          <strong>{channel?.title || "Feed"}</strong>
          <span>{sourceLabel || "Configured feed"}</span>
          <span>{fetchedAt ? formatDateTime(fetchedAt) : ""}</span>
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="filter-row" aria-label="Table filters">
        <label className="search-field">
          <Search size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title, summary, link, category"
          />
        </label>
        <label>
          <span>Category</span>
          <select
            value={localCategory}
            onChange={(event) => setLocalCategory(event.target.value)}
          >
            <option value="">All categories</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="table-wrap" aria-label="Stories">
        <table>
          <thead>
            <tr>
              <SortableHeader
                active={sort.key === "title"}
                direction={sort.direction}
                onClick={() => toggleSort("title")}
              >
                Story
              </SortableHeader>
              <SortableHeader
                active={sort.key === "publishedAt"}
                direction={sort.direction}
                onClick={() => toggleSort("publishedAt")}
              >
                Published
              </SortableHeader>
              <SortableHeader
                active={sort.key === "categoryText"}
                direction={sort.direction}
                onClick={() => toggleSort("categoryText")}
              >
                Section
              </SortableHeader>
              <SortableHeader
                active={sort.key === "description"}
                direction={sort.direction}
                onClick={() => toggleSort("description")}
              >
                Description
              </SortableHeader>
            </tr>
          </thead>
          <tbody>
            {visibleItems.map((item) => (
              <tr key={item.id}>
                <td>
                  <div className="story-stack">
                    <Link to={`/stories/${item.id}`}>{item.title}</Link>
                    <span title={item.guid}>{item.guid}</span>
                  </div>
                </td>
                <td>{item.published ? formatDateTime(item.published) : "None"}</td>
                <td>
                  <CategoryList categories={item.categories} />
                </td>
                <td>
                  <div className="description-preview">
                    {item.description || "No description"}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!visibleItems.length ? (
          <div className="empty-state">
            {loading ? "Loading feed..." : "No stories match the current filters."}
          </div>
        ) : null}
      </section>
    </>
  );
}

function SortableHeader({ active, children, direction, onClick }) {
  return (
    <th>
      <button
        className={active ? "sort-button active" : "sort-button"}
        type="button"
        onClick={onClick}
      >
        <span>{children}</span>
        <ArrowUpDown size={15} />
        <span className="sort-direction">{active ? direction : ""}</span>
      </button>
    </th>
  );
}

function StoryDetailPage() {
  const { storyId } = useParams();
  const { items, loadFeed, loading, request } = useFeed();
  const item = items.find((candidate) => candidate.id === storyId);

  React.useEffect(() => {
    if (!items.length && !loading) {
      loadFeed(request);
    }
  }, []);

  if (!item) {
    return (
      <section className="detail-layout">
        <Link className="back-link" to="/">
          <ArrowLeft size={17} />
          Stories
        </Link>
        <div className="empty-state">
          {loading ? "Loading story..." : "Story not found in the loaded feed."}
        </div>
      </section>
    );
  }

  return (
    <section className="detail-layout">
      <Link className="back-link" to="/">
        <ArrowLeft size={17} />
        Stories
      </Link>
      <header className="detail-header">
        <div>
          <p className="eyebrow">{item.categoryText || "Uncategorized"}</p>
          <h2>{item.title}</h2>
        </div>
        {item.link ? (
          <a
            className="icon-button"
            href={item.link}
            target="_blank"
            rel="noreferrer"
            title="Open article link"
            aria-label="Open article link"
          >
            <ExternalLink size={18} />
          </a>
        ) : null}
      </header>

      <dl className="metadata-grid">
        <div>
          <dt>Published</dt>
          <dd>{item.published ? formatDateTime(item.published) : "None"}</dd>
        </div>
        <div>
          <dt>GUID</dt>
          <dd>{item.guid}</dd>
        </div>
        <div>
          <dt>Link</dt>
          <dd>{item.link || "No link in RSS item"}</dd>
        </div>
        <div>
          <dt>Categories</dt>
          <dd>{item.categoryText || "None"}</dd>
        </div>
      </dl>

      <section className="detail-section">
        <h3>Description</h3>
        <p>{item.description || "No description in this RSS item."}</p>
      </section>

      <section className="detail-section">
        <h3>RSS fields</h3>
        <div className="field-list">
          {item.fields.map((field) => (
            <div className="field-row" key={`${field.name}-${field.index}`}>
              <span>{field.name}</span>
              <code>{field.value || " "}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="detail-section">
        <h3>Raw item XML</h3>
        <pre>{item.rawXml}</pre>
      </section>
    </section>
  );
}

function CategoryList({ categories }) {
  if (!categories.length) {
    return <span className="muted">None</span>;
  }
  return (
    <div className="category-list">
      {categories.map((category) => (
        <span key={category}>{category}</span>
      ))}
    </div>
  );
}

function parseRss(xmlText) {
  const document = new DOMParser().parseFromString(xmlText, "application/xml");
  const parserError = document.querySelector("parsererror");
  if (parserError) {
    throw new Error("RSS XML could not be parsed.");
  }

  const channelNode = document.querySelector("channel");
  const channel = {
    title: childText(channelNode, "title"),
    description: childText(channelNode, "description"),
    link: childText(channelNode, "link"),
    lastBuildDate: childText(channelNode, "lastBuildDate"),
  };

  const serializer = new XMLSerializer();
  const items = Array.from(document.querySelectorAll("item")).map((node, index) => {
    const fields = Array.from(node.children).map((child, childIndex) => ({
      index: childIndex,
      name: child.tagName,
      value: normalizeText(child.textContent || ""),
    }));
    const categories = Array.from(node.querySelectorAll("category"))
      .map((category) => normalizeText(category.textContent || ""))
      .filter(Boolean);
    const title = childText(node, "title") || "Untitled";
    const guid = childText(node, "guid") || childText(node, "link") || title;
    const published = childText(node, "pubDate");
    const description = stripHtml(childText(node, "description"));
    const link = childText(node, "link");
    return {
      id: stableId(`${guid}-${index}`),
      title,
      link,
      guid,
      published,
      publishedAt: Date.parse(published || "") || 0,
      description,
      categories,
      categoryText: categories.join(", "),
      fields,
      rawXml: serializer.serializeToString(node),
    };
  });

  return { channel, items };
}

function childText(node, tagName) {
  if (!node) {
    return "";
  }
  const child = Array.from(node.children).find(
    (candidate) => candidate.tagName.toLowerCase() === tagName.toLowerCase()
  );
  return normalizeText(child?.textContent || "");
}

function stripHtml(value) {
  const template = document.createElement("template");
  template.innerHTML = value;
  return normalizeText(template.content.textContent || value);
}

function normalizeText(value) {
  return value.replace(/\s+/g, " ").trim();
}

function uniqueCategories(items) {
  return Array.from(new Set(items.flatMap((item) => item.categories))).sort((a, b) =>
    a.localeCompare(b)
  );
}

function filterItems(items, query, category) {
  const normalizedQuery = query.trim().toLowerCase();
  return items.filter((item) => {
    const matchesCategory = !category || item.categories.includes(category);
    if (!matchesCategory) {
      return false;
    }
    if (!normalizedQuery) {
      return true;
    }
    return [
      item.title,
      item.guid,
      item.link,
      item.description,
      item.categoryText,
      item.published,
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedQuery);
  });
}

function sortItems(items, sort) {
  return [...items].sort((first, second) => {
    let firstValue = first[sort.key] ?? "";
    let secondValue = second[sort.key] ?? "";
    if (sort.key === "publishedAt") {
      firstValue = Number(firstValue || 0);
      secondValue = Number(secondValue || 0);
    } else {
      firstValue = String(firstValue).toLowerCase();
      secondValue = String(secondValue).toLowerCase();
    }
    if (firstValue < secondValue) {
      return sort.direction === "asc" ? -1 : 1;
    }
    if (firstValue > secondValue) {
      return sort.direction === "asc" ? 1 : -1;
    }
    return 0;
  });
}

function stableId(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function formatDateTime(value) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}
