// US print-edition contents order; daily briefs lead and Online is an extra view.
// Regional order varies by edition. Reference: docs/READER_SECTIONS.md.
export const NEWSPAPER_ORDER = [
  "The World in Brief", "United States in Brief", "The World This Week",
  "Leaders", "Letters", "By Invitation", "Briefing", "United States",
  "The Americas", "Asia", "China", "Middle East and Africa", "Europe", "Britain",
  "International", "Essay", "Special Report", "1843", "Business",
  "Finance and Economics", "Science and Technology", "Culture", "Graphic Detail",
  "Economic and Financial Indicators", "Obituary",
];

export function sectionForItem(item) {
  if (item.categories.includes("The World in Brief")) return "The World in Brief";
  if (/^(?:the )?(?:us|united states) in brief(?:\s*:|$)/i.test(item.title)) {
    return "United States in Brief";
  }
  return item.categories.find((category) => NEWSPAPER_ORDER.includes(category))
    || item.categories[0] || "Uncategorized";
}

export function compareSections(a, b) {
  const rank = (name) => name === "Online" ? Infinity
    : NEWSPAPER_ORDER.includes(name) ? NEWSPAPER_ORDER.indexOf(name) : NEWSPAPER_ORDER.length;
  return rank(a) - rank(b) || a.localeCompare(b);
}

export function groupItemsBySection(items, selectedSection = "") {
  const groups = new Map();
  for (const item of items) {
    const section = sectionForItem(item);
    if (!groups.has(section)) groups.set(section, []);
    groups.get(section).push(item);
  }
  const online = items.filter((item) => item.editionKind === "online_only");
  if (online.length) groups.set("Online", online);
  return Array.from(groups, ([name, groupedItems]) => ({ name, items: groupedItems }))
    .filter((group) => !selectedSection || group.name === selectedSection)
    .sort((a, b) => compareSections(a.name, b.name));
}

// The cache can contain URL aliases for the same publisher GUID. Prefer a
// specific section over the generic /in-brief/ alias, without merging titles
// or combining categories from conflicting copies. Raw XML stays untouched.
export function deduplicateArticles(items) {
  const preferred = new Map();
  const score = (item) => item.categories.some((c) => c !== "In Brief") ? 1 : 0;
  for (const item of items) {
    const key = item.guid || item.link || item.id;
    const previous = preferred.get(key);
    if (!previous || score(item) > score(previous)) preferred.set(key, item);
  }
  return [...preferred.values()];
}
