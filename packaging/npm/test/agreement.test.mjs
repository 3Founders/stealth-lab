import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

import { agreeCheck, definesFunctions, firstDifference } from "../lib/agree.mjs";
import { dispatch, triedNote } from "../lib/dispatch.mjs";
import { replay } from "../scripts/replay_agreement.mjs";

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const hasHypothesis = spawnSync("python", ["-c", "import hypothesis, pytest"], { encoding: "utf8" }).status === 0;
const need = { skip: !hasHypothesis && "needs python with hypothesis and pytest" };

const answer = (code, file = "solution.py") => { const d = tmp("agree-wt-"); fs.writeFileSync(path.join(d, file), code); return { worktree: d, files: [file] }; };
const specFile = (code) => { const f = path.join(tmp("agree-spec-"), "test_spec.py"); fs.writeFileSync(f, code); return f; };
const DOUBLE_SPEC = "from solution import double\n\ndef test_a():\n    assert double(3) == 6\n    assert double(0) == 0\n";

// ---- the comparison, with real python --------------------------------------------------------------------------------

test("two answers that behave the same agree, on the test's own inputs and generated ones", need, () => {
  const r = agreeCheck({ a: answer("def double(x):\n    return x * 2\n"), b: answer("def double(x):\n    return x + x\n"), specFile: specFile(DOUBLE_SPEC) });
  assert.equal(r.verdict, "agree");
  assert.ok(r.compared >= 10, `compared ${r.compared}`);
});

test("two answers that differ where the tests do not look disagree, and the difference names the function and the input", need, () => {
  const r = agreeCheck({ a: answer("def double(x):\n    return x * 2\n"), b: answer("def double(x):\n    return abs(x) * 2\n"), specFile: specFile(DOUBLE_SPEC) });
  assert.equal(r.verdict, "disagree");
  assert.equal(r.difference.function, "double");
  assert.match(r.difference.input, /^\[-\d+\]$/);
  assert.notDeepEqual(r.difference.a, r.difference.b);
});

test("an exception on one side and a value on the other is a difference; the same exception type on both is not", need, () => {
  const spec = specFile("from solution import root\n\ndef test_a():\n    assert root(4) == 2\n");
  const raises = "import math\ndef root(x):\n    if x < 0:\n        raise ValueError('negative')\n    return int(math.sqrt(x))\n";
  const quiet = "import math\ndef root(x):\n    return int(math.sqrt(abs(x)))\n";
  assert.equal(agreeCheck({ a: answer(raises), b: answer(quiet), specFile: spec }).verdict, "disagree");
  assert.equal(agreeCheck({ a: answer(raises), b: answer(raises.replace("'negative'", "'less than zero'")), specFile: spec }).verdict, "agree");
});

test("an in-place change to an argument counts, and floats are compared to 9 places", need, () => {
  const spec = specFile("from solution import srt, half\n\ndef test_a():\n    assert srt([3, 1, 2]) == [1, 2, 3]\n    assert half(3.0) == 1.5\n");
  const inplace = "def srt(xs):\n    xs.sort()\n    return list(xs)\ndef half(x):\n    return x / 2\n";
  const copy = "def srt(xs):\n    return sorted(xs)\ndef half(x):\n    return x / 2\n";
  assert.equal(agreeCheck({ a: answer(inplace), b: answer(copy), specFile: spec }).verdict, "disagree", "one mutates the caller's list, the other does not");
  const rounding = "def srt(xs):\n    return sorted(xs)\ndef half(x):\n    return x * 0.5 + 1e-12\n";
  assert.equal(agreeCheck({ a: answer(copy), b: answer(rounding), specFile: spec }).verdict, "agree");
});

test("strings, lists, tuples, dicts and booleans as arguments get inferred strategies", need, () => {
  const spec = specFile("from solution import f\n\ndef test_a():\n    assert f('ab', [1, 2], (1, 'x'), {'k': 1}, True) == 5\n    assert f('', [], (2, 'y'), {}, False) == 0\n");
  const code = "def f(s, xs, t, d, b):\n    return len(s) + len(xs) + t[0] + sum(d.values()) + (1 if b else 0)\n";
  const r = agreeCheck({ a: answer(code), b: answer(code.replace("t[0]", "t[0] + 0")), specFile: spec });
  assert.equal(r.verdict, "agree");
  assert.ok(r.compared >= 10);
});

