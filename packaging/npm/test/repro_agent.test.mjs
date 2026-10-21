import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { lastTry, REPRO_FILE } from "../lib/repro_agent.mjs";

const patchWith = (body) => [
  "diff --git a/src/x.py b/src/x.py", "--- a/src/x.py", "+++ b/src/x.py", "@@ -1 +1 @@", "-a", "+b",
  `diff --git a/tests/${REPRO_FILE} b/tests/${REPRO_FILE}`, "new file mode 100644", "--- /dev/null", `+++ b/tests/${REPRO_FILE}`,
  "@@ -0,0 +1,3 @@", ...body.map((l) => `+${l}`), "",
].join("\n");

function run(patch, extra = {}) {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "repro-")), "a.patch");
  fs.writeFileSync(f, patch);
  return { state: "failed", diff: { patch_path: f }, ...extra };
}

test("lastTry: the agent's test from its patch, and why the check did not accept it", () => {
  const got = lastTry(run(patchWith(["from x import f", "def test_f():", "    assert f() == 1"]),
    { checks: [{ tail: "1 passed\nNOT OK: the test PASSES on the current code, so it does not reproduce the problem" }] }));
  assert.equal(got.tried, "from x import f\ndef test_f():\n    assert f() == 1");
  assert.match(got.why, /PASSED on the current code/);
});

test("lastTry: an import error is named; a run that wrote no test gives nothing", () => {
  const got = lastTry(run(patchWith(["from x import g", "def test_g():", "    assert g()"]),
    { checks: [{ tail: "ImportError: cannot import name 'g' from 'x'\nNOT OK: the test could not run (an import or setup error)" }] }));
  assert.match(got.why, /could not run.*ImportError: cannot import name 'g'/);
  assert.equal(lastTry(run("diff --git a/src/x.py b/src/x.py\n+++ b/src/x.py\n+b\n")), null);
  assert.equal(lastTry({ state: "timed_out", diff: {} }), null);
});

test("lastTry: a timed-out run's test is still passed on", () => {
  const got = lastTry(run(patchWith(["def test_a():", "    assert 1 == 2"]), { state: "timed_out", checks: [] }));
  assert.match(got.why, /ran out of time/);
});
