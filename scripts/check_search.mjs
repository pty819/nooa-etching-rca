// Execute the actual generated scripts: syntax success alone missed this regression.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const site = path.join(root, "docs/_build/html");
let index;
const context = vm.createContext({ window: {}, Search: { setIndex(value) { index = value; } } });
vm.runInContext(fs.readFileSync(path.join(site, "_static/language_data.js"), "utf8"), context);
assert.equal(typeof context.window.Stemmer, "function", "Search stemmer constructor must exist");
const stemmer = new context.window.Stemmer();
assert.equal(stemmer.stemWord("背氦"), "背氦");
assert.equal(stemmer.stemWord("researching"), "research");
vm.runInContext(fs.readFileSync(path.join(site, "searchindex.js"), "utf8"), context);
for (const term of ["背氦", "刻蚀", "根因", "配方"]) {
  assert.ok(index.terms[term] !== undefined || index.titleterms[term] !== undefined, `Chinese term missing: ${term}`);
}
assert.ok(index.docnames.includes("algorithms") && index.docnames.includes("case-study"));
console.log(JSON.stringify({ stemmer: "passed", chinese_terms: 4, indexed_documents: index.docnames.length }));
