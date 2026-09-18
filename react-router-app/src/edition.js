export const EDITION_NS = "https://github.com/andrewfurman/economist-newspaper-rss-feed/ns/1.0";

export function editionLabel(kind) {
  return { print_edition: "Print Edition", online_only: "Online Only" }[kind] || "";
}

export function normalizeEdition({ title, categories = [], kind = "" }) {
  // Accept feeds produced before edition metadata gained its own namespace.
  const legacyKind = categories.includes("Print Edition") ? "print_edition"
    : categories.includes("Online Only") ? "online_only" : "";
  const editionKind = kind || legacyKind;
  const suffix = legacyKind ? ` [${editionLabel(legacyKind)}]` : "";
  return {
    title: suffix && title.endsWith(suffix) ? title.slice(0, -suffix.length) : title,
    categories: categories.filter((category) => !["print edition", "online only"].includes(category.toLowerCase())),
    editionKind,
  };
}
