// The dispatch defaults (2026-10-18): what a user gets with no STEALTHLAB_DISPATCH_* setting -- every configured cheap
// model at once, one 600 s budget, and an answer delivered when it passes the project's own tests and the strong
// reviewer says it fixes the request.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { dispatch, dispatchPolicy } from "../lib/dispatch.mjs";

const hasPytest = spawnSync("python", ["-m", "pytest", "--version"], { encoding: "utf8" }).status === 0;
const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "minimax-m2-7|stealth", "gpt-oss-120b|stealth"] };
const MINE = "claude-opus-5-5|claude-code";
const SPEC = "from calc import x\n\ndef test_x_is_two():\n    assert x == 2\n";

test("the defaults: the evidence rule, all models at once, a 600 s budget, and none of the limits without a measured basis", () => {
  const p = dispatchPolicy({});
  assert.equal(p.accept, "E Rs", "the project's tests and the strong reviewer: nothing else gates");
  assert.equal(p.parallel, true);
  assert.equal(p.timeoutS, 600);
  assert.equal(p.maxParallel, Infinity, "no per-provider limit");
  assert.equal(p.maxRungs, Infinity, "every configured model is tried");
  assert.equal(p.strongExtraS, 0);
  assert.equal(p.modelCap, true, "the learned per-model cap: off for a model until it has 5 wins here");
  assert.equal(p.areaSkip, false);
  assert.equal(p.testFirst, true);
  assert.equal(p.specBaseline, true, "the reproduction test must fail on the unchanged code");
  assert.equal(p.gate, true, "only what the server's judge calls a change goes to the cheap models");
  assert.equal(p.dag, false, "a task is tried whole");
  assert.equal(p.specOnly, false, "no project tests: nothing but a guessed test would judge, so no dispatch");
});

test("the server judge decides: only work=change is dispatched; an answer, conversation, an unclear change or no verdict is not", { skip: !hasPytest }, async () => {
  for (const verdict of [{ work: "answer" }, { work: "conversation" }, { work: "unclear_change" }, { needs_retrieval: true, reason: "triage over 4s" }, null]) {
    const s = setup({ "gemma-4-31b-it": "x = 2\n" });
    const logs = [];
    const d = await dispatch({ payload: { cwd: s.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE,
      env: s.env, runtime: s.rt, runClaude: writer(SPEC), triage: verdict, fetchImpl: reviewer("fixes"), log: (m) => logs.push(m) });
    assert.equal(d, null, JSON.stringify(verdict));
    assert.equal(s.rt.calls.length, 0);
    assert.ok(logs.some((l) => /not dispatched \((the judge says work=|no verdict from the server's judge)/.test(l)), logs.join("\n"));
  }
});

// a repo where calc.x is 1 and the task is to make it 2; each model's answer is the calc.py it leaves in its own worktree
function setup(answers) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "def-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: Object.keys(answers) } },
    profiles: { reviewer: { openai_base_url: "https://review.example/v1", key_env: "K" } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "def-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  const calls = [];
  const rt = { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const model = calls[Number(id.slice(2)) - 1].model;
      const body = answers[model];
      if (!body) return { run_id: id, state: "failed", cost_usd: 0.001, checks: [{ tail: "" }], diff: { files: [] } };
      const wt = fs.mkdtempSync(path.join(os.tmpdir(), "def-wt-"));
      fs.writeFileSync(path.join(wt, "calc.py"), body);
      const patch = `diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x = 1\n+${body.trim()}\n`;
      const pp = path.join(wt, "answer.patch");
      fs.writeFileSync(pp, patch);
      return { run_id: id, state: "verified", cost_usd: 0.001, checks: [{ tail: "" }], diff: { files: ["calc.py"], patch_path: pp, worktree: wt } };
    },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun() {} };
  const env = { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "node -e 0", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off",
                STEALTHLAB_LOCATE_MODEL: "off", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_REVIEW_STRONG_MODEL: "reviewer", K: "k" };
  return { env, repo, rt };
}
const CHANGE = { needs_retrieval: true, kind: "reusable_task", work: "change", provider: "jev" };   // the server judge's verdict
const writer = (code) => () => ({ stdout: JSON.stringify({ result: code, total_cost_usd: 0.01 }) });
const reviewer = (verdict) => async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: JSON.stringify({ verdict, why: "w" }) } }] }) });

