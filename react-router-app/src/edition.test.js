import { test } from "node:test";
import assert from "node:assert/strict";
import { editionLabel, normalizeEdition } from "./edition.js";

test("namespaced edition metadata leaves the title and sections intact", () => {
  assert.deepEqual(normalizeEdition({ title: "A story", categories: ["Asia"], kind: "online_only" }),
    { title: "A story", categories: ["Asia"], editionKind: "online_only" });
  assert.equal(editionLabel("online_only"), "Online Only");
  assert.equal(editionLabel("unknown"), "Edition unverified");
});

test("legacy labels never leak into newspaper sections or decorated titles", () => {
  assert.deepEqual(normalizeEdition({ title: "A story [Print Edition]", categories: ["Asia", "Print Edition"] }),
    { title: "A story", categories: ["Asia"], editionKind: "print_edition" });
  assert.deepEqual(normalizeEdition({ title: "A story [Online Only]", categories: ["Online Only"] }),
    { title: "A story", categories: [], editionKind: "unknown" });
});

test("explicit metadata wins over legacy labels; unlabelled titles stay verbatim", () => {
  assert.equal(normalizeEdition({ title: "A story", categories: ["Online Only"], kind: "print_edition" }).editionKind, "print_edition");
  assert.equal(normalizeEdition({ title: "A story [Online Only]" }).title, "A story [Online Only]");
});

test("missing or legacy online-only metadata is unverified, not an online claim", () => {
  assert.equal(normalizeEdition({ title: "Politics", categories: ["The World This Week"] }).editionKind, "unknown");
  assert.equal(normalizeEdition({ title: "Politics [Online Only]", categories: ["The World This Week", "Online Only"] }).editionKind, "unknown");
  assert.equal(normalizeEdition({ title: "Politics", kind: "unknown", categories: ["Online Only"] }).editionKind, "unknown");
});
