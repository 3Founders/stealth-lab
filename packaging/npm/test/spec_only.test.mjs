import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { dispatch, writeSpecRunner } from "../lib/dispatch.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth"] };
const MINE = "claude-opus-5-5|claude-code";
const hasPytest = spawnSync("python", ["-m", "pytest", "--version"], { encoding: "utf8" }).status === 0;

// a repo with NO test command anywhere (no tests dir, no package.json, no pytest config)
function setup() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "so-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "so-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "def add(a, b):\n    return a - b\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off",
                  STEALTHLAB_DISPATCH_TESTFIRST: "on" }, repo };
}
function stub(repo) {
  const calls = [];
  return { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) { return { run_id: id, state: "verified", cost_usd: 0.001, checks: [{ tail: "" }] }; },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "def add(a, b):\n    return a + b\n"); return { files: ["calc.py"] }; },
    async cancelRun() {} };
}
const writer = () => ({ stdout: JSON.stringify({ result: "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5", total_cost_usd: 0.01 }) });

test("no test command: the tests written for the task are the whole check, and the user is told so", async () => {
  const { env, repo } = setup();
  const rt = stub(repo);
  const d = await dispatch({ payload: { cwd: repo, prompt: "add in calc.py subtracts; make it add" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env, runtime: rt, runClaude: writer });
  assert.equal(d.handled, true);
  assert.match(rt.calls[0].checks[0], /^python ".*run_spec\.py"$/, "only the written tests, no project command");
  assert.match(d.text, /the tests written for this task \(the project has no test command\)/);
});

test("no test command and the switch off: no dispatch, as before", async () => {
  const { env, repo } = setup();
  const rt = stub(repo);
  const d = await dispatch({ payload: { cwd: repo, prompt: "add in calc.py subtracts; make it add" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env: { ...env, STEALTHLAB_DISPATCH_SPEC_ONLY: "off" }, runtime: rt, runClaude: writer });
  assert.equal(d, null);
  assert.equal(rt.calls.length, 0);
});

test("no test command and test-first off: no dispatch (nothing could check the answer)", async () => {
  const { env, repo } = setup();
  const d = await dispatch({ payload: { cwd: repo, prompt: "add in calc.py subtracts; make it add" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env: { ...env, STEALTHLAB_DISPATCH_TESTFIRST: "off" }, runtime: stub(repo), runClaude: writer });
  assert.equal(d, null);
});

test("the strict runner fails when no tests could be written; the normal one passes (the project's check still decides)", { skip: !hasPytest }, () => {
  for (const [strict, want] of [[true, 1], [false, 0]]) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "so-spec-"));
    fs.writeFileSync(path.join(dir, "SKIP"), "");
    const r = spawnSync("python", [writeSpecRunner(dir, { strict })], { encoding: "utf8" });
    assert.equal(r.status, want, `strict=${strict}: ${r.stdout}${r.stderr}`);
  }
});

test("tests that do not parse get a second attempt", { skip: !hasPytest }, async () => {
  const { writeSpecTests } = await import("../lib/dispatch.mjs");
  const { env, repo } = setup();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "so-retry-"));
  const replies = ["def test_x(:\n    assert True", "def test_x():\n    assert True"];
  let n = 0;
  const runClaude = () => ({ stdout: JSON.stringify({ result: replies[n++], total_cost_usd: 0.01 }) });
  const r = await writeSpecTests({ prompt: "fix add", root: repo, mine: MINE, env, runClaude, dir });
  assert.equal(n, 2, "asked twice");
  assert.ok(r && fs.existsSync(r.file));
  assert.match(fs.readFileSync(r.file, "utf8"), /def test_x\(\):/);
});
