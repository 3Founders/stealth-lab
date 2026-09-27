import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { PassThrough } from "node:stream";

import { ExecRuntime } from "../lib/exec/runtime.mjs";
import { runExecServer } from "../lib/exec/server.mjs";
import {
  alive, git, makeRepo, mockHosted, outboxFiles, pidsFrom, sleep, stubAdapter, testEnv, tmpDir, waitFor, writeExecConfig,
} from "./fixtures/exec/helpers.mjs";

const FIX = { edits: [{ path: "calc.txt", content: "fixed\n" }], finalMessage: "fixed calc", learned: ["calc needed a fix"] };
const TASK = "Fix calc.\nNODE|N-1|ready|make calc.txt say fixed|step=P-1:2|claims=R-1|deps=-|check=node check.mjs";

function runtime(env, adapters) {
  return new ExecRuntime({ env, adapters });
}
function readEvents(env, runId) {
  return fs.readFileSync(path.join(env.STEALTHLAB_HOME, "runs", runId, "events.jsonl"), "utf8")
    .trim().split("\n").map((l) => JSON.parse(l));
}

test("lifecycle (spec 8.1): achieve returns at once; queued -> running -> verifying -> verified; checkout untouched", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const rt = runtime(env, { stub: stubAdapter({ scenario: { ...FIX, sleepMs: 700 } }) });
  const t0 = Date.now();
  const started = await rt.achieve({ repo_path: repo, task: TASK, checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1", goal_id: "g-1" });
  assert.ok(Date.now() - t0 < 2000, "achieve must return in < 2 s");
  assert.equal(started.state, "queued");
  assert.match(started.run_id, /^r-\d{14}-/);
  assert.equal(started.executor, "stub");

  const seen = new Set();
  await waitFor(async () => { const s = await rt.runStatus(started.run_id); seen.add(s.state); return s.state === "running"; });
  assert.ok(seen.has("running"));
  const res = await rt.runResult(started.run_id, 30);
  assert.equal(res.state, "verified");
  assert.equal(res.verified, true);
  assert.deepEqual(res.diff.files, ["calc.txt"]);
  assert.match(res.diff.stat, /calc\.txt/);
  assert.ok(fs.existsSync(res.diff.patch_path));
  assert.equal(res.checks[0].exit, 0);
  assert.equal(res.summary, "fixed calc");
  assert.deepEqual(res.learned, ["calc needed a fix"]);
  assert.equal(res.executor, "stub");
  assert.equal(res.model, "m1");
  assert.equal(res.attempt, 1);
  assert.deepEqual(res.tokens, { in: 10, out: 5 });
  assert.equal(res.evidence.queued, true, "no hosted URL -> outbox");
  assert.equal(outboxFiles(env)[0].args.check_kind, "tests");
  assert.equal(outboxFiles(env)[0].args.step_order, 2, "step_order taken from the NODE line");

  const order = readEvents(env, started.run_id).map((e) => e.event).filter((e) => ["queued", "running", "verifying", "verified"].includes(e));
  assert.deepEqual(order, ["queued", "running", "verifying", "verified"]);
  assert.equal(fs.readFileSync(path.join(repo, "calc.txt"), "utf8"), "broken\n", "main checkout untouched");
  assert.equal(git(repo, "status", "--porcelain").trim(), "");
  assert.ok(fs.existsSync(res.diff.worktree), "verified, unapplied runs keep their worktree");
  await rt.cancelRun(started.run_id);
  assert.ok(!fs.existsSync(res.diff.worktree));
});

test("failing check (spec 8.2): verified=false, tail present and redacted; logs and summary redacted too", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const rt = runtime(env, { stub: stubAdapter({ scenario: {
    edits: [{ path: "calc.txt", content: "still wrong\n" }], printSecret: true,
    finalMessage: "all good, used sk-test-ABCDEFGHIJKLMNOPQRSTUVWX" } }) });
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1", goal_id: "g-1" });
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.state, "failed");
  assert.equal(res.verified, false, "the agent's own 'all good' never sets verified");
  assert.equal(res.checks[0].exit, 1);
  assert.match(res.checks[0].tail, /FAILED: expected fixed/);
  assert.ok(res.checks[0].tail.split("\n").length <= 40);
  assert.match(res.checks[0].tail, /\[REDACTED:secret_key\]/);
  const everything = JSON.stringify(res) +
    fs.readdirSync(path.join(env.STEALTHLAB_HOME, "runs", run_id)).filter((f) => !f.endsWith(".patch"))
      .map((f) => fs.readFileSync(path.join(env.STEALTHLAB_HOME, "runs", run_id, f), "utf8")).join("\n") +
    JSON.stringify(outboxFiles(env));
  assert.ok(!everything.includes("sk-test-ABCDEFGHIJKLMNOPQRSTUVWX"), "secret must not reach result, run store or outbox");
  assert.ok(!everything.includes(env.STEALTHLAB_TOKEN), "the StealthLab token must not appear anywhere");
  assert.equal(outboxFiles(env)[0].args.accepted, false);
  assert.ok(fs.existsSync(res.diff.worktree), "failed WITH a diff: worktree kept for inspection");
});