test("nothing to compare is inconclusive: no function the tests call, a non-literal call, no python file, no spec, hypothesis missing", need, () => {
  const noFn = agreeCheck({ a: answer("x = 1\n"), b: answer("x = 2\n"), specFile: specFile("def test_a():\n    assert True\n") });
  assert.equal(noFn.verdict, "inconclusive");
  assert.match(noFn.reason, /no function the tests call/);
  const nonLiteral = agreeCheck({ a: answer("def g(x):\n    return x\n"), b: answer("def g(x):\n    return x\n"),
    specFile: specFile("from solution import g\n\nclass O:\n    pass\n\ndef test_a():\n    assert g(O()) is not None\n") });
  assert.equal(nonLiteral.verdict, "inconclusive");
  assert.match(agreeCheck({ a: { worktree: ".", files: ["query.sql"] }, b: { worktree: ".", files: ["query.sql"] }, specFile: "x" }).reason, /no Python file/);
  assert.match(agreeCheck({ a: answer("def g(x):\n    return x\n"), b: answer("def g(x):\n    return x\n"), specFile: null }).reason, /no test-first file/);
  const spec = specFile("from solution import g\n\ndef test_a():\n    assert g(1) == 1\n");
  fs.mkdirSync(path.join(path.dirname(spec), "agree"));
  fs.writeFileSync(path.join(path.dirname(spec), "agree", "hypothesis.py"), "raise ImportError('not installed')\n");   // shadows the real one for this run
  assert.match(agreeCheck({ a: answer("def g(x):\n    return x\n"), b: answer("def g(x):\n    return x\n"), specFile: spec }).reason, /hypothesis is not installed/);
});

test("definesFunctions: a Python file with a top-level def, and nothing else (SQL, a snippet, a method)", () => {
  assert.equal(definesFunctions(answer("def f(x):\n    return x\n")), true);
  assert.equal(definesFunctions(answer("result = 1\n")), false);
  assert.equal(definesFunctions(answer("class A:\n    def f(self):\n        return 1\n")), false);
  assert.equal(definesFunctions(answer("select 1;\n", "query.sql")), false);
  assert.equal(firstDifference({ f: [["ok", "1"], ["ok", "2"]] }, { f: [["ok", "1"], ["ok", "3"]] }).index, 1);
  assert.equal(firstDifference({ f: [["ok", "1"]] }, { f: [["ok", "1"]] }), null);
});

// ---- through dispatch -----------------------------------------------------------------------------------------------------------

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "minimax-m2-7|stealth", "claude-opus-5-5|claude-code"] };
const MINE = "claude-opus-5-5|claude-code";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SPEC_REPLY = JSON.stringify({ result: "```python\nfrom solution import double\n\ndef test_a():\n    assert double(3) == 6\n```", total_cost_usd: 0.01 });

