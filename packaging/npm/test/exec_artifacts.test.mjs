// Acceptance-run regression: bytecode/tool caches an executor leaves behind (it runs the tests itself) are not
// changed files -- they must not reach the diff, the scope check, the patch or apply_run.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { collectDiff, snapshotTree } from "../lib/exec/worktree.mjs";
import { scopeViolations } from "../lib/exec/verify.mjs";
import { makeRepo } from "./fixtures/exec/helpers.mjs";

test("generated caches are excluded from the diff and the scope check; real edits are kept", async () => {
  const dir = makeRepo();
  const env = { ...process.env };
  const baseTree = await snapshotTree(dir, env);
  fs.writeFileSync(path.join(dir, "calc.txt"), "fixed\n");
  for (const f of ["__pycache__/calc.cpython-313.pyc", "pkg/__pycache__/x.pyc", "loose.pyc", ".pytest_cache/v/cache/nodeids",
                   "node_modules/.cache/tool/blob"]) {
    fs.mkdirSync(path.join(dir, path.dirname(f)), { recursive: true });
    fs.writeFileSync(path.join(dir, f), "generated");
  }
  const d = await collectDiff({ dir, baseTree, env });
  assert.deepEqual(d.files, ["calc.txt"]);
  assert.deepEqual(scopeViolations(d.files, ["calc.txt"]), []);
  assert.ok(!d.patch.toString().includes("__pycache__"));
});
