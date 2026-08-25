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
  BarChart3,
  Braces,
  ChevronDown,
  ChevronRight,
  Database,
  ExternalLink,
  FileText,
  Filter,
  RefreshCw,
  Search,
} from "lucide-react";

import { datePartsToIso, isoToDateParts, numericSegment } from "./date-input.js";
import {
  buildApiFeedUrl,
  buildArticleTextUrl,
  buildStatsUrl,
  hasCatalogSearch,
  searchParamsFromRequest,
  searchRequestFromParams,
} from "./feed-request.js";
import "./styles.css";

const FeedContext = createContext(null);
const DatabaseContext = createContext(null);
const DEFAULT_LIMIT = 200;
const DEFAULT_REQUEST = {
  feedUrl: "",
  q: "",
  start_date: "",
  end_date: "",
  category: "",
  limit: DEFAULT_LIMIT,
};
const basePath = import.meta.env.BASE_URL.replace(/\/$/, "") || "/";

const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <AppShell />,
      children: [
        { index: true, element: <Navigate to="/recent" replace /> },
        { path: "raw", element: <RawFeedPage /> },
        { path: "recent", element: <RecentArticlesPage /> },
        { path: "search", element: <CatalogSearchPage /> },
        { path: "stats", element: <DatabaseStatsPage /> },
        { path: "stories/:storyId", element: <StoryDetailPage /> },
      ],
    },
  ],
  { basename: basePath }
);

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <DatabaseProvider>
      <FeedProvider>
        <RouterProvider router={router} />
      </FeedProvider>
    </DatabaseProvider>
  </React.StrictMode>
);

function FeedProvider({ children }) {
  const [items, setItems] = useState([]);
  const [channel, setChannel] = useState(null);
  const [rawXml, setRawXml] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
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
    () => ({ channel, error, items, loadFeed, loading, rawXml, request }),
    [channel, error, items, loading, rawXml, request]
  );

  return <FeedContext.Provider value={value}>{children}</FeedContext.Provider>;
}

function DatabaseProvider({ children }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadStats() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(buildStatsUrl());
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Could not load database statistics.");
      }
      setStats(payload);
      return payload;
    } catch (fetchError) {
      setError(
        fetchError instanceof Error
          ? fetchError.message
          : "Could not load database statistics."
      );
      return null;
    } finally {
      setLoading(false);
    }
  }

  const loaded = useRef(false);
  React.useEffect(() => {
    if (!loaded.current) {
      loaded.current = true;
      loadStats();
    }
  }, []);

  const sections = stats?.sections?.values || [];
  const value = useMemo(
    () => ({ error, loadStats, loading, sections, stats }),
    [error, loading, sections, stats]
  );
  return <DatabaseContext.Provider value={value}>{children}</DatabaseContext.Provider>;
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
  return { xml };
}

function useFeed() {
  const context = useContext(FeedContext);
  if (!context) {
    throw new Error("FeedContext is missing.");
  }
  return context;
}

