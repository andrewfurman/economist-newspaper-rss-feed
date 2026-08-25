export function isoToDateParts(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) {
    return { month: "", day: "", year: "" };
  }
  return { month: match[2], day: match[3], year: match[1] };
}

export function datePartsToIso(parts) {
  const month = String(parts.month || "").padStart(2, "0");
  const day = String(parts.day || "").padStart(2, "0");
  const year = String(parts.year || "");
  if (!/^\d{2}$/.test(month) || !/^\d{2}$/.test(day) || !/^\d{4}$/.test(year)) {
    return "";
  }
  const candidate = new Date(`${year}-${month}-${day}T00:00:00Z`);
  if (
    Number.isNaN(candidate.getTime()) ||
    candidate.getUTCFullYear() !== Number(year) ||
    candidate.getUTCMonth() + 1 !== Number(month) ||
    candidate.getUTCDate() !== Number(day)
  ) {
    return "";
  }
  return `${year}-${month}-${day}`;
}

export function numericSegment(value, maxLength) {
  return String(value || "").replace(/\D/g, "").slice(0, maxLength);
}