test("scope violation (spec 8.3): verified=false and the out-of-scope file is listed", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const rt = runtime(env, { stub: stubAdapter({ scenario: { edits: [...FIX.edits, { path: "other.txt", content: "touched\n" }, { path: "new/dir/x.txt", content: "x" }] } }) });
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1" });
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.verified, false);
  assert.equal(res.checks[0].exit, 0, "checks pass, scope still fails it");
  assert.deepEqual(res.scope_violations.sort(), ["new/dir/x.txt", "other.txt"]);
  await rt.cancelRun(run_id);
});

test("hang (spec 8.4): no output for hang_s -> timed_out within hang_s + 5 s, agent process gone", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const pidFile = path.join(tmpDir(), "pids");
  const rt = runtime(env, { stub: stubAdapter({ scenario: { silentMs: 60000, pidFile } }) });
  const t0 = Date.now();
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1", hang_s: 1, goal_id: "g-1" });
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.state, "timed_out");
  assert.equal(res.stop_reason, "hang");
  assert.ok(Date.now() - t0 < 1000 + 5000 + 3000, `took ${Date.now() - t0} ms`);
  assert.equal(res.checks.length, 0, "a timed-out run is not verified at all");
  const agent = pidsFrom(pidFile).find((p) => p.role === "agent");
  await waitFor(() => !alive(agent.pid), { timeoutMs: 5000 });
  assert.equal(res.diff.worktree, null, "timed out with no diff: worktree removed");
  assert.equal(outboxFiles(env)[0].args.check_kind, "self_report");
});

test("hard timeout (spec 8.5): the whole tree dies, grandchild included", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const pidFile = path.join(tmpDir(), "pids");
  const rt = runtime(env, { stub: stubAdapter({ scenario: { chatterMs: 60000, spawnGrandchild: true, pidFile } }) });
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1", timeout_s: 2, hang_s: 30 });
  await waitFor(() => pidsFrom(pidFile).length === 2);
  const pids = pidsFrom(pidFile);
  assert.ok(pids.every((p) => alive(p.pid)), "agent and grandchild running before the timeout");
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.state, "timed_out");
  assert.equal(res.stop_reason, "timeout");
  for (const p of pids) await waitFor(() => !alive(p.pid), { timeoutMs: 5000 }).catch(() => assert.fail(`${p.role} ${p.pid} survived`));
});

