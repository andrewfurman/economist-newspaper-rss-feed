import React, {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  RouterProvider,
  createBrowserRouter,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpDown,
  Braces,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
} from "lucide-react";

import {
  buildApiFeedUrl,
  hasCatalogSearch,
  searchParamsFromRequest,
  searchRequestFromParams,
} from "./feed-request.js";
import "./styles.css";

const FeedContext = createContext(null);
const DEFAULT_LIMIT = 100;
const DEFAULT_REQUEST = {
  feedUrl: "",
  q: "",
  start_date: "",
  end_date: "",
  category: "",
  limit: DEFAULT_LIMIT,
};

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/recent" replace /> },
      { path: "raw", element: <RawFeedPage /> },
      { path: "recent", element: <RecentArticlesPage /> },
      { path: "search", element: <CatalogSearchPage /> },
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
  const [rawXml, setRawXml] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sourceLabel, setSourceLabel] = useState("");
  const [fetchedAt, setFetchedAt] = useState("");
  const [request, setRequest] = useState(DEFAULT_REQUEST);

  async function loadFeed(nextRequest = request) {
    const normalizedRequest = { ...DEFAULT_REQUEST, ...nextRequest };
    setRequest(normalizedRequest);
    setLoading(true);
    setError("");
    setItems([]);
    setChannel(null);
    setRawXml("");
    try {
      const payload = await fetchFeedPayload(normalizedRequest);
      const parsed = parseRss(payload.xml);
      setRawXml(payload.xml);
      setItems(parsed.items);
      setChannel(parsed.channel);
      setSourceLabel(payload.sourceLabel || "");
      setFetchedAt(payload.fetchedAt || "");
      return parsed;
    } catch (fetchError) {
      setError(
        fetchError instanceof Error ? fetchError.message : "Could not fetch feed."
      );
      return null;
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
      rawXml,
      request,
      sourceLabel,
    }),
    [channel, error, fetchedAt, items, loading, rawXml, request, sourceLabel]
  );

  return <FeedContext.Provider value={value}>{children}</FeedContext.Provider>;
}

async function fetchFeedPayload(request) {
  const explicitFeedUrl = String(request.feedUrl || "").trim();
  const url = buildApiFeedUrl(request);
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
        <div className="brand-block">
          <p className="eyebrow">Private RSS reader</p>
          <h1>The Economist</h1>
        </div>
        <nav className="tabs" aria-label="Reader views">
          <NavLink to="/raw">
            <Braces size={17} />
            Raw RSS
          </NavLink>
          <NavLink to="/recent">
            <FileText size={17} />
            Recent articles
          </NavLink>
          <NavLink to="/search">
            <Search size={17} />
            Search
          </NavLink>
        </nav>
      </header>
      <div className="page-shell">
        <Outlet />
      </div>
    </main>
  );
}

function RawFeedPage() {
  const { channel, error, items, loadFeed, loading, rawXml } = useFeed();
  const [request, setRequest] = useState({ category: "", limit: DEFAULT_LIMIT });
  const [format, setFormat] = useState("formatted");
  const loaded = useRef(false);

  React.useEffect(() => {
    if (!loaded.current) {
      loaded.current = true;
      loadFeed(request);
    }
  }, []);

  return (
    <section className="view-layout">
      <ViewHeader eyebrow="RSS document" title="Raw RSS feed">
        <div className="segmented" aria-label="XML formatting">
          <button
            className={format === "formatted" ? "active" : ""}
            type="button"
            onClick={() => setFormat("formatted")}
          >
            Formatted
          </button>
          <button
            className={format === "raw" ? "active" : ""}
            type="button"
            onClick={() => setFormat("raw")}
          >
            Compact
          </button>
        </div>
      </ViewHeader>
      <FeedRequestToolbar
        request={request}
        setRequest={setRequest}
        loading={loading}
        onReload={() => loadFeed(request)}
      />
      <FeedSummary channel={channel} items={items} label="RSS items" />
      {error ? <div className="error-banner">{error}</div> : null}
      <pre className="raw-feed" aria-label="Raw RSS XML">
        {loading && !rawXml
          ? "Loading feed..."
          : format === "formatted"
            ? formatXml(rawXml)
            : rawXml}
      </pre>
    </section>
  );
}

