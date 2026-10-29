import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch, isFinding } from "../lib/dispatch.mjs";
import { LEGACY_POLICY } from "./helpers/legacy_policy.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "glm-5.3-flash|stealth"] };
const MINE = "claude-opus-5-5|claude-code";
// gemma-4-31b on kell-try, 2026-10-29: the typo the request named was not in the repository
const REPORT = "I searched jsonargparse/_namespace.py and the rest of the repository: the typo \"Namspace\" does not occur anywhere, " +
  "so no change was necessary.\n\nLearned: the docstrings already say Namespace.";
const NO_CHANGE = { state: "failed", error: "empty diff", summary: REPORT, diff: { files: [] }, cost_usd: 0.02, checks: [] };

function setup() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "find-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "glm-5.3-flash"] } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "find-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "ns.py"), "class Namespace:\n    \"\"\"A Namespace.\"\"\"\n");
  fs.mkdirSync(path.join(repo, ".kell")); fs.writeFileSync(path.join(repo, ".kell", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_CROSSCHECK: "off",
                  STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off", STEALTHLAB_DISPATCH_TESTFIRST: "off", STEALTHLAB_DISPATCH_PARALLEL: "on" }, repo };
}
// the first run reports a finding at once; the second would take `slowMs`
function stub(first, slowMs = 20000) {
  const rt = { calls: 0, cancelled: 0,
    async achieve() { rt.calls++; return { run_id: `r-${rt.calls}` }; },
    async runResult(id) {
      if (id === "r-1") { await new Promise((r) => setTimeout(r, 500)); return { run_id: id, ...first }; }
      await new Promise((r) => setTimeout(r, slowMs));
      return { run_id: id, state: "failed", error: "a check failed", diff: { files: ["ns.py"] }, checks: [{ tail: "" }] };
    },
    async applyRun() { throw new Error("nothing is applied for a finding"); },
    async cancelRun() { rt.cancelled++; } };
  return rt;
}
const verdict = (v, why) => () => ({ stdout: JSON.stringify({ result: JSON.stringify({ verdict: v, why }), total_cost_usd: 0.03 }) });

test("isFinding: finished on its own, no change, a reason given; anything else is a failed try", () => {
  assert.equal(isFinding(NO_CHANGE), true);
  assert.equal(isFinding({ ...NO_CHANGE, error: "a check failed; empty diff" }), true, "the unchanged code's own failures do not matter");
  assert.equal(isFinding({ ...NO_CHANGE, stalled: { rule: "e" } }), false, "stopped for going round in circles");
  assert.equal(isFinding({ ...NO_CHANGE, stop_reason: "timeout" }), false, "stopped from outside");
  assert.equal(isFinding({ ...NO_CHANGE, usage_limited: true }), false);
  assert.equal(isFinding({ ...NO_CHANGE, diff: { files: ["ns.py"] }, error: "a check failed" }), false, "it changed something");
  assert.equal(isFinding({ ...NO_CHANGE, summary: "done" }), false, "no reason given");
  assert.equal(isFinding({ ...NO_CHANGE, state: "verified" }), false);
});

test("a confirmed finding ends the dispatch with the report: nothing applied, the other try cancelled at once", async () => {
  const { env, repo } = setup();
  const rt = stub(NO_CHANGE);
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: 'fix the typo "Namspace" in ns.py' }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env,
    runtime: rt, runClaude: verdict("good", "ns.py has no such typo; the docstring says Namespace") });
  assert.equal(d.handled, true);
  assert.equal(d.finding, true);
  assert.deepEqual(d.files, []);
  assert.match(d.text, /^No change made: gemma-4-31b-it/);
  assert.match(d.text, /does not occur anywhere/);
  assert.doesNotMatch(d.text, /Learned:/, "the agent's notes to itself are not shown");
  assert.match(d.text, /Reviewer: ns\.py has no such typo/);
  assert.equal(rt.calls, 2, "both tries started");
  assert.ok(rt.cancelled >= 1, "the slower try was cancelled");
  assert.ok(Date.now() - t0 < 15000, "did not wait for the 20 s try");
  assert.ok(d.tried.some((t) => t.state === "finding, confirmed by review"));
});

