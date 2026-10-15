import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { enclosingBlock, locate, renderForWriter, renderWhere, taskTerms, testFileFor } from "../lib/locate.mjs";
import { preloadNamedFiles } from "../lib/agent/step_agent.mjs";
import { readArea, recordArea } from "../lib/areas.mjs";
import { RUN_HEAD, renderDispatchRun, writeDispatchRun } from "../lib/dispatch_run.mjs";
import { triedNote, withWhere, writeRegressRunner, writeSpecTests } from "../lib/dispatch.mjs";

// A package-shaped repository: the code the task is about sits in a large module of a package, not at the top level.
function repoWithPackage() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "loc-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.mkdirSync(path.join(repo, "pkg")); fs.mkdirSync(path.join(repo, "tests"));
  const filler = Array.from({ length: 300 }, (_, i) => `def helper_${i}(x):\n    return x + ${i}\n`).join("\n");
  fs.writeFileSync(path.join(repo, "pkg", "_typehints.py"), `import typing\n\n${filler}\n\n` +
    "def adapt_typehints(val, typehint):\n    \"\"\"Adapt a value to a type hint.\"\"\"\n    if typehint is typing.Optional:\n" +
    "        return None\n    return val\n\n\nclass ActionTypeHint:\n    def __init__(self, typehint):\n        self.typehint = typehint\n");
  fs.writeFileSync(path.join(repo, "pkg", "_cli.py"), "from ._typehints import adapt_typehints\n\ndef run(x):\n    return adapt_typehints(x, int)\n");
  fs.writeFileSync(path.join(repo, "pkg", "__init__.py"), "");
  fs.writeFileSync(path.join(repo, "tests", "test_typehints.py"), "import pytest\nfrom pkg._typehints import adapt_typehints\n\n\n" +
    "def test_adapt_int():\n    assert adapt_typehints(1, int) == 1\n\n\ndef test_other():\n    assert True\n");
  fs.writeFileSync(path.join(repo, "README.md"), "adapt_typehints is documented here\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return repo;
}

test("taskTerms: code-looking names, paths and quoted messages; plain words are not terms", () => {
  const terms = taskTerms("In `pkg/_cli.py` the call adapt_typehints(val) fails with \"value does not match the type hint\" for Optional fields");
  const by = Object.fromEntries(terms.map((t) => [t.term, t.kind]));
  assert.equal(by["pkg/_cli.py"], "path");
  assert.equal(by.adapt_typehints, "ident");
  assert.equal(by["value does not match the type hint"], "message");
  assert.equal(by.fields, undefined);
  assert.equal(by.fails, undefined);
});

test("locate: the function the task names, in its module, with the module's own test file", () => {
  const repo = repoWithPackage();
  const loc = locate({ prompt: "adapt_typehints returns None for Optional[int] instead of the value", root: repo, env: {} });
  assert.equal(loc.files[0], "pkg/_typehints.py", "the defining module ranks above the module that only calls it");
  const s = loc.spots[0];
  assert.equal(s.symbol, "adapt_typehints");
  const lines = fs.readFileSync(path.join(repo, s.file), "utf8").split("\n");
  assert.match(lines[s.start - 1], /def adapt_typehints/);
  assert.ok(s.end - s.start < 20, "the function, not the whole 900-line file");
  assert.equal(loc.testFile, "tests/test_typehints.py");
  assert.ok(!loc.files.includes("README.md"), "docs are not code locations");
  const where = renderWhere(loc);
  assert.match(where, /pkg\/_typehints\.py:\d+-\d+ \(adapt_typehints\)/);
  assert.match(where, /Existing tests for it: tests\/test_typehints\.py/);
  const forWriter = renderForWriter(loc, repo);
  assert.match(forWriter, /def adapt_typehints/);
  assert.match(forWriter, /from pkg\._typehints import adapt_typehints/, "the test file's imports show the writer how tests reach the code");
  assert.deepEqual(locate({ prompt: "x", root: repo, env: { STEALTHLAB_DISPATCH_LOCATE: "off" } }).spots, []);
});