test("apply_run (spec 8.6): refuses unverified, refuses a changed target, applies otherwise", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const bad = stubAdapter({ id: "bad", scenario: { edits: [{ path: "calc.txt", content: "nope\n" }] } });
  const good = stubAdapter({ id: "good", scenario: { edits: [...FIX.edits, { path: "sub/new.txt", content: "new\n" }] } });
  const rt = runtime(env, { bad, good });
  const base = { repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt", "sub/**"], model: "m1" };

  const r1 = await rt.achieve({ ...base, executor: "bad" });
  assert.equal((await rt.runResult(r1.run_id, 30)).verified, false);
  await assert.rejects(rt.applyRun(r1.run_id), /not verified/);

  const r2 = await rt.achieve({ ...base, executor: "good" });
  assert.equal((await rt.runResult(r2.run_id, 30)).verified, true);
  fs.writeFileSync(path.join(repo, "calc.txt"), "user edited meanwhile\n");
  await assert.rejects(rt.applyRun(r2.run_id), /changed since the run started: calc\.txt/);
  assert.equal(fs.readFileSync(path.join(repo, "calc.txt"), "utf8"), "user edited meanwhile\n");
  git(repo, "checkout", "--", "calc.txt");

  const r3 = await rt.achieve({ ...base, executor: "good" });
  const res3 = await rt.runResult(r3.run_id, 30);
  assert.equal(res3.verified, true);
  assert.equal(fs.readFileSync(path.join(repo, "calc.txt"), "utf8"), "broken\n", "untouched before apply_run");
  const applied = await rt.applyRun(r3.run_id);
  assert.deepEqual(applied.files.sort(), ["calc.txt", "sub/new.txt"]);
  assert.equal(fs.readFileSync(path.join(repo, "calc.txt"), "utf8"), "fixed\n");
  assert.equal(fs.readFileSync(path.join(repo, "sub", "new.txt"), "utf8"), "new\n");
  assert.ok(!fs.existsSync(res3.diff.worktree), "worktree removed after apply");
  await assert.rejects(rt.applyRun(r3.run_id), /already applied/);
  assert.equal(git(repo, "worktree", "list").trim().split("\n").length, 3, "r1 (failed with a diff) and r2 (verified) are kept");
  await rt.cancelRun(r1.run_id);
  await rt.cancelRun(r2.run_id);
  assert.equal(git(repo, "worktree", "list").trim().split("\n").length, 1);
});

test("base=working-tree copies the uncommitted diff; it is not counted as the run's change", async () => {
  const repo = makeRepo();
  const env = testEnv();
  fs.writeFileSync(path.join(repo, "other.txt"), "user's uncommitted edit\n");
  const seen = path.join(tmpDir(), "seen");
  const rt = runtime(env, { stub: stubAdapter({ scenario: { ...FIX } }) });
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: [`node -e "require('fs').copyFileSync('other.txt', process.argv[1])" "${seen}"`, "node check.mjs"],
    scope: ["calc.txt"], executor: "stub", model: "m1", base: "working-tree" });
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.verified, true, JSON.stringify(res.checks));
  assert.deepEqual(res.diff.files, ["calc.txt"]);
  assert.equal(fs.readFileSync(seen, "utf8"), "user's uncommitted edit\n");
  await rt.cancelRun(run_id);
});

test("refuses a repo with an unresolved merge, a missing scope, and an oversized task", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const rt = runtime(env, { stub: stubAdapter({ scenario: FIX }) });
  const base = { repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub" };
  await assert.rejects(rt.achieve({ ...base, scope: [] }), /scope is required/);
  await assert.rejects(rt.achieve({ ...base, task: "x".repeat(2001) }), /2,000/);
  await assert.rejects(rt.achieve({ ...base, checks: [] }), /checks are required/);
  await assert.rejects(rt.achieve({ ...base, repo_path: tmpDir() }), /not inside a git work tree/);
  fs.writeFileSync(path.join(repo, ".git", "MERGE_HEAD"), git(repo, "rev-parse", "HEAD"));
  await assert.rejects(rt.achieve(base), /unresolved merge/);
});

test("adapter refusal at runtime (spec 8.10): missing VERIFIED_WITH or a major mismatch never spawns", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const pidFile = path.join(tmpDir(), "pids");
  const rt = runtime(env, {
    unverified: stubAdapter({ id: "unverified", verifiedWith: null, scenario: { ...FIX, pidFile } }),
    newer: stubAdapter({ id: "newer", version: "2.0.1", scenario: { ...FIX, pidFile } }),
  });
  for (const [executor, why] of [["unverified", /no VERIFIED_WITH/], ["newer", /major version 2/]]) {
    const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor });
    const res = await rt.runResult(run_id, 30);
    assert.equal(res.state, "failed");
    assert.match(res.error, why);
  }
  assert.equal(pidsFrom(pidFile).length, 0, "the agent never ran");
  assert.equal(outboxFiles(env).length + outboxFiles(env, "held").length, 0, "nothing ran, so nothing is reported");
  const listed = await rt.listExecutors();
  assert.deepEqual(listed.map((x) => [x.id, x.verified_flags, x.runnable]), [["unverified", false, false], ["newer", true, false]]);
});

test("race (spec 8.7): the faster verified attempt wins, the other is cancelled, both reported via the outbox", async () => {
  const repo = makeRepo();
  const hosted = await mockHosted((name) => {
    if (name === "recommend_models") {
      return { text: JSON.stringify({ status: "ok", recommendation_id: "rec-7", instance_key: "ik-7",
        recommended: { ladder: ["s1|slow", "f1|fast"], p_success_q05: 0.4, p_success_q95: 0.6 } }) };
    }
    return { status: 500, text: "down" };
  });
  const env = testEnv({ url: hosted.url });
  writeExecConfig(env, { executors: { slow: { models: ["s1"] }, fast: { models: ["f1"] } } });
  const rt = runtime(env, {
    slow: stubAdapter({ id: "slow", scenario: { ...FIX, chatterMs: 20000 } }),
    fast: stubAdapter({ id: "fast", scenario: { ...FIX, sleepMs: 300 } }),
  });
  try {
    const { run_id } = await rt.achieve({ repo_path: repo, task: TASK, checks: ["node check.mjs"], scope: ["calc.txt"], goal_id: "g-1", race: 2 });
    const res = await rt.runResult(run_id, 45);
    assert.equal(res.state, "verified");
    assert.equal(res.executor, "fast");
    assert.equal(res.attempt, 2, "the recommender ranked slow first; the verified one wins");
    assert.deepEqual(res.race.map((r) => [r.executor, r.state]), [["slow", "cancelled"], ["fast", "verified"]]);
    const queued = outboxFiles(env).map((e) => e.args);
    assert.deepEqual(queued.map((a) => [a.scaffold, a.accepted]).sort(), [["fast", true], ["slow", false]]);
    for (const a of queued) {
      assert.equal(a.instance_key, "ik-7");
      assert.equal(a.recommendation_id, "rec-7");
      assert.equal(a.goal_id, "g-1");
    }
    assert.ok(!fs.existsSync(res.race[0].worktree || path.join(env.STEALTHLAB_EXEC_WORKTREE_ROOT, `${run_id}-a1`)), "loser's worktree removed");
    await rt.cancelRun(run_id);
  } finally {
    await hosted.close();
  }
});

test("MCP stdio server: initialize, tools/list, tools/call, errors as isError; runs survive restart via run.json", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const stdin = new PassThrough();
  const stdout = new PassThrough();
  const lines = [];
  let buf = "";
  stdout.on("data", (d) => { buf += d; let i; while ((i = buf.indexOf("\n")) !== -1) { lines.push(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1); } });
  const adapters = { stub: stubAdapter({ scenario: FIX }) };
  const server = runExecServer({ stdin, stdout, stderr: new PassThrough(), env, adapters });
  const send = (m) => stdin.write(JSON.stringify(m) + "\n");
  const reply = (id) => waitFor(() => lines.find((l) => l.id === id));
  const call = async (id, name, args) => { send({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } }); return reply(id); };

  send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t" } } });
  assert.equal((await reply(1)).result.serverInfo.name, "stealthlab-exec");
  send({ jsonrpc: "2.0", method: "notifications/initialized" });
  send({ jsonrpc: "2.0", id: 2, method: "tools/list" });
  assert.deepEqual((await reply(2)).result.tools.map((t) => t.name),
    ["list_executors", "achieve", "run_status", "run_result", "apply_run", "cancel_run"]);
  const listed = JSON.parse((await call(3, "list_executors", {})).result.content[0].text);
  assert.equal(listed[0].id, "stub");
  const bad = await call(4, "achieve", { repo_path: repo, task: "x", checks: ["node check.mjs"] });
  assert.equal(bad.result.isError, true);
  assert.match(bad.result.content[0].text, /scope is required/);
  const started = JSON.parse((await call(5, "achieve", { repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1" })).result.content[0].text);
  const result = JSON.parse((await call(6, "run_result", { run_id: started.run_id, wait_s: 30 })).result.content[0].text);
  assert.equal(result.verified, true);
  send({ jsonrpc: "2.0", id: 7, method: "nope" });
  assert.equal((await reply(7)).error.code, -32601);
  stdin.write("not json\n");
  await waitFor(() => lines.find((l) => l.id === null && l.error?.code === -32700));
  stdin.end();
  await server;

  // A new server process (fresh runtime) can still apply the verified run from run.json.
  const rt2 = runtime(env, adapters);
  assert.equal((await rt2.runStatus(started.run_id)).state, "verified");
  const applied = await rt2.applyRun(started.run_id);
  assert.deepEqual(applied.files, ["calc.txt"]);
  assert.equal(fs.readFileSync(path.join(repo, "calc.txt"), "utf8"), "fixed\n");
  await sleep(10);
});

test("gc: a verified-but-unapplied run's worktree is removed after 7 days, not before", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const adapters = { stub: stubAdapter({ scenario: FIX }) };
  const rt = runtime(env, adapters);
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1" });
  const res = await rt.runResult(run_id, 30);
  assert.ok(fs.existsSync(res.diff.worktree));
  const later = runtime(env, adapters); // a later server process
  assert.equal((await later.gc({ now: Date.now() + 6 * 24 * 3600 * 1000 })).removed, 0);
  assert.equal((await later.gc({ now: Date.now() + 8 * 24 * 3600 * 1000 })).removed, 1);
  assert.ok(!fs.existsSync(res.diff.worktree));
  assert.equal(git(repo, "worktree", "list").trim().split("\n").length, 1, "pruned");
});

// Integration with Builder B's real adapter registry (lib/executors/index.mjs), loaded lazily exactly as
// `stealthlab-mcp exec` does. Skipped until that module exists in the tree.
const HAS_REGISTRY = fs.existsSync(new URL("../lib/executors/index.mjs", import.meta.url));
test("lazy adapter registry: the real `fake` adapter runs end to end (STEALTHLAB_EXEC_ALLOW_FAKE=1)",
  { skip: !HAS_REGISTRY && "lib/executors/index.mjs not present yet (built in parallel by the adapters builder)" }, async () => {
  const repo = makeRepo();
  const env = testEnv({ extra: { STEALTHLAB_EXEC_ALLOW_FAKE: "1",
    STEALTHLAB_FAKE_SCENARIO: JSON.stringify({ edits: [{ path: "calc.txt", content: "fixed\n" }], finalMessage: "done" }) } });
  const rt = new ExecRuntime({ env });
  const ids = (await rt.listExecutors()).map((x) => x.id);
  assert.ok(ids.includes("fake"), ids.join(","));
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "fake" });
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.verified, true, JSON.stringify(res));
  await rt.cancelRun(run_id);
  const prod = new ExecRuntime({ env: { ...env, STEALTHLAB_EXEC_ALLOW_FAKE: "" } });
  assert.ok(!(await prod.listExecutors()).some((x) => x.id === "fake"), "fake is never selectable in production");
});