function useDatabase() {
  const context = useContext(DatabaseContext);
  if (!context) {
    throw new Error("DatabaseContext is missing.");
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
          <NavLink to="/stats">
            <Database size={17} />
            Database stats
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
  const { channel, error, items, loadFeed, loading } = useFeed();
  const [request, setRequest] = useState({ limit: DEFAULT_LIMIT });
  const [section, setSection] = useState("");
  const loaded = useRef(false);

  React.useEffect(() => {
    if (!loaded.current) {
      loaded.current = true;
      loadFeed(request);
    }
  }, []);

  const categories = useMemo(() => uniqueCategories(items), [items]);
  const visibleItemCount = section
    ? items.filter((item) => item.categories.includes(section)).length
    : items.length;
  return (
    <section className="view-layout">
      <ViewHeader eyebrow="RSS document" title="Raw RSS feed" />
      <FeedRequestToolbar
        request={request}
        setRequest={setRequest}
        loading={loading}
        onReload={() => loadFeed(request)}
      />
      <div className="view-controls">
        <label className="section-select">
          <span>Section</span>
          <select value={section} onChange={(event) => setSection(event.target.value)}>
            <option value="">All sections</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </label>
        <span className="result-count">
          {visibleItemCount.toLocaleString()}
          {section ? ` of ${items.length.toLocaleString()}` : ""} RSS items
        </span>
      </div>
      {error ? <div className="error-banner">{error}</div> : null}
      <FormattedRssViewer
        channel={channel}
        items={items}
        loading={loading}
        section={section}
      />
    </section>
  );
}

function FormattedRssViewer({ channel, items, loading, section }) {
  const groups = useMemo(() => groupItemsBySection(items, section), [items, section]);
  const [expandedSections, setExpandedSections] = useState(new Set());

  React.useEffect(() => {
    setExpandedSections(new Set(groups.length ? [groups[0].name] : []));
  }, [section, items]);

  function setSectionOpen(name, open) {
    setExpandedSections((current) => {
      const next = new Set(current);
      if (open) {
        next.add(name);
      } else {
        next.delete(name);
      }
      return next;
    });
  }

  if (!items.length) {
    return (
      <div className="empty-state">
        {loading ? "Loading formatted RSS..." : "The RSS feed has no items."}
      </div>
    );
  }

  return (
    <section className="formatted-rss" aria-label="Formatted RSS">
      <header className="rss-channel-header">
        <div>
          <strong>{channel?.title || "RSS feed"}</strong>
          <span>{channel?.description || ""}</span>
        </div>
        <div className="expand-actions">
          <button
            type="button"
            onClick={() => setExpandedSections(new Set(groups.map((group) => group.name)))}
          >
            <ChevronDown size={16} />
            Expand all
          </button>
          <button type="button" onClick={() => setExpandedSections(new Set())}>
            <ChevronRight size={16} />
            Collapse all
          </button>
        </div>
      </header>
      <div className="rss-section-list">
        {groups.map((group) => (
          <details
            className="rss-section"
            key={group.name}
            open={expandedSections.has(group.name)}
            onToggle={(event) => setSectionOpen(group.name, event.currentTarget.open)}
          >
            <summary>
              <span>{group.name}</span>
              <span>{group.items.length.toLocaleString()}</span>
            </summary>
            <div className="rss-card-grid">
              {group.items.map((item) => (
                <details className="rss-item-card" key={item.id}>
                  <summary>
                    <span>{item.title}</span>
                    <time>{item.published ? formatDateTime(item.published) : "Undated"}</time>
                  </summary>
                  <div className="rss-item-content">
                    {item.link ? (
                      <a href={item.link} target="_blank" rel="noreferrer">
                        Original article <ExternalLink size={14} />
                      </a>
                    ) : null}
                    <CategoryList categories={item.categories} />
                    {item.description ? <p>{item.description}</p> : null}
                    <dl className="rss-field-list">
                      {item.fields.map((field) => (
                        <div key={`${field.name}-${field.index}`}>
                          <dt>{field.name}</dt>
                          <dd>{field.value || "None"}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                </details>
              ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function RecentArticlesPage() {
  const { error, items, loadFeed, loading } = useFeed();
  const [request, setRequest] = useState({ limit: DEFAULT_LIMIT });
  const [localQuery, setLocalQuery] = useState("");
  const loaded = useRef(false);

  React.useEffect(() => {
    if (!loaded.current) {
      loaded.current = true;
      loadFeed(request);
    }
  }, []);

  const visibleItems = useMemo(
    () => filterItems(items, localQuery),
    [items, localQuery]
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
      {error ? <div className="error-banner">{error}</div> : null}
      <label className="search-field recent-search">
        <Search size={16} />
        <input
          value={localQuery}
          onChange={(event) => setLocalQuery(event.target.value)}
          placeholder="Filter recent articles"
        />
      </label>
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
  const { error, items, loadFeed, loading } = useFeed();
  const { sections } = useDatabase();
  const [searchParams, setSearchParams] = useSearchParams();
  const [request, setRequest] = useState(() =>
    searchRequestFromParams(searchParams, DEFAULT_LIMIT)
  );
  const [formError, setFormError] = useState("");
  const [dateValidity, setDateValidity] = useState({ start: true, end: true });
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
      setFormError("Enter keywords, a section, or a start or end date.");
      return;
    }
    if (!dateValidity.start || !dateValidity.end) {
      setFormError("Enter complete, valid dates.");
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
        <SegmentedDateInput
          label="Start date"
          value={request.start_date}
          onChange={(value) => updateRequest("start_date", value)}
          onValidityChange={(valid) =>
            setDateValidity((current) => ({ ...current, start: valid }))
          }
        />
        <SegmentedDateInput
          label="End date"
          value={request.end_date}
          onChange={(value) => updateRequest("end_date", value)}
          onValidityChange={(valid) =>
            setDateValidity((current) => ({ ...current, end: valid }))
          }
        />
        <label>
          <span>Section</span>
          <select
            value={request.category}
            onChange={(event) => updateRequest("category", event.target.value)}
          >
            <option value="">All sections</option>
            {sections.map((section) => (
              <option key={section.name} value={section.name}>
                {section.name}
              </option>
            ))}
          </select>
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

function SegmentedDateInput({ label, onChange, onValidityChange, value }) {
  const initialParts = isoToDateParts(value);
  const [parts, setParts] = useState(initialParts);
  const [valid, setValid] = useState(true);
  const monthRef = useRef(null);
  const dayRef = useRef(null);
  const yearRef = useRef(null);
  const emittedValue = useRef(value);

  React.useEffect(() => {
    if (value !== emittedValue.current) {
      setParts(isoToDateParts(value));
      emittedValue.current = value;
      setValid(true);
      onValidityChange(true);
    }
  }, [value]);

  function updatePart(key, rawValue, maxLength, nextRef) {
    const nextValue = numericSegment(rawValue, maxLength);
    const nextParts = { ...parts, [key]: nextValue };
    setParts(nextParts);
    const anyValue = Boolean(nextParts.month || nextParts.day || nextParts.year);
    const complete =
      nextParts.month.length === 2 &&
      nextParts.day.length === 2 &&
      nextParts.year.length === 4;
    const isoValue = complete ? datePartsToIso(nextParts) : "";
    const nextValid = !anyValue || Boolean(isoValue) || !complete;
    setValid(nextValid);
    onValidityChange(nextValid && (!anyValue || complete));
    emittedValue.current = isoValue;
    onChange(isoValue);
    if (nextValue.length === maxLength && nextRef?.current) {
      nextRef.current.focus();
      nextRef.current.select();
    }
  }

  return (
    <fieldset className={valid ? "segmented-date" : "segmented-date invalid"}>
      <legend>{label}</legend>
      <div>
        <input
          ref={monthRef}
          inputMode="numeric"
          aria-label={`${label} month`}
          placeholder="MM"
          value={parts.month}
          onChange={(event) => updatePart("month", event.target.value, 2, dayRef)}
          maxLength={2}
        />
        <span>/</span>
        <input
          ref={dayRef}
          inputMode="numeric"
          aria-label={`${label} day`}
          placeholder="DD"
          value={parts.day}
          onChange={(event) => updatePart("day", event.target.value, 2, yearRef)}
          onKeyDown={(event) => {
            if (event.key === "Backspace" && !parts.day) {
              monthRef.current?.focus();
            }
          }}
          maxLength={2}
        />
        <span>/</span>
        <input
          ref={yearRef}
          inputMode="numeric"
          aria-label={`${label} year`}
          placeholder="YYYY"
          value={parts.year}
          onChange={(event) => updatePart("year", event.target.value, 4)}
          onKeyDown={(event) => {
            if (event.key === "Backspace" && !parts.year) {
              dayRef.current?.focus();
            }
          }}
          maxLength={4}
        />
      </div>
    </fieldset>
  );
}

function DatabaseStatsPage() {
  const { error, loadStats, loading, stats } = useDatabase();
  const articles = stats?.articles;
  const sections = stats?.sections?.values || [];
  const refresh = stats?.refresh;
  const catalog = stats?.catalog;

  return (
    <section className="view-layout">
      <ViewHeader eyebrow="SQLite catalog" title="Database stats">
        <button
          className="icon-button"
          type="button"
          onClick={loadStats}
          disabled={loading}
          title="Reload database stats"
          aria-label="Reload database stats"
        >
          <RefreshCw size={18} />
        </button>
      </ViewHeader>
      {error ? <div className="error-banner">{error}</div> : null}
      {!stats ? (
        <div className="empty-state">
          {loading ? "Loading database stats..." : "Database stats are unavailable."}
        </div>
      ) : (
        <>
          <section className="metric-grid" aria-label="Database totals">
            <MetricCard label="Total articles" value={formatNumber(articles.total)} />
            <MetricCard label="Full-text articles" value={formatNumber(articles.full_text)} />
            <MetricCard label="Sections" value={formatNumber(stats.sections.total)} />
            <MetricCard
              label="Full-text coverage"
              value={`${articles.full_text_coverage_percent}%`}
            />
          </section>
          <section className="stats-detail-grid">
            <div className="stats-panel">
              <h3>Database health</h3>
              <dl className="stats-list">
                <StatRow label="Last database refresh" value={formatOptionalDate(refresh.last_refresh_at)} />
                <StatRow label="Metadata only" value={formatNumber(articles.metadata_only)} />
                <StatRow label="Queued downloads" value={formatNumber(articles.queued)} />
                <StatRow label="Earliest publication" value={formatOptionalDate(articles.earliest_published_at)} />
                <StatRow label="Latest publication" value={formatOptionalDate(articles.latest_published_at)} />
                <StatRow label="Search index" value={catalog.fts5_enabled ? "FTS5 active" : "Fallback scan"} />
              </dl>
            </div>
            <div className="stats-panel">
              <h3>Current catalog</h3>
              <dl className="stats-list">
                <StatRow label="Current issue" value={refresh.current_issue_date || refresh.current_issue_id || "Unknown"} />
                <StatRow
                  label="Default feed articles"
                  value={formatNumber(refresh.default_feed_article_count)}
                />
                <StatRow label="Issues discovered" value={formatNumber(catalog.issues_discovered)} />
                <StatRow label="Issues failed" value={formatNumber(catalog.issues_failed)} />
                <StatRow label="Last catalog discovery" value={formatOptionalDate(catalog.last_discovery_at)} />
                <StatRow
                  label="Last refresh result"
                  value={
                    refresh.last_stop_reason
                      ? humanizeStatus(refresh.last_stop_reason)
                      : "Completed"
                  }
                />
              </dl>
            </div>
          </section>
          <section className="stats-table-section">
            <header>
              <div>
                <p className="eyebrow">Coverage</p>
                <h3>Articles by section</h3>
              </div>
              <span>{sections.length.toLocaleString()} sections</span>
            </header>
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>Section</th>
                    <th>Articles</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.map((section) => (
                    <tr key={section.name}>
                      <td>{section.name}</td>
                      <td>{formatNumber(section.article_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="stats-table-section">
            <header>
              <div>
                <p className="eyebrow">Fetch state</p>
                <h3>Article status</h3>
              </div>
            </header>
            <div className="status-grid">
              {Object.entries(articles.content_statuses).map(([status, count]) => (
                <div key={status}>
                  <span>{humanizeStatus(status)}</span>
                  <strong>{formatNumber(count)}</strong>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </section>
  );
}

function MetricCard({ label, value }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatRow({ label, value }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value ?? "Unknown"}</dd>
    </div>
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
  return (
    <section className="toolbar limit-toolbar" aria-label="Feed request controls">
      <label className="small-field">
        <span>Limit</span>
        <input
          type="number"
          min="1"
          max="500"
          value={request.limit}
          onChange={(event) =>
            setRequest((current) => ({ ...current, limit: event.target.value }))
          }
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

function StoryTable({ emptyMessage, items, loading, returnTo }) {
  const [sort, setSort] = useState({ key: "publishedAt", direction: "desc" });
  const [sectionFilter, setSectionFilter] = useState("");
  const categories = useMemo(() => uniqueCategories(items), [items]);
  const filteredItems = useMemo(
    () =>
      sectionFilter
        ? items.filter((item) => item.categories.includes(sectionFilter))
        : items,
    [items, sectionFilter]
  );
  const sortedItems = useMemo(
    () => sortItems(filteredItems, sort),
    [filteredItems, sort]
  );

  function toggleSort(key) {
    setSort((current) => ({
      key,
      direction:
        current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  }

  return (
    <>
      <div className="table-result-line">
        <span>{sortedItems.length.toLocaleString()} articles</span>
        {sectionFilter ? (
          <button type="button" onClick={() => setSectionFilter("")}>
            {sectionFilter} &times;
          </button>
        ) : null}
      </div>
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
              <SectionHeader
                categories={categories}
                direction={sort.direction}
                filter={sectionFilter}
                isSorted={sort.key === "categoryText"}
                onFilter={setSectionFilter}
                onSort={() => toggleSort("categoryText")}
              />
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
                    {item.link ? (
                      <a
                        className="source-link"
                        href={item.link}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Original article <ExternalLink size={12} />
                      </a>
                    ) : null}
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

function SectionHeader({ categories, direction, filter, isSorted, onFilter, onSort }) {
  const [open, setOpen] = useState(false);
  return (
    <th className="section-header-cell">
      <div className="section-header-actions">
        <button
          className={isSorted ? "sort-button active" : "sort-button"}
          type="button"
          onClick={onSort}
        >
          <span>Section</span>
          <ArrowUpDown size={15} />
          <span className="sort-direction">{isSorted ? direction : ""}</span>
        </button>
        <button
          className={filter ? "header-filter active" : "header-filter"}
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          title="Filter sections"
          aria-label="Filter sections"
        >
          <Filter size={15} />
        </button>
      </div>
      {open ? (
        <div className="section-filter-panel">
          <label>
            <span>Filter section</span>
            <select value={filter} onChange={(event) => onFilter(event.target.value)}>
              <option value="">All sections</option>
              {categories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
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
  const [articleText, setArticleText] = useState("");
  const [articleLoading, setArticleLoading] = useState(false);
  const [articleError, setArticleError] = useState("");

  React.useEffect(() => {
    if (!items.length && !loading && !loaded.current) {
      loaded.current = true;
      loadFeed(request);
    }
  }, []);

  React.useEffect(() => {
    if (!item) {
      return undefined;
    }
    const controller = new AbortController();
    let active = true;
    setArticleLoading(true);
    setArticleError("");
    setArticleText("");
    fetch(buildArticleTextUrl(item), { signal: controller.signal })
      .then(async (response) => {
        const text = await response.text();
        if (!response.ok) {
          throw new Error(
            response.status === 404
              ? "Full text is not cached for this article."
              : `Article text returned HTTP ${response.status}.`
          );
        }
        if (active) {
          setArticleText(text.trim());
        }
      })
      .catch((fetchError) => {
        if (active && fetchError.name !== "AbortError") {
          setArticleError(fetchError.message || "Could not load article text.");
        }
      })
      .finally(() => {
        if (active) {
          setArticleLoading(false);
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [item?.id]);

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
            title="Open original article"
            aria-label="Open original article"
          >
            <ExternalLink size={18} />
          </a>
        ) : null}
      </header>
      <dl className="metadata-grid compact-metadata">
        <div>
          <dt>Published</dt>
          <dd>{item.published ? formatDateTime(item.published) : "None"}</dd>
        </div>
        <div>
          <dt>Sections</dt>
          <dd>{item.categoryText || "None"}</dd>
        </div>
        <div>
          <dt>Original</dt>
          <dd>
            {item.link ? (
              <a href={item.link} target="_blank" rel="noreferrer">
                Open article <ExternalLink size={13} />
              </a>
            ) : (
              "Included in RSS description"
            )}
          </dd>
        </div>
      </dl>
      <section className="article-reader">
        <header>
          <p className="eyebrow">Plain text</p>
          <h3>Full article</h3>
        </header>
        {articleLoading ? <div className="article-loading">Loading full text...</div> : null}
        {articleError ? <div className="error-banner">{articleError}</div> : null}
        {articleText ? (
          <div className="article-body">
            {articleText.split(/\n{2,}/).map((paragraph, index) => (
              <p key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>
            ))}
          </div>
        ) : null}
      </section>
      <section className="detail-section">
        <h3>RSS description</h3>
        <p>{item.description || "No description in this RSS item."}</p>
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

function groupItemsBySection(items, selectedSection) {
  const groups = new Map();
  for (const item of items) {
    if (selectedSection && !item.categories.includes(selectedSection)) {
      continue;
    }
    const section = selectedSection || item.categories[0] || "Uncategorized";
    if (!groups.has(section)) {
      groups.set(section, []);
    }
    groups.get(section).push(item);
  }
  return Array.from(groups, ([name, groupedItems]) => ({ name, items: groupedItems })).sort(
    (first, second) => first.name.localeCompare(second.name)
  );
}

function filterItems(items, query) {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return items;
  }
  return items.filter((item) =>
    [
      item.title,
      item.link,
      item.description,
      item.categoryText,
      item.published,
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedQuery)
  );
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

function formatOptionalDate(value) {
  return value ? formatDateTime(value) : "Unknown";
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function humanizeStatus(value) {
  return String(value || "unknown")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