test("locate: a task with no code names is placed by this repo's past fixes (library terms.idx -> solution diffs)", () => {
  const repo = repoWithPackage();
  const sdir = path.join(repo, ".stealth");
  fs.mkdirSync(path.join(sdir, "index"), { recursive: true });
  fs.mkdirSync(path.join(sdir, "library", "solutions"), { recursive: true });
  fs.writeFileSync(path.join(sdir, "index", "terms.idx"), "# terms.idx\nunion|L-abc123\n");
  fs.writeFileSync(path.join(sdir, "library", "solutions", "L-abc123.diff"),
    "diff --git a/pkg/_typehints.py b/pkg/_typehints.py\n--- a/pkg/_typehints.py\n+++ b/pkg/_typehints.py\n@@ -1 +1 @@\n-import typing\n+import typing  # union\n");
  assert.equal(locate({ prompt: "the `union` handling is wrong again", root: repo, env: {} }).files[0], "pkg/_typehints.py");
});

test("enclosingBlock: the def around a line, decorators included; module-level code gets a window", () => {
  const lines = ["import x", "", "@dec", "def f(a):", "    b = a", "    return b", "", "y = 1"];
  assert.deepEqual(enclosingBlock(lines, 5), { start: 3, end: 6, symbol: "f" });
  assert.equal(enclosingBlock(lines, 8).symbol, null);
});

test("testFileFor: test_<module>.py for _module.py", () => {
  const repo = repoWithPackage();
  assert.equal(testFileFor(repo, "pkg/_typehints.py"), "tests/test_typehints.py");
  assert.equal(testFileFor(repo, "pkg/_cli.py"), null);
});

test("preload: path:start-end in the task reads those lines, not the file's start", () => {
  const repo = repoWithPackage();
  const loc = locate({ prompt: "adapt_typehints is wrong for Optional", root: repo, env: {} });
  const out = preloadNamedFiles(`Fix it.\n\n${renderWhere(loc)}`, repo);
  assert.match(out, /pkg\/_typehints\.py \(lines \d+-\d+\)/);
  assert.match(out, /def adapt_typehints/);
  assert.doesNotMatch(out, /def helper_0\b/);
});

test("withWhere: the line always fits inside the executor's 2,000 characters; the request is cut, not the line", () => {
  const where = "Likely place in this repository: pkg/_typehints.py:900-910 (adapt_typehints).";
  assert.equal(withWhere("short task", where), `short task\n\n${where}`);
  const long = withWhere("x".repeat(2500), where);
  assert.ok(long.length <= 2000 && long.endsWith(where));
  assert.equal(withWhere("task", ""), "task");
});

test("areas.md: wins and losses per file, rewritten in place", () => {
  const repo = repoWithPackage();
  assert.equal(readArea(repo, "pkg/_typehints.py"), null);
  recordArea(repo, "pkg/_typehints.py", false);
  recordArea(repo, "pkg/_typehints.py", false);
  recordArea(repo, "pkg/_cli.py", true);
  assert.deepEqual({ ...readArea(repo, "pkg/_typehints.py"), at: undefined }, { wins: 0, losses: 2, at: undefined });
  assert.equal(readArea(repo, "pkg/_cli.py").wins, 1);
  const text = fs.readFileSync(path.join(repo, ".stealth", "areas.md"), "utf8");
  assert.equal(text.match(/AREA\|pkg\/_typehints\.py\|/g).length, 1, "one line per file");
});