function RecentArticlesPage() {
  const { channel, error, items, loadFeed, loading } = useFeed();
  const [request, setRequest] = useState({ category: "", limit: DEFAULT_LIMIT });
  const [localQuery, setLocalQuery] = useState("");
  const [localCategory, setLocalCategory] = useState("");
  const loaded = useRef(false);

  React.useEffect(() => {
    if (!loaded.current) {
      loaded.current = true;
      loadFeed(request);
    }
  }, []);

  const categories = useMemo(() => uniqueCategories(items), [items]);
  const visibleItems = useMemo(
    () => filterItems(items, localQuery, localCategory),
    [items, localCategory, localQuery]
  );

  return (
    <section className="view-layout">
      <ViewHeader eyebrow="Current edition" title="Recent articles" />
      <FeedRequestToolbar
        request={request}
        setRequest={setRequest}
        loading={loading}
        onReload={() => loadFeed(request)}
      />
      <FeedSummary channel={channel} items={items} shown={visibleItems.length} />
      {error ? <div className="error-banner">{error}</div> : null}
      <section className="filter-row" aria-label="Table filters">
        <label className="search-field">
          <Search size={16} />
          <input
            value={localQuery}
            onChange={(event) => setLocalQuery(event.target.value)}
            placeholder="Filter loaded articles"
          />
        </label>
        <label>
          <span>Section</span>
          <select
            value={localCategory}
            onChange={(event) => setLocalCategory(event.target.value)}
          >
            <option value="">All sections</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </label>
      </section>
      <StoryTable
        items={visibleItems}
        loading={loading}
        returnTo="/recent"
        emptyMessage="No recent articles match the current filters."
      />
    </section>
  );
}

function CatalogSearchPage() {
  const { channel, error, items, loadFeed, loading } = useFeed();
  const [searchParams, setSearchParams] = useSearchParams();
  const [request, setRequest] = useState(() =>
    searchRequestFromParams(searchParams, DEFAULT_LIMIT)
  );
  const [formError, setFormError] = useState("");
  const searchKey = searchParams.toString();
  const searched = hasCatalogSearch(searchRequestFromParams(searchParams));

  React.useEffect(() => {
    const nextRequest = searchRequestFromParams(searchParams, DEFAULT_LIMIT);
    setRequest(nextRequest);
    if (hasCatalogSearch(nextRequest)) {
      loadFeed(nextRequest);
    }
  }, [searchKey]);

  function submitSearch(event) {
    event.preventDefault();
    if (!hasCatalogSearch(request)) {
      setFormError("Enter keywords or a start or end date.");
      return;
    }
    if (
      request.start_date &&
      request.end_date &&
      request.start_date > request.end_date
    ) {
      setFormError("Start date must be on or before end date.");
      return;
    }
    setFormError("");
    setSearchParams(searchParamsFromRequest(request));
  }

  function updateRequest(key, value) {
    setRequest((current) => ({ ...current, [key]: value }));
  }

  const results = searched ? items : [];
  return (
    <section className="view-layout">
      <ViewHeader eyebrow="Local catalog" title="Search articles" />
      <form className="catalog-search" onSubmit={submitSearch}>
        <label className="query-field">
          <span>Keywords</span>
          <div className="input-with-icon">
            <Search size={17} />
            <input
              value={request.q}
              onChange={(event) => updateRequest("q", event.target.value)}
              placeholder="Iran"
            />
          </div>
        </label>
        <label>
          <span>Start date</span>
          <input
            type="date"
            value={request.start_date}
            onChange={(event) => updateRequest("start_date", event.target.value)}
          />
        </label>
        <label>
          <span>End date</span>
          <input
            type="date"
            value={request.end_date}
            onChange={(event) => updateRequest("end_date", event.target.value)}
          />
        </label>
        <label>
          <span>Section</span>
          <input
            value={request.category}
            onChange={(event) => updateRequest("category", event.target.value)}
            placeholder="All sections"
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
        <button className="command-button" type="submit" disabled={loading}>
          <Search size={17} />
          Search
        </button>
      </form>
      {formError ? <div className="error-banner">{formError}</div> : null}
      {error ? <div className="error-banner">{error}</div> : null}
      {searched ? (
        <FeedSummary channel={channel} items={results} label="matches" />
      ) : null}
      <StoryTable
        items={results}
        loading={loading}
        returnTo={`/search${searchKey ? `?${searchKey}` : ""}`}
        emptyMessage={
          searched ? "No catalog articles match this search." : "No search has been run."
        }
      />
    </section>
  );
}

