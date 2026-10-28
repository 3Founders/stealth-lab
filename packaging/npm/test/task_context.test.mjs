import { test } from "node:test";
import assert from "node:assert/strict";
import { relevantFirst } from "../lib/task_context.mjs";

test("relevantFirst: facts citing a located file, then facts naming a task word, then the rest in order; headings stay first", () => {
  const claims = ["# claims.md", "CLAIM|R-001|current|deps|repository|The repository uses requests|source=pyproject.toml:63",
    "CLAIM|R-002|current|api|pkg|Union errors list every subtype tried|source=pkg/_errors.py:5",
    "CLAIM|R-003|current|api|pkg|adapt_typehints converts values to their type hint|source=pkg/_typehints.py:900",
    "CLAIM|R-004|current|runtime|repository|Python >=3.7|source=pyproject.toml:13"].join("\n");
  const out = relevantFirst(claims, { files: ["pkg/_typehints.py"], prompt: "Does not validate against any of the Union subtypes" }).split("\n");
  assert.deepEqual(out.map((l) => l.split("|")[1] || l), ["# claims.md", "R-003", "R-002", "R-001", "R-004"]);
});
