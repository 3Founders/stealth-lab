import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { ExecRuntime } from "../lib/exec/runtime.mjs";
import { makeRepo, mockHosted, outboxFiles, stubAdapter, testEnv, writeExecConfig } from "./fixtures/exec/helpers.mjs";

const FIX = { edits: [{ path: "calc.txt", content: "fixed\n" }], finalMessage: "fixed calc" };
const WRONG = { edits: [{ path: "calc.txt", content: "nope\n" }], finalMessage: "I fixed it (I did not)" };
const TASK = "Fix calc.\nNODE|N-1|ready|make calc.txt say fixed|step=P-1:2|claims=R-1|deps=-|check=node check.mjs";

// The recommender's ladder: cheapest first.
async function ladderServer(ladder) {
  return mockHosted((name) => {
    if (name === "recommend_models") {
      return { text: JSON.stringify({ status: "ok", recommendation_id: "rec-e", instance_key: "ik-e",
        recommended: { ladder, p_success_q05: 0.6, p_success_q95: 0.8 } }) };
    }
    return { status: 500, text: "down" };
  });
}

async function setup({ cheap, mid, strong }) {
  const repo = makeRepo();
  const hosted = await ladderServer(["c1|cheap", "m1|mid", "s1|strong"]);
  const env = testEnv({ url: hosted.url });
  writeExecConfig(env, { executors: { cheap: { models: ["c1"] }, mid: { models: ["m1"] }, strong: { models: ["s1"] } } });
  const rt = new ExecRuntime({ env, adapters: {
    cheap: stubAdapter({ id: "cheap", scenario: cheap }),
    mid: stubAdapter({ id: "mid", scenario: mid }),
    strong: stubAdapter({ id: "strong", scenario: strong }),
  } });
  const start = (extra = {}) => rt.achieve({ repo_path: repo, task: TASK, checks: ["node check.mjs"], scope: ["calc.txt"],
    goal_id: "g-1", ...extra });
  return { repo, hosted, env, rt, start };
}

test("escalate: cheap and mid fail their checks, the strong rung verifies; every rung reports its own outcome", async () => {
  const s = await setup({ cheap: WRONG, mid: WRONG, strong: FIX });
  try {
    const { run_id } = await s.start({ escalate: 2 });
    const res = await s.rt.runResult(run_id, 50);
    assert.equal(res.state, "verified");
    assert.equal(res.executor, "strong");
    assert.equal(res.attempt, 3);
    assert.equal(res.escalated, 2);
    assert.deepEqual(res.race.map((r) => [r.executor, r.state, !!r.escalated]),
      [["cheap", "failed", false], ["mid", "failed", true], ["strong", "verified", true]]);
    const reports = outboxFiles(s.env).map((e) => e.args).sort((a, b) => a.attempt_index - b.attempt_index);
    assert.deepEqual(reports.map((a) => [a.scaffold, a.accepted, a.attempt_index]),
      [["cheap", false, 0], ["mid", false, 1], ["strong", true, 2]]);
    for (const a of reports) {
      assert.equal(a.instance_key, "ik-e");
      assert.equal(a.goal_id, "g-1");
      assert.equal(a.check_kind, "tests");
    }
    assert.equal(fs.readFileSync(path.join(s.repo, "calc.txt"), "utf8").replace(/\r\n/g, "\n"), "broken\n",
      "the checkout is untouched until apply_run");
    const events = fs.readFileSync(path.join(s.env.STEALTHLAB_HOME, "runs", run_id, "events.jsonl"), "utf8");
    assert.equal((events.match(/"event":"escalate"/g) || []).length, 2);
    await s.rt.cancelRun(run_id);
  } finally {
    await s.hosted.close();
  }
});

test("escalate defaults to 0: one failed attempt, one report, no further rung", async () => {
  const s = await setup({ cheap: WRONG, mid: FIX, strong: FIX });
  try {
    const { run_id } = await s.start();
    const res = await s.rt.runResult(run_id, 50);
    assert.equal(res.state, "failed");
    assert.equal(res.executor, "cheap");
    assert.equal(res.race, undefined);
    assert.equal(outboxFiles(s.env).length, 1);
    await s.rt.cancelRun(run_id);
  } finally {
    await s.hosted.close();
  }
});

test("escalate stops at its cap, and never runs when the first rung verifies", async () => {
  const capped = await setup({ cheap: WRONG, mid: WRONG, strong: FIX });
  try {
    const { run_id } = await capped.start({ escalate: 1 });
    const res = await capped.rt.runResult(run_id, 50);
    assert.equal(res.state, "failed");
    assert.deepEqual(res.race.map((r) => r.executor), ["cheap", "mid"], "strong never ran");
    await capped.rt.cancelRun(run_id);
  } finally {
    await capped.hosted.close();
  }
  const easy = await setup({ cheap: FIX, mid: FIX, strong: FIX });
  try {
    const { run_id } = await easy.start({ escalate: 3 });
    const res = await easy.rt.runResult(run_id, 50);
    assert.equal(res.state, "verified");
    assert.equal(res.executor, "cheap");
    assert.equal(res.escalated, undefined);
    assert.equal(outboxFiles(easy.env).length, 1);
    await easy.rt.cancelRun(run_id);
  } finally {
    await easy.hosted.close();
  }
});

test("escalate is validated like every numeric input: clamped to 0..3, non-numbers refused", async () => {
  const s = await setup({ cheap: WRONG, mid: WRONG, strong: WRONG });
  try {
    await assert.rejects(s.start({ escalate: "lots" }), /not a number/);
    const { run_id } = await s.start({ escalate: 99 });   // clamped to 3; the ladder has only 2 more rungs
    const res = await s.rt.runResult(run_id, 50);
    assert.equal(res.state, "failed");
    assert.deepEqual(res.race.map((r) => r.executor), ["cheap", "mid", "strong"]);
    await s.rt.cancelRun(run_id);
  } finally {
    await s.hosted.close();
  }
});