test("a finding the reviewer does not confirm is a failed try, as before; the switch off skips the review", async () => {
  const { env, repo } = setup();
  const d = await dispatch({ payload: { cwd: repo, prompt: "add a --dry-run flag to ns.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env,
    runtime: stub(NO_CHANGE, 50), runClaude: verdict("bad", "the flag can be added; the agent gave up") });
  assert.equal(d.handled, false);
  assert.ok(d.tried.some((t) => /^finding, not confirmed \(bad: the flag can be added/.test(t.state)));
  let asked = 0;
  const off = await dispatch({ payload: { cwd: repo, prompt: "x" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_FINDINGS: "off" }, runtime: stub(NO_CHANGE, 50), runClaude: () => { asked++; return { stdout: "" }; } });
  assert.equal(off.handled, false);
  assert.equal(asked, 0, "no finding review with the switch off");
});

test("newNonCodeTarget: a config, CI or docs file the repository does not have yet, when the request names no file it has", async () => {
  const { newNonCodeTarget } = await import("../lib/dispatch.mjs");
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "nc-repo-"));
  fs.mkdirSync(path.join(repo, ".github", "workflows"), { recursive: true });
  fs.writeFileSync(path.join(repo, ".github", "workflows", "tests.yml"), "on: push\n");
  fs.mkdirSync(path.join(repo, "pkg")); fs.writeFileSync(path.join(repo, "pkg", "core.py"), "");
  assert.equal(newNonCodeTarget("add a GitHub Actions workflow .github/workflows/lint.yml that runs ruff on push", repo), ".github/workflows/lint.yml");
  assert.equal(newNonCodeTarget("add a .editorconfig with 4-space indent", repo), ".editorconfig");
  assert.equal(newNonCodeTarget("update .github/workflows/tests.yml to Python 3.12", repo), null, "it exists: there is something to locate");
  assert.equal(newNonCodeTarget("add lint.yml like pkg/core.py's style", repo), null, "an existing file is named too");
  assert.equal(newNonCodeTarget("write tools/flatten.py that reads a JSON file", repo), null, "new code is still code");
  assert.equal(newNonCodeTarget("bump the version to 4.32.1", repo), null, "a version is not a path");
});

test("two answers the strong review will not call a fix end the cheap tries; the third model is not waited for", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "rv-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({
    executors: { stealth: { models: ["gemma-4-31b-it", "glm-5.3-flash", "minimax-m2-7"] } },
    profiles: { rev: { model_id: "rev", openai_base_url: "https://rev.example/v1", key_env: "RK" } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "rv-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "core.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".kell")); fs.writeFileSync(path.join(repo, ".kell", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  const patches = fs.mkdtempSync(path.join(os.tmpdir(), "rv-patches-"));
  const rt = { calls: 0, cancelled: 0,
    async achieve() { rt.calls++; return { run_id: `r-${rt.calls}` }; },
    async runResult(id) {
      if (id === "r-3") await new Promise((r) => setTimeout(r, 20000));      // the third model: slow
      else await new Promise((r) => setTimeout(r, id === "r-1" ? 100 : 400));
      const f = path.join(patches, `${id}.patch`);
      fs.writeFileSync(f, "diff --git a/core.py b/core.py\n--- a/core.py\n+++ b/core.py\n@@ -1 +1 @@\n-x = 1\n+x = 2  # a new example\n");
      return { run_id: id, state: "verified", cost_usd: 0.01, checks: [{ tail: "" }], diff: { files: ["core.py"], patch_path: f, worktree: null } };
    },
    async applyRun() { throw new Error("nothing should be applied"); },
    async cancelRun() { rt.cancelled++; } };
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content:
    JSON.stringify({ verdict: "does_not_fix", why: "the request names an existing example; the diff invents a new one" }) } }] }) });
  const env = { STEALTHLAB_HOME: home, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "node -e 0", STEALTHLAB_DISPATCH_TESTFIRST: "off",
    STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off", STEALTHLAB_LOCATE_MODEL: "off",
    STEALTHLAB_DISPATCH_ACCEPT: "E Rs", STEALTHLAB_REVIEW_STRONG_MODEL: "rev", RK: "k", STEALTHLAB_DISPATCH_PARALLEL: "on",
    STEALTHLAB_DISPATCH_MAX_PARALLEL: "3", STEALTHLAB_DISPATCH_WIDTH: "3" };
  const plan = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "glm-5.3-flash|stealth", "minimax-m2-7|stealth"] };
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: "add --dry-run to the CLI example in core.py" }, reply: { model_plan: plan }, root: repo,
    mine: MINE, env, runtime: rt, fetchImpl });
  assert.equal(d.handled, false);
  assert.ok(Date.now() - t0 < 15000, "did not wait for the slow third model");
  assert.ok(d.tried.filter((t) => /not accepted by the rule/.test(t.state)).length >= 2);
  assert.ok(d.tried.some((t) => /invents a new one/.test(t.review || "")), "the reviewer's reason goes to the session's model");
});
