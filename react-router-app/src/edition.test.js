import { test } from "node:test";
import assert from "node:assert/strict";
import { editionLabelFromCategories } from "./edition.js";

test("editionLabelFromCategories returns Print Edition", () => {
  assert.equal(editionLabelFromCategories(["United States", "Print Edition"]), "Print Edition");
});

test("editionLabelFromCategories returns Online Only", () => {
  assert.equal(editionLabelFromCategories(["Business", "Online Only"]), "Online Only");
});

test("editionLabelFromCategories returns empty string when absent", () => {
  assert.equal(editionLabelFromCategories(["Asia"]), "");
  assert.equal(editionLabelFromCategories([]), "");
});

