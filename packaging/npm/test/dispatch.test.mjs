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
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q" }, repo };
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
