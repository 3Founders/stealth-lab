import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch, dispatchableRungs, dispatchCheck, triedNote } from "../lib/dispatch.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1",
  ladder: ["gemma-4-31b-it|stealth", "minimax-m2-7|stealth", "claude-sonnet-5-5|claude-code", "claude-opus-5-5|claude-code"] };
const MINE = "claude-opus-5-5|claude-code";

function setup(execModels = ["gemma-4-31b-it", "minimax-m2-7"]) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "disp-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: execModels } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "disp-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".stealth"));
  fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  // these tests are about the dispatch loop: test-first and the cross-check have tests of their own below
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q",
                  STEALTHLAB_DISPATCH_TESTFIRST: "off", STEALTHLAB_DISPATCH_CROSSCHECK: "off" }, repo };
}

// A stand-in for ExecRuntime: each model's outcome is scripted; applying writes the "fixed" file.
function stubRuntime(repo, outcomes) {
  const calls = [];
  return {
    calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const input = calls[Number(id.slice(2)) - 1];
      return { run_id: id, state: outcomes[input.model], cost_usd: 0.001, checks: [{ tail: `${input.model} check output` }] };
    },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
  };
}

test("the dispatchable rungs: open-model rungs before the session's model or any Claude rung", () => {
  const { env } = setup();
  assert.deepEqual(dispatchableRungs(PLAN, MINE, env).map((r) => r.unit), ["gemma-4-31b-it|stealth", "minimax-m2-7|stealth"]);
  assert.deepEqual(dispatchableRungs({ ladder: ["claude-sonnet-5-5|claude-code", "gemma-4-31b-it|stealth"] }, MINE, env), [],
    "a Claude rung first: nothing to dispatch");
  assert.deepEqual(dispatchableRungs(PLAN, MINE, setup(["gpt-oss-120b"]).env), [], "no local executor for the model");
});

test("a cheap rung that passes the check is applied, recorded, and handles the prompt", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo, { "gemma-4-31b-it": "failed", "minimax-m2-7": "verified" });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env, runtime: rt });
  assert.equal(d.handled, true);
  assert.equal(d.unit, "minimax-m2-7|stealth");
  assert.deepEqual(rt.calls.map((c) => [c.executor, c.model, c.instance_key, c.checks[0]]),
    [["stealth", "gemma-4-31b-it", "g1.k1", "python -m pytest -q"], ["stealth", "minimax-m2-7", "g1.k1", "python -m pytest -q"]]);
  assert.match(d.text, /Done by minimax-m2-7/);
  assert.match(d.text, /calc\.py/);
  const routing = fs.readFileSync(path.join(repo, ".stealth", "routing.md"), "utf8");
  assert.ok(routing.includes("|gemma-4-31b-it|stealth|n=1|ok=0|") && routing.includes("|minimax-m2-7|stealth|n=1|ok=1|"));
  assert.ok(d.library_entry, "the pass becomes a library entry");
  assert.match(fs.readFileSync(path.join(repo, ".stealth", "library.md"), "utf8"), /Fix calc\.py so x is 2/);
});

test("when every cheap rung fails, the prompt goes on with what was tried and the rest of the plan", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo, { "gemma-4-31b-it": "failed", "minimax-m2-7": "failed" });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo,
    mine: MINE, env, runtime: rt });
  assert.equal(d.handled, false);
  assert.deepEqual(d.remaining, ["claude-sonnet-5-5|claude-code", "claude-opus-5-5|claude-code"]);
  assert.match(triedNote(d), /gemma-4-31b-it \(failed\), minimax-m2-7 \(failed\).*nothing was applied/s);
  assert.equal(fs.readFileSync(path.join(repo, "calc.py"), "utf8"), "x = 1\n", "nothing applied");
});

test("no check (none configured, no matched library entry), dispatch off, or no plan: no dispatch", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo, {});
  const base = { payload: { cwd: repo, prompt: "x" }, root: repo, mine: MINE, runtime: rt };
  assert.equal(await dispatch({ ...base, reply: { model_plan: PLAN }, env: { ...env, STEALTHLAB_DISPATCH_CHECK: "" } }), null);
  assert.equal(await dispatch({ ...base, reply: { model_plan: PLAN }, env: { ...env, STEALTHLAB_DISPATCH: "off" } }), null);
  assert.equal(await dispatch({ ...base, reply: {}, env }), null);
  assert.equal(rt.calls.length, 0);
  assert.equal(dispatchCheck({ check: null }, { library_matches: [] }, repo), null);
});

test("a Claude model run by the claude executor is a dispatched rung too; the session's own model never is", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "disp-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: {
    stealth: { models: ["gemma-4-31b-it"] }, claude: { models: ["claude-sonnet-5-5"] } } }));
  const plan = { ladder: ["gemma-4-31b-it|stealth", "claude-sonnet-5-5|claude", "claude-opus-5-5|claude-code"] };
  assert.deepEqual(dispatchableRungs(plan, MINE, { STEALTHLAB_HOME: home }).map((r) => [r.executor, r.model]),
    [["stealth", "gemma-4-31b-it"], ["claude", "claude-sonnet-5-5"]]);
});

// --- cross-check (differential testing), with real worktree folders and real pytest -----------------

import { crossCheck, EXTRA_TESTS } from "../lib/dispatch.mjs";
import { spawnSync } from "node:child_process";

const hasPytest = spawnSync("python", ["-m", "pytest", "--version"], { encoding: "utf8" }).status === 0;

