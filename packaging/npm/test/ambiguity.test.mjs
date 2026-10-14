import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch, triedNote } from "../lib/dispatch.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth"] };
const MINE = "claude-opus-5-5|claude-code";

function setup() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "amb-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "amb-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "perm.py"), "def apply(a, p):\n    return a\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_CROSSCHECK: "off",
                  STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off", STEALTHLAB_DISPATCH_TESTFIRST: "on" }, repo };
}
function stub(repo, waitMs = 0) {
  const calls = [];
  return { calls, cancelled: 0,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) { await new Promise((r) => setTimeout(r, waitMs)); return { run_id: id, state: "verified", cost_usd: 0.001, checks: [{ tail: "" }] }; },
    async applyRun() { return { files: ["perm.py"] }; },
    async cancelRun() { this.cancelled++; } };
}
const TESTS = "def test_x():\n    assert True";

test("a task the test writer flags as reading two ways: no cheap answer is delivered, and the session is told the readings", async () => {
  const { env, repo } = setup();
  const runClaude = () => ({ stdout: JSON.stringify({ result: "# AMBIGUOUS: permute columns by p, or by the inverse of p\n" + TESTS, total_cost_usd: 0.01 }) });
  const d = await dispatch({ payload: { cwd: repo, prompt: "apply the permutation p to the columns of a" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env, runtime: stub(repo), runClaude });
  assert.equal(d.handled, false);
  assert.match(d.ambiguous, /inverse of p/);
  assert.match(triedNote(d), /read two ways \(permute columns by p, or by the inverse of p\)/);
});

test("the flag ends a still-running cheap try at once", async () => {
  const { env, repo } = setup();
  const runClaude = () => ({ stdout: JSON.stringify({ result: "# AMBIGUOUS: a or b\n" + TESTS, total_cost_usd: 0.01 }) });
  const rt = stub(repo, 15000);
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: "apply the permutation" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt, runClaude });
  assert.equal(d.handled, false);
  assert.ok(Date.now() - t0 < 11000, "did not wait for the 15 s run (5 s settle at most)");
  assert.ok(rt.cancelled >= 1);
});

test("no flag: delivered as before; the switch off: delivered despite a flag", async () => {
  const { env, repo } = setup();
  const plain = () => ({ stdout: JSON.stringify({ result: TESTS, total_cost_usd: 0.01 }) });
  assert.equal((await dispatch({ payload: { cwd: repo, prompt: "x" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: stub(repo), runClaude: plain })).handled, true);
  const flagged = () => ({ stdout: JSON.stringify({ result: "# AMBIGUOUS: a or b\n" + TESTS, total_cost_usd: 0.01 }) });
  const d = await dispatch({ payload: { cwd: repo, prompt: "x" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_AMBIGUITY: "off" }, runtime: stub(repo), runClaude: flagged });
  assert.equal(d.handled, true);
});
