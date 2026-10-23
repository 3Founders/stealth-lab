import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch } from "../lib/dispatch.mjs";
import { answerEvidence, parseRule } from "../lib/accept.mjs";
import { parseEvidence } from "../lib/status.mjs";
import { LEGACY_POLICY } from "./helpers/legacy_policy.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth"] };
const MINE = "claude-opus-5-5|claude-code";
const FIX = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n";

function setup(extraEnv = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dev-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "dev-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".stealth"));
  fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  fs.writeFileSync(path.join(repo, ".stealth", "meta.json"), JSON.stringify({ repo_identity: { repo_id: "r:0123456789abcdef" } }));
  g("add", "-A"); g("commit", "-q", "-m", "init");
  const env = { STEALTHLAB_HOME: home, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "node -e 0", STEALTHLAB_DISPATCH_TESTFIRST: "off",
    STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off", STEALTHLAB_LOCATE_MODEL: "off",
    STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_ACCEPT: "E S F X", STEALTHLAB_TOKEN: "tok-test-123456789",
    STEALTHLAB_MCP_URL: "http://127.0.0.1:9/mcp", ...extraEnv };
  return { env, repo, home };
}

function stub(repo) {
  const calls = [];
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dev-patches-"));
  return { calls, cancelled: [],
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const f = path.join(dir, `${id}.patch`);
      fs.writeFileSync(f, FIX);
      return { run_id: id, state: "verified", cost_usd: 0.001, checks: [{ tail: "" }], diff: { files: ["calc.py"], patch_path: f, worktree: repo } };
    },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun(id) { this.cancelled.push(id); } };
}

const outbox = (home) => {
  const d = path.join(home, "outbox");
  try { return fs.readdirSync(d).filter((f) => f.endsWith(".json")).map((f) => JSON.parse(fs.readFileSync(path.join(d, f), "utf8"))); } catch { return []; }
};

test("an accepted answer leaves evidence for the library entry it created, locally, and nothing is queued while upload is off", async () => {
  const { env, repo, home } = setup();
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: stub(repo) });
  assert.equal(d.handled, true);
  assert.match(d.library_entry, /^L-[0-9a-f]+$/);
  const lines = parseEvidence(fs.readFileSync(path.join(repo, ".stealth", "evidence.md"), "utf8"));
  assert.equal(lines.length, 1, "the check; no reviewer ran under this rule, so no independent line");
  assert.deepEqual([lines[0].target, lines[0].method, lines[0].result, lines[0].where], [d.library_entry, "deterministic_check", "pass", "worktree"]);
  assert.equal(lines[0].check, "node -e 0");
  assert.ok(lines[0].hashes.some((h) => h.path === "calc.py"), "recorded at the file the answer changed");
  assert.equal(outbox(home).length, 0, "upload is off by default");
  assert.equal(fs.existsSync(path.join(repo, ".stealth", "evidence.key")), false);
});

test("with upload on, dispatch queues the report (no network, no wait) and the payload holds no command, path or diff", async () => {
  const { env, repo, home } = setup({ STEALTHLAB_EVIDENCE_UPLOAD: "on" });
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: stub(repo) });
  assert.equal(d.handled, true);
  const queued = outbox(home).filter((q) => q.tool === "report_check_evidence");
  assert.equal(queued.length, 1);
  const ev = queued[0].args.evidence;
  assert.equal(ev.target_id, d.library_entry);
  assert.equal(ev.target_kind, "library_entry");
  assert.equal(ev.method, "deterministic_check");
  const text = JSON.stringify(queued);
  for (const forbidden of ["node -e 0", "calc.py", "x = 2", "Fix calc", "diff --git"]) assert.ok(!text.includes(forbidden), `not queued: ${forbidden}`);
  assert.ok(Date.now() - t0 < 60000);
});

test("an evidence failure never fails the answer: an unwritable evidence.md leaves the delivered result as it was", async () => {
  const { env, repo } = setup({ STEALTHLAB_EVIDENCE_UPLOAD: "on" });
  fs.mkdirSync(path.join(repo, ".stealth", "evidence.md"));          // a directory where the file should be: appending fails
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: stub(repo) });
  assert.equal(d.handled, true, "the answer is delivered regardless");
  assert.match(d.library_entry, /^L-/);
});

test("the strong reviewer's model name is kept on the answer's evidence, for the independent_agent line", async () => {
  const result = { diff: { patch_path: (() => { const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dev-ev-")), "a.patch"); fs.writeFileSync(f, FIX); return f; })() } };
  const ev = await answerEvidence({ rule: parseRule("E S F X Rs"), result, prompt: "Fix calc.py", env: {},
    review: async ({ model }) => ({ verdict: "fixes", why: "sets x to 2", model, cost_usd: 0.01 }) });
  assert.equal(ev.Rs, true);
  assert.equal(ev.Rs_model, "claude-sonnet-5-5", "the default strong reviewer");
  const own = await answerEvidence({ rule: parseRule("E S F X Rs"), result, prompt: "Fix calc.py", env: { STEALTHLAB_REVIEW_STRONG_MODEL: "claude-opus-5-5" },
    review: async ({ model }) => ({ verdict: "does_not_fix", why: "no", model, cost_usd: 0 }) });
  assert.equal(own.Rs, false);
  assert.equal(own.Rs_no, true);
  assert.equal(own.Rs_model, "claude-opus-5-5");
});