function ViewHeader({ children, eyebrow, title }) {
  return (
    <header className="view-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {children}
    </header>
  );
}

function FeedRequestToolbar({ loading, onReload, request, setRequest }) {
  function updateRequest(key, value) {
    setRequest((current) => ({ ...current, [key]: value }));
  }

  return (
    <section className="toolbar compact-toolbar" aria-label="Feed request controls">
      <label>
        <span>RSS section query</span>
        <input
          value={request.category}
          onChange={(event) => updateRequest("category", event.target.value)}
          placeholder="All sections"
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
        onClick={onReload}
        disabled={loading}
        title="Reload feed"
        aria-label="Reload feed"
      >
        <RefreshCw size={18} />
      </button>
    </section>
  );
}

function FeedSummary({ channel, items, label = "loaded", shown }) {
  const categories = uniqueCategories(items);
  return (
    <section
      className={shown === undefined ? "summary-band compact-summary" : "summary-band"}
      aria-label="Feed summary"
    >
      {shown !== undefined ? (
        <div>
          <span className="metric-value">{shown}</span>
          <span className="metric-label">shown</span>
        </div>
      ) : null}
      <div>
        <span className="metric-value">{items.length}</span>
        <span className="metric-label">{label}</span>
      </div>
      <div>
        <span className="metric-value">{categories.length}</span>
        <span className="metric-label">sections</span>
      </div>
      <div className="summary-copy">
        <strong>{channel?.title || "RSS feed"}</strong>
        <span>{channel?.description || ""}</span>
      </div>
    </section>
  );
}

function StoryTable({ emptyMessage, items, loading, returnTo }) {
  const [sort, setSort] = useState({ key: "publishedAt", direction: "desc" });
  const sortedItems = useMemo(() => sortItems(items, sort), [items, sort]);

  function toggleSort(key) {
    setSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  }

  return (
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
          {sortedItems.map((item) => (
            <tr key={item.id}>
              <td>
                <div className="story-stack">
                  <Link to={`/stories/${item.id}`} state={{ from: returnTo }}>
                    {item.title}
                  </Link>
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
      {!sortedItems.length ? (
        <div className="empty-state">
          {loading ? "Loading feed..." : emptyMessage}
        </div>
      ) : null}
    </section>
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
  const location = useLocation();
  const { items, loadFeed, loading, request } = useFeed();
  const item = items.find((candidate) => candidate.id === storyId);
  const backTarget = location.state?.from || "/recent";
  const loaded = useRef(false);

  React.useEffect(() => {
    if (!items.length && !loading && !loaded.current) {
      loaded.current = true;
      loadFeed(request);
    }
  }, []);

  if (!item) {
    return (
      <section className="detail-layout">
        <Link className="back-link" to={backTarget}>
          <ArrowLeft size={17} />
          Articles
        </Link>
        <div className="empty-state">
          {loading ? "Loading story..." : "Story not found in the loaded feed."}
        </div>
      </section>
    );
  }

  return (
    <section className="detail-layout">
      <Link className="back-link" to={backTarget}>
        <ArrowLeft size={17} />
        Articles
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
    return String(second.id).localeCompare(String(first.id));
  });
}

function formatXml(xml) {
  if (!xml) {
    return "";
  }
  const lines = xml.replace(/>\s*</g, ">\n<").split("\n");
  let depth = 0;
  return lines
    .map((line) => {
      const trimmed = line.trim();
      if (/^<\//.test(trimmed)) {
        depth = Math.max(0, depth - 1);
      }
      const output = `${"  ".repeat(depth)}${trimmed}`;
      if (
        /^<[^!?/][^>]*>$/.test(trimmed) &&
        !/<\/[^>]+>$/.test(trimmed) &&
        !/\/>$/.test(trimmed)
      ) {
        depth += 1;
      }
      return output;
    })
    .join("\n");
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
