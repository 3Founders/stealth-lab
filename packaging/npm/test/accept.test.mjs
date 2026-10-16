import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch, triedNote } from "../lib/dispatch.mjs";
import { decide, evalRule, parseRule, patchSignals } from "../lib/accept.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "minimax-m2-7|stealth", "gpt-oss-120b|stealth"] };
const MINE = "claude-opus-5-5|claude-code";
const FIX = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n";
const OTHER = "diff --git a/other.py b/other.py\n--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-y = 1\n+y = 3\n";
const SWALLOW = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,4 @@\n-x = 1\n+try:\n+    x = 2\n+except Exception:\n+    pass\n";

function setup() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "acc-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7", "gpt-oss-120b"] } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "acc-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n"); fs.writeFileSync(path.join(repo, "other.py"), "y = 1\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "node -e 0", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                  STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off", STEALTHLAB_LOCATE_MODEL: "off",
                  STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_AGREE_WAIT_S: "5" }, repo };
}
// each model: [state, patch]; a verified run's patch is on disk, as the runtime keeps it
function stub(repo, outcomes) {
  const calls = [];
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "acc-patches-"));
  return { calls, cancelled: [],
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const m = calls[Number(id.slice(2)) - 1].model;
      const [state, patch] = outcomes[m];
      const f = path.join(dir, `${id}.patch`);
      fs.writeFileSync(f, patch || "");
      return { run_id: id, state, cost_usd: 0.001, checks: [{ tail: "" }],
               diff: { files: [...String(patch || "").matchAll(/^diff --git a\/\S+ b\/(\S+)/gm)].map((x) => x[1]), patch_path: f, worktree: repo } };
    },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun(id) { this.cancelled.push(id); } };
}

test("parseRule / evalRule: AND of groups, A|R is either, !X is not; an unknown signal or 'check' is no rule", () => {
  assert.equal(parseRule("check"), null);
  assert.equal(parseRule("E Q"), null);
  const r = parseRule("E S T* A|R !Rs_no");
  assert.equal(evalRule(r, { E: true, S: true, "T*": true, R: true }), true);
  assert.equal(evalRule(r, { E: true, S: true, "T*": true }), false, "neither A nor R");
  assert.equal(evalRule(r, { E: true, S: true, "T*": true, A: true, Rs_no: true }), false, "the strong reviewer's veto");
});

test("patchSignals and decide: S/F/X from the patch; A when two answers change the same source files; T* when no answer passes T", () => {
  assert.deepEqual(patchSignals(SWALLOW).X, false);
  assert.deepEqual(patchSignals(FIX), { S: true, F: true, X: true, src: ["calc.py"] });
  assert.equal(patchSignals("diff --git a/tests/test_c.py b/tests/test_c.py\n+x\n").S, false);
  const a = { evidence: { ...patchSignals(FIX), E: true } }, b = { evidence: { ...patchSignals(FIX), E: true } }, c = { evidence: { ...patchSignals(OTHER), E: true } };
  assert.equal(decide([c, a, b], parseRule("E S F X A")), a, "the agreeing answer, not the first one");
  assert.equal(c.evidence.A, false);
  assert.equal(a.evidence["T*"], true, "no answer passed our tests: they are no evidence");
});

test("evidence mode: two cheap answers that agree are accepted; the check alone no longer decides", async () => {
  const { env, repo } = setup();
  const rt = stub(repo, { "gemma-4-31b-it": ["verified", FIX], "minimax-m2-7": ["verified", FIX], "gpt-oss-120b": ["failed", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_ACCEPT: "E S F X A" }, runtime: rt });
  assert.equal(d?.handled, true);
  assert.equal(d.model, "gemma-4-31b-it");
  assert.match(d.tried.find((t) => t.state === "verified").evidence, /\bA\b/);
  assert.ok(d.tried.some((t) => t.state === "passed the check, another answer was taken"));
  assert.ok(!rt.calls.some((c) => (c.checks || []).some((k) => /run_spec/.test(k))), "no written tests in the agents' check");
});

test("evidence mode: a lone passing answer the rule does not accept goes to the session; one that swallows exceptions too", async () => {
  const { env, repo } = setup();
  const rt = stub(repo, { "gemma-4-31b-it": ["verified", FIX], "minimax-m2-7": ["failed", ""], "gpt-oss-120b": ["failed", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_ACCEPT: "E S F X A" }, runtime: rt });
  assert.equal(d.handled, false);
  assert.match(d.tried.find((t) => t.unit.startsWith("gemma")).state, /passed the check, not accepted by the rule/);
  assert.match(triedNote(d), /StealthLab first tried this/);
  const rt2 = stub(repo, { "gemma-4-31b-it": ["verified", SWALLOW], "minimax-m2-7": ["failed", ""], "gpt-oss-120b": ["failed", ""] });
  const d2 = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_ACCEPT: "E S X" }, runtime: rt2 });
  assert.equal(d2.handled, false);
  const d3 = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env, runtime: stub(repo, { "gemma-4-31b-it": ["verified", SWALLOW], "minimax-m2-7": ["failed", ""], "gpt-oss-120b": ["failed", ""] }) });
  assert.equal(d3.handled, true, "accept=check (the default): the check alone, as before");
});

test("a middle model (tier: 2) starts only after every cheap one has finished without a pass, and can then solve it", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({
    executors: { stealth: { models: ["gemma-4-31b-it", "glm-5.3", "minimax-m2-7"] } },
    profiles: { "glm-5.3": { openai_base_url: "https://example.test/v1", key_env: "K", tier: 2 } } }));
  const order = [];
  const rt = stub(repo, { "gemma-4-31b-it": ["failed", ""], "minimax-m2-7": ["failed", ""], "glm-5.3": ["verified", FIX] });
  const achieve = rt.achieve.bind(rt);
  rt.achieve = async (i) => { order.push(`start ${i.model}`); return achieve(i); };
  const result = rt.runResult.bind(rt);
  rt.runResult = async (id) => { const r = await result(id); order.push(`end ${rt.calls[Number(id.slice(2)) - 1].model}`); return r; };
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" },
    reply: { model_plan: { ...PLAN, ladder: ["gemma-4-31b-it|stealth", "glm-5.3|stealth", "minimax-m2-7|stealth"] } }, root: repo, mine: MINE,
    env: { ...env, K: "k" }, runtime: rt });
  assert.equal(d?.handled, true);
  assert.equal(d.model, "glm-5.3");
  const glm = order.indexOf("start glm-5.3");
  assert.ok(glm > order.indexOf("end gemma-4-31b-it") && glm > order.indexOf("end minimax-m2-7"), order.join(", "));
});