function setup(envExtra = {}) {
  const home = tmp("agree-home-");
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7"] } } }));
  const repo = tmp("agree-repo-");
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "solution.py"), "def double(x):\n    raise NotImplementedError\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { repo, env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_PARALLEL: "on", STEALTHLAB_DISPATCH_CROSSCHECK: "off",
                        STEALTHLAB_BRIEF: "off", STEALTHLAB_DISPATCH_TESTFIRST_MODEL: "claude-sonnet-5-5", ...envExtra } };
}
// each model: {code, delay, state}; every run gets its own worktree directory holding that model's solution.py
function runtime(repo, models) {
  const runs = new Map(), cancelled = [], wake = new Map();
  return { cancelled, applied: [],
    async achieve(i) { const id = `r-${i.model}`; runs.set(id, i.model); return { run_id: id }; },
    async runResult(id) {
      const m = models[runs.get(id)];
      await Promise.race([sleep(m.delay || 0), new Promise((r) => wake.set(id, r))]);      // a cancelled run ends at once, as the real runtime does
      const wt = tmp("agree-run-"); fs.writeFileSync(path.join(wt, "solution.py"), m.code);
      return { run_id: id, state: m.state || "verified", cost_usd: 0.001, diff: { files: ["solution.py"], worktree: wt }, checks: [{ tail: "ok" }] };
    },
    async applyRun(id) { this.applied.push(id); fs.writeFileSync(path.join(repo, "solution.py"), models[runs.get(id)].code); return { files: ["solution.py"] }; },
    async cancelRun(id) { cancelled.push(id); wake.get(id)?.(); return {}; } };
}
const run = (s, rt) => dispatch({ payload: { cwd: s.repo, prompt: "make double return twice x" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE, env: s.env, runtime: rt,
                                  runClaude: async () => ({ status: 0, stdout: SPEC_REPLY }) });
const GOOD = "def double(x):\n    return x * 2\n";

test("dispatch: a second pass that agrees -> the first is applied, the second is released", need, async () => {
  const s = setup();
  const rt = runtime(s.repo, { "gemma-4-31b-it": { code: GOOD }, "minimax-m2-7": { code: GOOD.replace("x * 2", "x + x"), delay: 300 } });
  const d = await run(s, rt);
  assert.equal(d.handled, true);
  assert.equal(d.agreement.verdict, "agree");
  assert.deepEqual(rt.applied, ["r-gemma-4-31b-it"]);
  assert.ok(rt.cancelled.includes("r-minimax-m2-7"));
  assert.ok(d.tried.some((t) => /passed too \(agree\)/.test(t.state)));
});

test("dispatch: two passes that disagree -> neither is applied, nothing cheaper is tried, the session is told", need, async () => {
  const s = setup();
  const rt = runtime(s.repo, { "gemma-4-31b-it": { code: GOOD }, "minimax-m2-7": { code: GOOD.replace("x * 2", "abs(x) * 2"), delay: 300 } });
  const d = await run(s, rt);
  assert.equal(d.handled, false);
  assert.equal(d.agreement.verdict, "disagree");
  assert.deepEqual(rt.applied, []);
  assert.equal(fs.readFileSync(path.join(s.repo, "solution.py"), "utf8"), "def double(x):\n    raise NotImplementedError\n", "the checkout is untouched");
  assert.ok(d.tried.every((t) => /disagrees with/.test(t.state)));
  assert.match(triedNote(d), /two cheap models both passed the check but disagreed \(double\(-\d+\)/);
  assert.match(triedNote(d), /neither change was applied/);
});

test("dispatch: a second answer that fails its check does not hold the first back; one that is too late is not waited for", need, async () => {
  const s = setup();
  const failing = runtime(s.repo, { "gemma-4-31b-it": { code: GOOD }, "minimax-m2-7": { code: "x", state: "failed", delay: 200 } });
  const d1 = await run(s, failing);
  assert.equal(d1.handled, true);
  assert.equal(d1.agreement, undefined, "nothing to compare");
  const late = setup({ STEALTHLAB_DISPATCH_AGREE_WAIT_S: "0.3" });
  const slow = runtime(late.repo, { "gemma-4-31b-it": { code: GOOD }, "minimax-m2-7": { code: GOOD.replace("x * 2", "abs(x) * 2"), delay: 3000 } });
  const t0 = Date.now();
  const d2 = await run(late, slow);
  assert.equal(d2.handled, true);
  assert.equal(d2.agreement, undefined);
  assert.ok(Date.now() - t0 < 2500, "the late answer was cancelled, not waited for");
});

test("dispatch: no second model running means no waiting; a SQL or snippet answer, a non-pytest check and the switch skip the check", async () => {
  const one = setup({ STEALTHLAB_DISPATCH_PARALLEL: "off" });
  const t0 = Date.now();
  const d = await run(one, runtime(one.repo, { "gemma-4-31b-it": { code: GOOD }, "minimax-m2-7": { code: GOOD } }));
  assert.equal(d.handled, true);
  assert.equal(d.agreement, undefined);
  assert.ok(Date.now() - t0 < 5000);
  for (const [env, code] of [[{}, "result = 2\n"], [{ STEALTHLAB_DISPATCH_CHECK: "npm test" }, GOOD], [{ STEALTHLAB_DISPATCH_AGREE: "off" }, GOOD]]) {
    const s = setup(env);
    const rt = runtime(s.repo, { "gemma-4-31b-it": { code }, "minimax-m2-7": { code: GOOD.replace("x * 2", "abs(x) * 2"), delay: 300 } });
    const r = await run(s, rt);
    assert.equal(r.handled, true, JSON.stringify(env));
    assert.equal(r.agreement, undefined, JSON.stringify(env));
    assert.deepEqual(rt.applied, ["r-gemma-4-31b-it"]);
  }
});

// ---- the offline replay -------------------------------------------------------------------------------------------------------------

test("replay: a wrong cheap answer is catchable only with a second cheap pass AND Python functions to compare", () => {
  const row = (task, suite, attempts) => ({ task_id: task, suite, arm: "C", status: "done", resolved: false, turns: 0, attempts });
  const r = replay([row("b1", "bird", [{ model: "gemma", state: "verified" }, { model: "oss", state: "cancelled" }]),
    row("m1", "mbpp", [{ model: "gemma", state: "verified" }, { model: "oss", state: "verified" }]),
    row("m2", "mbpp", [{ model: "gemma", state: "verified" }]),
    { ...row("ok", "mbpp", [{ model: "gemma", state: "verified" }]), resolved: true }]);
  assert.deepEqual(r.map((x) => [x.task, x.secondPass, x.functions]), [["b1", false, false], ["m1", true, true], ["m2", false, true]]);
});