function crossFixture(firstAnswer) {
  const dirA = fs.mkdtempSync(path.join(os.tmpdir(), "xa-"));
  const dirB = fs.mkdtempSync(path.join(os.tmpdir(), "xb-"));
  fs.writeFileSync(path.join(dirA, "solution.py"), firstAnswer);
  fs.writeFileSync(path.join(dirB, "solution.py"), "def f(x):\n    return x * 2\n");
  fs.mkdirSync(path.join(dirB, "tests_extra"));
  fs.writeFileSync(path.join(dirB, EXTRA_TESTS), "import sys, os\nsys.path.insert(0, os.getcwd())\nfrom solution import f\n\n" +
    "def test_negative():\n    assert f(-3) == -6\n\ndef test_zero():\n    assert f(0) == 0\n\n" +
    "def test_wrong_itself():\n    assert f(1) == 999\n");                      // the verifier's own bad test
  const rt = {
    async achieve() { return { run_id: "r-v" }; },
    async runResult() { return { run_id: "r-v", state: "verified", cost_usd: 0.001, diff: { worktree: dirB } }; },
    async cancelRun() { return {}; },
  };
  return { rt, result: { run_id: "r-a", state: "verified", diff: { worktree: dirA } } };
}
const RUNGS = [{ unit: "gemma-4-31b-it|stealth", executor: "stealth", model: "gemma-4-31b-it" },
               { unit: "minimax-m2-7|stealth", executor: "stealth", model: "minimax-m2-7" }];

test("cross-check: a second model's edge-case tests reject an answer that only the visible test liked", { skip: !hasPytest }, async () => {
  const { rt, result } = crossFixture("def f(x):\n    return abs(x) * 2\n");   // right for x >= 0, wrong below
  const c = await crossCheck({ rt, rung: RUNGS[0], result, rungs: RUNGS, env: {}, payload: { prompt: "double x" },
    root: ".", check: "python -m pytest -q" });
  assert.equal(c.verdict, "disagree");
  assert.equal(c.verifier, "minimax-m2-7|stealth");
  assert.equal(c.tests, 2, "the verifier's test that fails its own solution is dropped");
});

test("cross-check: an answer that passes the other model's tests is accepted; no pytest or no other model is inconclusive", { skip: !hasPytest }, async () => {
  const ok = crossFixture("def f(x):\n    return x + x\n");
  assert.equal((await crossCheck({ rt: ok.rt, rung: RUNGS[0], result: ok.result, rungs: RUNGS, env: {},
    payload: { prompt: "double x" }, root: ".", check: "python -m pytest -q" })).verdict, "agree");
  assert.equal((await crossCheck({ rt: ok.rt, rung: RUNGS[0], result: ok.result, rungs: RUNGS, env: {},
    payload: { prompt: "x" }, root: ".", check: "npm test" })).verdict, "inconclusive");
  assert.equal((await crossCheck({ rt: ok.rt, rung: RUNGS[0], result: ok.result, rungs: [RUNGS[0]],
    env: { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "nx-")) }, payload: { prompt: "x" }, root: ".",
    check: "python -m pytest -q" })).verdict, "inconclusive");
});


// --- test-first and the strict cross-check ---------------------------------------------------------------------

import { writeSpecTests } from "../lib/dispatch.mjs";

test("test-first: the session model's tests are saved outside the repo, sized, and the cost recorded", () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(repo, "solution.py"), "def f(x):\n    pass\n");
  let asked = null;
  const runClaude = (args, input) => { asked = { args, input };
    return { stdout: JSON.stringify({ result: "```python\nfrom solution import f\n\ndef test_neg():\n    assert f(-1) == -2\n```",
                                      total_cost_usd: 0.02 }) }; };
  const spec = writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env, runClaude });
  assert.ok(spec.file.startsWith(env.STEALTHLAB_HOME) && !spec.file.startsWith(repo), "outside the repo");
  assert.match(fs.readFileSync(spec.file, "utf8"), /sys\.path\.insert\(0, os\.getcwd\(\)\)[\s\S]*def test_neg/);
  assert.equal(spec.cost_usd, 0.02);
  assert.ok(asked.args.includes("--tools") && asked.args.includes("claude-opus-5-5"));
  assert.match(asked.input, /double x[\s\S]*solution\.py/, "the prompt and the project's files go on stdin");
  assert.match(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "dispatch_costs.jsonl"), "utf8"), /"test-first"/);
  assert.equal(writeSpecTests({ prompt: "x", root: repo, mine: "gpt-5|codex", env, runClaude }), null, "not a Claude session");
  assert.equal(writeSpecTests({ prompt: "x", root: repo, mine: MINE, env,
    runClaude: () => ({ stdout: JSON.stringify({ result: "no tests here" }) }) }), null);
});

test("strict cross-check: an answer nothing could confirm is not delivered and not counted against the model", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo, { "gemma-4-31b-it": "verified", "minimax-m2-7": "verified" });   // no worktrees: inconclusive
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo,
    mine: MINE, env: { ...env, STEALTHLAB_DISPATCH_CROSSCHECK: "strict" }, runtime: rt });
  assert.equal(d.handled, false);
  assert.deepEqual(d.tried.map((t) => t.state), ["unconfirmed", "unconfirmed"]);
  assert.equal(fs.existsSync(path.join(repo, ".stealth", "routing.md")), false, "no record either way");
  const lenient = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo,
    mine: MINE, env: { ...env, STEALTHLAB_DISPATCH_CROSSCHECK: "on" }, runtime: stubRuntime(repo, { "gemma-4-31b-it": "verified" }) });
  assert.equal(lenient.handled, true, "'on' accepts an inconclusive cross-check");
});