test("default rule: a passing answer the strong reviewer says fixes is delivered, and no test is written for it", { skip: !hasPytest }, async () => {
  const right = setup({ "gemma-4-31b-it": "x = 2\n" });
  let wrote = 0;
  const d = await dispatch({ payload: { cwd: right.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: right.repo, mine: MINE,
    env: right.env, runtime: right.rt, runClaude: () => { wrote++; return writer(SPEC)(); }, triage: CHANGE, fetchImpl: reviewer("fixes") });
  assert.equal(d?.handled, true, JSON.stringify(d?.tried));
  assert.equal(wrote, 0, "the default rule has no reproduction test: none is written");
});

test("default rule: 'does not fix' or an unsure review is not an accept", { skip: !hasPytest }, async () => {
  for (const verdict of ["does_not_fix", "unsure"]) {
    const s = setup({ "gemma-4-31b-it": "x = 2\n" });
    const d = await dispatch({ payload: { cwd: s.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE,
      env: s.env, runtime: s.rt, runClaude: writer(SPEC), triage: CHANGE, fetchImpl: reviewer(verdict) });
    assert.equal(d.handled, false, verdict);
    assert.match(d.tried.find((t) => t.unit.startsWith("gemma")).state, /not accepted by the rule/);
  }
});

test("a rule with T: an answer that makes the reproduction test pass is delivered; one that does not is not", { skip: !hasPytest }, async () => {
  const T = { STEALTHLAB_DISPATCH_ACCEPT: "E T Rs" };
  const right = setup({ "gemma-4-31b-it": "x = 2\n" });
  const d = await dispatch({ payload: { cwd: right.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: right.repo, mine: MINE,
    env: { ...right.env, ...T }, runtime: right.rt, runClaude: writer(SPEC), triage: CHANGE, fetchImpl: reviewer("fixes") });
  assert.equal(d?.handled, true, JSON.stringify(d?.tried));
  const wrong = setup({ "gemma-4-31b-it": "x = 3\n" });
  const d2 = await dispatch({ payload: { cwd: wrong.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: wrong.repo, mine: MINE,
    env: { ...wrong.env, ...T }, runtime: wrong.rt, runClaude: writer(SPEC), triage: CHANGE, fetchImpl: reviewer("fixes") });
  assert.equal(d2.handled, false, "it passed the project's check but not the reproduction test");
});

test("a rule with T and no reproduction test can be written: the cheap tries stop at once and the session takes over", { skip: !hasPytest }, async () => {
  const s = setup({ "gemma-4-31b-it": null, "minimax-m2-7": null, "gpt-oss-120b": null });
  s.env.STEALTHLAB_DISPATCH_ACCEPT = "E T Rs";
  const logs = [];
  // the runs never finish on their own: only the missing test may end the tries (the budget is 600 s)
  s.rt.runResult = () => new Promise(() => {});
  const keep = setInterval(() => {}, 1000);
  const t0 = Date.now();
  let d;
  try {
    d = await dispatch({ payload: { cwd: s.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE,
      env: s.env, runtime: s.rt, runClaude: writer("no tests here"), triage: CHANGE, fetchImpl: reviewer("fixes"), log: (m) => logs.push(m) });
  } finally { clearInterval(keep); }
  assert.ok(Date.now() - t0 < 60000, "stopped well before the budget");
  assert.equal(d.handled, false);
  assert.ok(logs.some((l) => /no reproduction test could be written/.test(l)), logs.join("\n"));
  assert.equal(s.rt.calls.length, 3, "all three models had started at once");
});

test("every configured model starts at once, with the budget as each run's own limit", { skip: !hasPytest }, async () => {
  const s = setup({ "gemma-4-31b-it": "x = 2\n", "minimax-m2-7": "x = 2\n", "gpt-oss-120b": "x = 2\n" });
  let started = 0;
  const ach = s.rt.achieve.bind(s.rt);
  let release;
  const gate = new Promise((r) => { release = r; });
  s.rt.achieve = async (i) => { started++; if (started === 3) release(); return ach(i); };
  const rr = s.rt.runResult.bind(s.rt);
  s.rt.runResult = async (id) => { await gate; return rr(id); };   // no run finishes before all three have started
  const d = await dispatch({ payload: { cwd: s.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE,
    env: s.env, runtime: s.rt, runClaude: writer(SPEC), triage: CHANGE, fetchImpl: reviewer("fixes") });
  assert.equal(d.handled, true);
  assert.equal(started, 3);
  assert.ok(s.rt.calls.every((c) => c.timeout_s === 600 && c.hang_s === 600), JSON.stringify(s.rt.calls.map((c) => [c.timeout_s, c.hang_s])));
});

test("at the time limit the runs still going hand in their change; a handed-in answer that passes is delivered", { skip: !hasPytest }, async () => {
  const s = setup({ "gemma-4-31b-it": "x = 2\n" });
  s.env.STEALTHLAB_DISPATCH_TIMEOUT_S = "1";
  const rr = s.rt.runResult.bind(s.rt);
  let handIn;
  const handedIn = new Promise((r) => { handIn = r; });
  s.rt.runResult = async (id) => { await handedIn; return rr(id); };       // still working at the limit
  const finished = [];
  s.rt.finishRun = async (id) => { finished.push(id); handIn(); return { run_id: id }; };
  const logs = [];
  const d = await dispatch({ payload: { cwd: s.repo, prompt: "Make calc.x equal 2" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE,
    env: s.env, runtime: s.rt, triage: CHANGE, fetchImpl: reviewer("fixes"), log: (m) => logs.push(m) });
  assert.deepEqual(finished, ["r-1"]);
  assert.ok(logs.some((l) => /the \d+ s are up: gemma-4-31b-it\|stealth stop and hand in their change/.test(l)), logs.join("\n"));
  assert.equal(d.handled, true, logs.join("\n"));
});