test("run.md: written for a dispatched task; the planner's own run.md is never touched", () => {
  const repo = repoWithPackage();
  const loc = { spots: [{ file: "pkg/_typehints.py", start: 900, end: 905, symbol: "adapt_typehints" }], testFile: "tests/test_typehints.py" };
  assert.equal(writeDispatchRun(repo, { task: "fix Optional", loc, specFile: "/s/test_spec.py", regressFile: "tests/test_typehints.py", tried: [{ unit: "gemma|stealth", state: "failed" }] }), true);
  const text = fs.readFileSync(path.join(repo, ".stealth", "run.md"), "utf8");
  assert.ok(text.startsWith(RUN_HEAD));
  assert.match(text, /^NODE\|N-1\|done\|Locate.*pkg\/_typehints\.py:900-905 \(adapt_typehints\)\|step=-\|claims=-\|deps=-\|check=test -f pkg\/_typehints\.py$/m);
  assert.match(text, /^NODE\|N-2\|done\|Tests written in advance.*\|deps=N-1\|check=python -m pytest -q -p no:cacheprovider "\/s\/test_spec\.py"$/m);
  assert.match(text, /^NODE\|N-3\|ready\|Make the change\. Cheap tries: gemma failed\.\|step=-\|claims=-\|deps=N-2\|check=.*test_spec\.py" && python -m pytest -q -x -p no:cacheprovider tests\/test_typehints\.py$/m);
  // the plan_and_run validator accepts it
  const v = spawnSync(process.execPath, [path.resolve("bin/stealthlab-mcp.mjs"), "plan", "validate"], { cwd: repo, encoding: "utf8" });
  assert.equal(JSON.parse(v.stdout).ok, true, v.stdout + v.stderr);
  fs.writeFileSync(path.join(repo, ".stealth", "run.md"), "NODE|N-1|ready|the planner's own|step=-|claims=-|deps=-|check=make\n");
  assert.equal(writeDispatchRun(repo, { task: "x", loc }), false);
  assert.match(fs.readFileSync(path.join(repo, ".stealth", "run.md"), "utf8"), /the planner's own/);
  assert.match(renderDispatchRun({ task: "t", loc: { spots: [] }, winner: "gemma" }), /N-3\|done\|Make the change \(done by gemma\)/);
});

test("the note to the session's model: where to look and the tests that fail on the unchanged code", () => {
  const d = { handled: false, tried: [{ unit: "gemma|stealth", state: "failed" }], runMd: true, spec: { file: "/s/test_spec.py" },
              loc: { spots: [{ file: "pkg/_typehints.py", start: 900, end: 905, symbol: "adapt_typehints" }] } };
  const note = triedNote(d);
  assert.match(note, /points at pkg\/_typehints\.py:900-905 \(adapt_typehints\)/);
  assert.match(note, /Tests written for this task, which fail on the unchanged code, are at \/s\/test_spec\.py/);
  assert.match(note, /\.stealth\/run\.md \(node N-3 is yours\)/);
  assert.doesNotMatch(triedNote({ ...d, sharedFail: "test_a" }), /are at \/s\/test_spec\.py/, "tests two answers failed alike may be wrong: not offered");
});

test("test-first: tests that all pass on the unchanged code are asked for again, then dropped", async () => {
  const repo = repoWithPackage();
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "loc-home-"));
  const passing = "```python\nfrom pkg._typehints import adapt_typehints\n\ndef test_int():\n    assert adapt_typehints(1, int) == 1\n```";
  const failing = "```python\nimport typing\nfrom pkg._typehints import adapt_typehints\n\ndef test_optional():\n    assert adapt_typehints(3, typing.Optional) == 3\n```";
  const asks = [];
  const runClaude = (replies) => (args, input) => { asks.push(input); return { stdout: JSON.stringify({ result: replies[asks.length - 1], total_cost_usd: 0.01 }) }; };
  const spec = await writeSpecTests({ prompt: "adapt_typehints drops Optional values", root: repo, mine: "claude-opus-5-5|claude-code",
    env: { STEALTHLAB_HOME: home }, runClaude: runClaude([passing, failing]), baseline: true, located: "--- pkg/_typehints.py ---\ndef adapt_typehints" });
  assert.ok(spec?.file, "the second, failing-now tests are kept");
  assert.match(asks[0], /Where the task is in this repository[\s\S]*def adapt_typehints/, "the writer sees the located code");
  assert.match(asks[1], /ALL PASS on the code as it is now/);
  asks.length = 0;
  assert.equal(await writeSpecTests({ prompt: "x", root: repo, mine: "claude-opus-5-5|claude-code", env: { STEALTHLAB_HOME: home },
    runClaude: runClaude([passing, passing]), baseline: true }), null, "still passing on the unchanged code: no tests");
});

test("the regression check: runs the module's tests when they passed on the unchanged code, else passes", () => {
  const repo = repoWithPackage();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "loc-spec-"));
  const runner = writeRegressRunner(dir);
  fs.writeFileSync(path.join(dir, "REGRESS_SKIP"), "");
  assert.equal(spawnSync("python", [runner], { cwd: repo }).status, 0);
  fs.unlinkSync(path.join(dir, "REGRESS_SKIP"));
  fs.writeFileSync(path.join(dir, "REGRESS_OK"), "tests/test_typehints.py");
  assert.equal(spawnSync("python", [runner], { cwd: repo }).status, 0, "the module's tests pass");
  fs.writeFileSync(path.join(repo, "pkg", "_typehints.py"), "def adapt_typehints(val, typehint):\n    return None\n");
  assert.notEqual(spawnSync("python", [runner], { cwd: repo }).status, 0, "an answer that breaks them fails the check");
});
