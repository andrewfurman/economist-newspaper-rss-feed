import assert from "node:assert/strict";
import test from "node:test";

import {
  datePartsToIso,
  isoToDateParts,
  numericSegment,
} from "./date-input.js";

test("date parts round-trip through ISO format", () => {
  const parts = isoToDateParts("2026-01-05");

  assert.deepEqual(parts, { month: "01", day: "05", year: "2026" });
  assert.equal(datePartsToIso(parts), "2026-01-05");
});

test("date parts reject impossible calendar dates", () => {
  assert.equal(
    datePartsToIso({ month: "02", day: "29", year: "2025" }),
    ""
  );
  assert.equal(
    datePartsToIso({ month: "02", day: "29", year: "2024" }),
    "2024-02-29"
  );
});

test("numeric segments discard punctuation and cap their length", () => {
  assert.equal(numericSegment("0a1", 2), "01");
  assert.equal(numericSegment("20267", 4), "2026");
});
