import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { StallDetector, earlyStopEnabled, failurePaths, failureSignature } from "../lib/agent/stall.mjs";
import { runAgent, runTool, worktreeFingerprint } from "../lib/agent/step_agent.mjs";
import stealth from "../lib/executors/stealth.mjs";
import { normaliseProfile } from "../lib/executors/profile.mjs";
import { ExecRuntime } from "../lib/exec/runtime.mjs";
import { makeRepo, stubAdapter, testEnv } from "./fixtures/exec/helpers.mjs";

const KEY = "sk-test-key-123456";
const PYTEST_FAIL = "[exit 1]\n============ FAILURES ============\n_____ test_add _____\ncalc.py:3: AssertionError\nassert 1 == 2\n=========== short test summary info ===========\nFAILED tests/test_calc.py::test_add - AssertionError\n";
const edit = (path_, hash, result = "edited") => ({ name: "edit_file", args: { path: path_ }, result, hash });
const shell = (command, result) => ({ name: "run_shell", args: { command }, result });

// ---- the signature and paths of a failure ---------------------------------------------------------------------------------

test("failureSignature: failing test names and the first error line, for pytest, jest, go and cargo", () => {
  assert.deepEqual(failureSignature(PYTEST_FAIL), { names: ["tests/test_calc.py::test_add"], first: "calc.py:3: AssertionError" });
  assert.deepEqual(failureSignature("FAIL src/a.test.js\n  ✕ adds (3 ms)\n").names, ["adds (3 ms)", "src/a.test.js"].sort());
  assert.deepEqual(failureSignature("--- FAIL: TestAdd (0.00s)\n").names, ["TestAdd"]);
  assert.deepEqual(failureSignature("test calc::add ... FAILED\n").names, ["calc::add"]);
  assert.deepEqual(failureSignature("all good"), { names: [], first: "" });
});

test("failurePaths: the code files a failure names", () => {
  assert.deepEqual([...failurePaths(PYTEST_FAIL)].sort(), ["calc.py", "tests/test_calc.py"]);
  assert.deepEqual([...failurePaths("no paths here")], []);
});

// ---- rule c: three tool calls in a row failed ------------------------------------------------------------------------------

test("rule c: three failed tool calls in a row stop the run; a success in between resets the count", () => {
  const d = new StallDetector();
  assert.equal(d.observe({ name: "edit_file", args: { path: "a.py" }, result: "error: old_text not found" }), null);
  assert.equal(d.observe({ name: "read_file", args: { path: "nope.py" }, result: "error: no such file" }), null);
  assert.equal(d.observe({ name: "list_files", args: {}, result: "a.py" }), null);                 // reset
  assert.equal(d.observe({ name: "edit_file", args: { path: "a.py" }, result: "error: old_text not found" }), null);
  assert.equal(d.observe({ name: "edit_file", args: { path: "a.py" }, result: "error: old_text not found" }), null);
  assert.deepEqual(d.observe({ name: "edit_file", args: { path: "a.py" }, result: "error: old_text not found" }),
    { rule: "c", why: "3 tool calls in a row failed" });
});

// ---- rule b: a file comes back to a content it had -------------------------------------------------------------------------

test("rule b: undoing an edit (back to the original, or to an earlier state) stops the run; a no-op write and moving on do not", () => {
  const d = new StallDetector();
  d.seed("a.py", "H0");
  assert.equal(d.observe(edit("a.py", "H1")), null);
  assert.equal(d.observe(edit("a.py", "H2")), null);
  assert.equal(d.observe(edit("a.py", "H2")), null, "writing the same content again is a no-op, not an oscillation");
  assert.deepEqual(d.observe(edit("a.py", "H1")), { rule: "b", why: "a.py came back to a content it already had (an edit undone and redone)" });
  const back = new StallDetector();
  back.seed("a.py", "H0");
  back.observe(edit("a.py", "H1"));
  assert.equal(back.observe(edit("a.py", "H0")).rule, "b", "putting the original back is a return");
  const fresh = new StallDetector();
  fresh.seed("a.py", "H0"); fresh.seed("b.py", "G0");
  assert.equal(fresh.observe(edit("a.py", "H1")), null);
  assert.equal(fresh.observe(edit("b.py", "G1")), null);
  assert.equal(fresh.observe(edit("a.py", "H2")), null);
});

// ---- rule a: the same failure twice, edits that cannot matter ----------------------------------------------------------------

test("rule a: the same check failing the same way twice stops the run when the edits in between touched nothing it points at", () => {
  const d = new StallDetector();
  d.seed("README.md", "R0"); d.seed("calc.py", "C0");
  assert.equal(d.observe(shell("python -m pytest -q", PYTEST_FAIL)), null);
  assert.equal(d.observe(edit("README.md", "R1")), null);                                   // unrelated edit
  const stop = d.observe(shell("python -m pytest -q", PYTEST_FAIL));
  assert.equal(stop.rule, "a");
  assert.match(stop.why, /tests\/test_calc\.py::test_add/);
});

test("rule a: once it has changed something, no edit between two identical failures stops it; before any change it is exploring; an edit to the file the failure names does NOT", () => {
  const explore = new StallDetector();                                                        // nothing changed yet: a probe re-run
  explore.observe(shell("python -m pytest -q", PYTEST_FAIL));
  assert.equal(explore.observe(shell("python -m pytest -q", PYTEST_FAIL)), null);
  const none = new StallDetector();
  none.seed("other.py", "O0");
  none.observe(edit("other.py", "O1"));
  none.observe(shell("python -m pytest -q", PYTEST_FAIL));
  assert.equal(none.observe(shell("python -m pytest -q", PYTEST_FAIL)).rule, "a");
  const relevant = new StallDetector();
  relevant.seed("calc.py", "C0");
  relevant.observe(shell("python -m pytest -q", PYTEST_FAIL));
  relevant.observe(edit("calc.py", "C1"));                                                    // calc.py is what the traceback names
  assert.equal(relevant.observe(shell("python -m pytest -q", PYTEST_FAIL)), null);
  const other = new StallDetector();                                                          // a different failure is progress
  other.observe(shell("python -m pytest -q", PYTEST_FAIL));
  assert.equal(other.observe(shell("python -m pytest -q", PYTEST_FAIL.replaceAll("test_add", "test_sub"))), null);
  const pass = new StallDetector();                                                           // a pass in between clears it
  pass.observe(shell("python -m pytest -q", PYTEST_FAIL));
  pass.observe(shell("python -m pytest -q", "[exit 0]\n3 passed"));
  assert.equal(pass.observe(shell("python -m pytest -q", PYTEST_FAIL)), null);
  const unknown = new StallDetector();                                                        // a failure with no recognisable signature is not compared
  unknown.observe(shell("make build", "[exit 2]\n"));
  assert.equal(unknown.observe(shell("make build", "[exit 2]\n")), null);
});

// ---- rule d: six calls without a change, after the first change ------------------------------------------------------------------

test("rule d: six tool calls in a row without a change to the worktree stop the run, but only after the first change", () => {
  const d = new StallDetector();
  d.start("F0");
  for (let i = 0; i < 12; i++) assert.equal(d.observe({ name: "read_file", args: { path: "a.py" }, result: "x" }), null, "exploring is never counted");
  assert.equal(d.observe({ name: "edit_file", args: { path: "a.py" }, result: "ok", hash: "H1", fingerprint: "F1" }), null);
  for (let i = 0; i < 5; i++) assert.equal(d.observe({ name: "read_file", args: { path: "a.py" }, result: "x" }), null);
  assert.deepEqual(d.observe({ name: "read_file", args: { path: "a.py" }, result: "x" }), { rule: "d", why: "6 tool calls in a row without any change to the worktree" });
  const moving = new StallDetector();
  moving.start("F0");
  moving.observe({ name: "write_file", args: { path: "a.py" }, result: "ok", hash: "H1", fingerprint: "F1" });
  for (let i = 0; i < 5; i++) moving.observe({ name: "read_file", args: {}, result: "x" });
  assert.equal(moving.observe({ name: "run_shell", args: { command: "sed -i s/a/b/ a.py" }, result: "[exit 0]", fingerprint: "F2" }), null, "a shell command that changes files counts as a change");
  const nogit = new StallDetector({ noChangeSteps: 2 });
  nogit.start(null);
  for (let i = 0; i < 6; i++) assert.equal(nogit.observe({ name: "read_file", args: {}, result: "x" }), null, "no git, no rule d");
});

test("thresholds are settable, and the switch is read from the environment", () => {
  const d = new StallDetector({ noChangeSteps: 2, errorRun: 2 });
  d.observe({ name: "edit_file", args: { path: "a" }, result: "error: x" });
  assert.equal(d.observe({ name: "edit_file", args: { path: "a" }, result: "error: x" }).rule, "c");
  assert.equal(earlyStopEnabled({}), true);
  assert.equal(earlyStopEnabled({ STEALTHLAB_EARLY_STOP: "off" }), false);
});

// ---- inside the agent loop, against a fake provider --------------------------------------------------------------------------------

function provider(script) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push(body ? JSON.parse(body) : null);
      const out = script(seen.length);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ choices: [{ message: out }], usage: { prompt_tokens: 100, completion_tokens: 10 } }));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ seen, url: `http://127.0.0.1:${server.address().port}/v1`, close: () => server.close() })));
}
const call = (id, name, args) => ({ id, type: "function", function: { name, arguments: JSON.stringify(args) } });
const turn = (...calls) => ({ role: "assistant", content: null, tool_calls: calls });
function gitRepo(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stall-repo-"));
  const g = (...a) => execFileSync("git", ["-C", dir, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  for (const [f, c] of Object.entries(files)) fs.writeFileSync(path.join(dir, f), c);
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return dir;
}
const run = (p, repo, extra = {}) => runAgent({ task: "fix it", base_url: p.url, model_id: "m", ...extra }, { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: async () => {} });

test("agent: three failing edits in a row end the run at step 3 with the stalled rule; the provider is not asked again", async () => {
  const repo = gitRepo({ "a.py": "x = 1\n" });
  const p = await provider((n) => turn(call(`c${n}`, "edit_file", { path: "a.py", old_text: "NOPE", new_text: "y" })));
  try {
    const out = await run(p, repo);
    assert.equal(out.stop, "stalled");
    assert.equal(out.stalled.rule, "c");
    assert.equal(out.steps, 3);
    assert.equal(p.seen.length, 3, "three model calls, not thirty");
    assert.match(out.message, /^stopped early \(stalled, rule c\)/);
  } finally { p.close(); }
});

test("agent: undoing its own edit (real files, real hashes) is rule b", async () => {
  const repo = gitRepo({ "a.py": "x = 1\n" });
  const steps = [
    turn(call("1", "edit_file", { path: "a.py", old_text: "x = 1", new_text: "x = 2" })),
    turn(call("2", "edit_file", { path: "a.py", old_text: "x = 2", new_text: "x = 1" })),            // back to the original
    turn(call("3", "finish", { summary: "done" })),
  ];
  const p = await provider((n) => steps[n - 1]);
  try {
    const out = await run(p, repo);
    assert.equal(out.stop, "stalled");
    assert.equal(out.stalled.rule, "b");
    assert.equal(out.steps, 2);
  } finally { p.close(); }
});

test("agent: a normal run (explore, edit, check, finish) is never stopped", async () => {
  const repo = gitRepo({ "a.py": "x = 1\n", "b.py": "y = 1\n" });
  const steps = [
    turn(call("1", "list_files", {})), turn(call("2", "read_file", { path: "a.py" })), turn(call("3", "search", { query: "x" })),
    turn(call("4", "read_file", { path: "b.py" })), turn(call("5", "list_files", { path: "." })), turn(call("6", "read_file", { path: "a.py", start_line: 1 })),
    turn(call("7", "read_file", { path: "b.py", start_line: 1 })),                                      // seven reads before the first edit
    turn(call("8", "edit_file", { path: "a.py", old_text: "x = 1", new_text: "x = 2" })),
    turn(call("9", "run_shell", { command: "git diff --stat" })),
    turn(call("10", "finish", { summary: "done" })),
  ];
  const p = await provider((n) => steps[n - 1]);
  try {
    const out = await run(p, repo);
    assert.equal(out.stop, "finished");
    assert.equal(out.stalled, undefined);
  } finally { p.close(); }
});

test("agent: early_stop=false is the old behaviour (the same three failing edits run on)", async () => {
  const repo = gitRepo({ "a.py": "x = 1\n" });
  const steps = [1, 2, 3, 4].map((n) => turn(call(`c${n}`, "edit_file", { path: "a.py", old_text: "NOPE", new_text: "y" })));
  steps.push(turn(call("f", "finish", { summary: "gave up" })));
  const p = await provider((n) => steps[n - 1]);
  try {
    const out = await run(p, repo, { early_stop: false });
    assert.equal(out.stop, "finished");
    assert.equal(out.steps, 5);
  } finally { p.close(); }
});

test("worktreeFingerprint: changes with an edit and with a new file, is stable otherwise, null outside git", () => {
  const repo = gitRepo({ "a.py": "x = 1\n" });
  const f0 = worktreeFingerprint(repo);
  assert.equal(worktreeFingerprint(repo), f0);
  fs.writeFileSync(path.join(repo, "a.py"), "x = 2\n");
  const f1 = worktreeFingerprint(repo);
  assert.notEqual(f1, f0);
  fs.writeFileSync(path.join(repo, "new.py"), "n\n");
  assert.notEqual(worktreeFingerprint(repo), f1);
  assert.equal(worktreeFingerprint(fs.mkdtempSync(path.join(os.tmpdir(), "nogit-"))), null);
});

// ---- the executor and the runtime ----------------------------------------------------------------------------------------------------

const profile = () => normaliseProfile("m", { key_env: "OPEN_KEY", openai_base_url: "https://api.example.com/v1", model_id: "m" });

test("the stealth executor passes the switch and thresholds to the agent and reads a stalled final line", () => {
  const spec = stealth.buildCommand({ task: "t", model: "m", worktree: ".", timeoutS: 10, env: { OPEN_KEY: KEY, PATH: process.env.PATH }, profile: profile() });
  assert.equal(JSON.parse(spec.stdinText).early_stop, true);
  const off = stealth.buildCommand({ task: "t", model: "m", worktree: ".", timeoutS: 10, profile: profile(),
    env: { OPEN_KEY: KEY, PATH: process.env.PATH, STEALTHLAB_EARLY_STOP: "off", STEALTHLAB_EARLY_STOP_STEPS: "4", STEALTHLAB_EARLY_STOP_ERRORS: "2" } });
  assert.deepEqual([JSON.parse(off.stdinText).early_stop, JSON.parse(off.stdinText).early_stop_steps, JSON.parse(off.stdinText).early_stop_errors], [false, 4, 2]);
  const parsed = stealth.parseOutput({ stdout: `step 1\n${JSON.stringify({ type: "final", message: "stopped early", tokens: { in: 5, out: 1 }, costUsd: 0.001, stalled: { rule: "c", why: "3 tool calls in a row failed" } })}\n` });
  assert.deepEqual(parsed.stalled, { rule: "c", why: "3 tool calls in a row failed" });
  assert.equal(stealth.parseOutput({ stdout: JSON.stringify({ type: "final", message: "ok" }) }).stalled, undefined);
});

test("runtime: a stalled attempt is FAILED with the rule, no check is run, the edit that would have passed is not accepted", async () => {
  const repo = makeRepo();
  const env = testEnv();
  const base = stubAdapter({ scenario: { edits: [{ path: "calc.txt", content: "fixed\n" }], finalMessage: "stopped early" } });
  const adapter = { ...base, parseOutput: (o) => ({ ...base.parseOutput(o), stalled: { rule: "d", why: "6 tool calls in a row without any change to the worktree" } }) };
  const rt = new ExecRuntime({ env, adapters: { stub: adapter } });
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1", goal_id: "g-1" });
  const res = await rt.runResult(run_id, 30);
  assert.equal(res.state, "failed");
  assert.equal(res.verified, false);
  assert.deepEqual(res.checks, [], "no check ran: it was stopped for going round in circles");
  assert.deepEqual(res.stalled, { rule: "d", why: "6 tool calls in a row without any change to the worktree" });
  assert.match(res.error, /stalled \(rule d\)/);
  assert.equal(res.evidence.queued, true, "it is reported like any failed attempt: counted against its model");
  await rt.cancelRun(run_id);
});

// ---- the offline replay over saved runs ---------------------------------------------------------------------------------------------------

import { replayDir, replayLog } from "../scripts/replay_early_stop.mjs";

const logOf = (...lines) => lines.map((l, i) => `step ${i + 1}: asking m\n${l}`).join("\n");

test("replay: rule c from three (error) lines; rule d from six read-only calls after the first edit; a run_shell resets it", () => {
  const c = replayLog(logOf("step 1: edit_file a.py (error)", "step 2: edit_file a.py (error)", "step 3: edit_file a.py (error)", "step 4: finish"));
  assert.deepEqual(c, { rule: "c", at: 3, of: 4 });
  const reads = Array.from({ length: 6 }, (_, i) => `step ${i + 2}: read_file a.py`);
  assert.equal(replayLog(logOf("step 1: edit_file a.py", ...reads)).rule, "d");
  assert.equal(replayLog(logOf("step 1: edit_file a.py", ...reads.slice(0, 5), "step 7: run_shell", "step 8: read_file a.py")).rule, null);
  assert.equal(replayLog(logOf(...reads.map((l) => l.replace(/step \d+/, "step 1")))).rule, null, "reading before any edit is never a stall");
  assert.equal(replayLog("").rule, null);
});

test("replay over a runs directory: failing attempts stopped and seconds saved, and the passed attempts that would have been stopped (must be 0)", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "replay-"));
  const add = (id, state, duration, log) => {
    fs.mkdirSync(path.join(dir, id), { recursive: true });
    fs.writeFileSync(path.join(dir, id, "run.json"), JSON.stringify({ attempts: [{ state, duration_s: duration }] }));
    fs.writeFileSync(path.join(dir, id, "a1.stdout.log"), log);
  };
  add("r-1", "failed", 100, logOf("step 1: edit_file a.py (error)", "step 2: edit_file a.py (error)", "step 3: edit_file a.py (error)", "step 4: read_file a.py", "step 5: finish"));
  add("r-2", "verified", 50, logOf("step 1: edit_file a.py", "step 2: finish"));
  add("r-3", "failed", 40, logOf("step 1: read_file a.py", "step 2: finish"));
  const r = replayDir(dir);
  assert.deepEqual({ runs: r.runs, passed: r.passed, failed: r.failed, stoppedFailed: r.stoppedFailed, stoppedPassed: r.stoppedPassed },
    { runs: 3, passed: 1, failed: 2, stoppedFailed: 1, stoppedPassed: 0 });
  assert.equal(r.secondsSaved, 40, "100 s x (1 - 3/5)");
});

test("edit_file on a Windows (CRLF) file: a multi-line old_text copied with \n still matches, and the file keeps CRLF", async () => {
  const { runTool } = await import("../lib/agent/step_agent.mjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "crlf-"));
  fs.writeFileSync(path.join(dir, "a.js"), "function f() {\r\n  return 1;\r\n}\r\n");
  assert.equal(await runTool("edit_file", { path: "a.js", old_text: "function f() {\n  return 1;", new_text: "function f() {\n  return 2;" }, { cwd: dir, env: {} }), "edited a.js");
  assert.equal(fs.readFileSync(path.join(dir, "a.js"), "utf8"), "function f() {\r\n  return 2;\r\n}\r\n");
});

test("rule d: reading new parts of the code after a first edit is not a stall; repeats are, and endless browsing stops at 3x", () => {
  const d = new StallDetector({ noChangeSteps: 6 });
  d.start("fp0");
  d.observe({ name: "write_file", args: { path: "repro.py" }, result: "ok", hash: "h1", fingerprint: "fp1" });   // a scratch script first
  for (let i = 0; i < 10; i++) assert.equal(d.observe({ name: "read_file", args: { path: "pkg/core.py", start_line: 1 + 200 * i } , result: "x" }), null, "new ranges");
  for (let i = 0; i < 5; i++) assert.equal(d.observe({ name: "read_file", args: { path: "pkg/core.py", start_line: 1 }, result: "x" }), null, "5 repeats: under 6");
  assert.equal(d.observe({ name: "search", args: { query: "a" }, result: "x" }), null, "16 calls");
  assert.equal(d.observe({ name: "search", args: { query: "b" }, result: "x" }), null, "17 calls");
  assert.equal(d.observe({ name: "search", args: { query: "c" }, result: "x" })?.rule, "d", "18 calls without a change: stopped");
  const r = new StallDetector({ noChangeSteps: 6 });
  r.start("fp0");
  r.observe({ name: "write_file", args: { path: "a.py" }, result: "ok", hash: "h1", fingerprint: "fp1" });
  for (let i = 0; i < 6; i++) r.observe({ name: "read_file", args: { path: "a.py" }, result: "x" });   // the first read is new
  assert.equal(r.observe({ name: "read_file", args: { path: "a.py" }, result: "x" })?.rule, "d", "the same read over and over: stuck");
});

test("rule d: testing an edit with new commands or new results is not a stall; the same command and result again is", () => {
  const d = new StallDetector({ noChangeSteps: 6 });
  d.start("fp0");
  d.observe({ name: "edit_file", args: { path: "a.py" }, result: "ok", hash: "h1", fingerprint: "fp1" });
  for (let i = 0; i < 8; i++) {
    assert.equal(d.observe({ name: "run_shell", args: { command: `python -c "probe ${i}"` }, result: `[exit 1]\nout ${i}`, fingerprint: "fp1" }), null);
  }
  const s = new StallDetector({ noChangeSteps: 6 });
  s.start("fp0");
  s.observe({ name: "edit_file", args: { path: "a.py" }, result: "ok", hash: "h1", fingerprint: "fp1" });
  let r = null;
  for (let i = 0; i < 7 && !r; i++) r = s.observe({ name: "run_shell", args: { command: "pytest -q" }, result: "[exit 1]\n1 failed", fingerprint: "fp1" });
  assert.equal(r?.rule, "d", "the same command, the same result, over and over");
});

test("rule e: before the first change only repeats count (24), with a ceiling of 144 calls; a change leaves it to rule d", () => {
  const d = new StallDetector();
  d.start("fp0");
  let r = null;
  for (let i = 0; i < 100 && !r; i++) r = d.observe({ name: "search", args: { query: `q${i}` }, result: "hits" });
  assert.equal(r, null, "100 searches for new things: still exploring, not stalled (GLM's first edit came at call 69)");
  for (let i = 100; i < 144 && !r; i++) r = d.observe({ name: "search", args: { query: `q${i}` }, result: "hits" });
  assert.equal(r?.rule, "e", "the ceiling");
  const rep = new StallDetector();
  rep.start("fp0");
  let k = 0;
  for (r = null; !r && k < 100; k++) r = rep.observe({ name: "read_file", args: { path: "a.py" }, result: "same" });
  assert.equal(r?.rule, "e");
  assert.equal(k, 25, "the first read is new; 24 repeats of it stop the run");
  const e = new StallDetector();
  e.start("fp0");
  assert.equal(e.observe({ name: "run_shell", args: { command: "touch x" }, result: "[exit 0]", fingerprint: "fp1" }), null);
  for (let i = 0; i < 17; i++) assert.equal(e.observe({ name: "read_file", args: { path: `f${i}.py` }, result: "x" }), null);
  const reads = Array.from({ length: 48 }, (_, i) => `step ${i + 1}: search a.py`);
  assert.equal(replayLog(logOf(...reads)).rule, "e");
  assert.equal(replayLog(logOf("step 1: run_shell", ...reads)).rule, null, "a shell command could have changed the worktree");
});

// ---- the root-cause method, the history tool and the transcript --------------------------------------------------------------

test("agent: with the root-cause method a finish with no change to the project's files is sent back, then accepted on the 3rd try", async () => {
  const repo = gitRepo({ "a.py": "x = 1\n" });
  const steps = [
    turn(call("1", "write_file", { path: "repro.py", content: "print(1)\n" })),        // a new script: not a project change
    turn(call("2", "finish", { summary: "done" })),
    turn(call("3", "edit_file", { path: "a.py", old_text: "x = 1", new_text: "x = 2" })),
    turn(call("4", "finish", { summary: "fixed" })),
  ];
  const p = await provider((n) => steps[n - 1]);
  const tr = path.join(repo, "..", `${path.basename(repo)}.transcript.jsonl`);
  try {
    const out = await run(p, repo, { method: "root_cause", transcript: tr });
    assert.equal(out.stop, "finished");
    assert.equal(out.message, "fixed");
    const lines = fs.readFileSync(tr, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    assert.equal(lines.find((l) => l.step === 2).result.startsWith("not finished"), true);
    assert.deepEqual(lines.map((l) => l.tool), ["write_file", "finish", "edit_file", "finish"]);
    assert.match(String(p.seen[0].messages[0].content), /Find the cause, not the symptom/);
  } finally { p.close(); }
  // without the method the same first finish ends the run, as before
  const repo2 = gitRepo({ "a.py": "x = 1\n" });
  const p2 = await provider((n) => steps[n - 1]);
  try { assert.equal((await run(p2, repo2)).steps, 2); } finally { p2.close(); }
});

test("agent: the history tool is offered only when asked for, and answers from the repository's git history", async () => {
  const repo = gitRepo({ "a.py": "def f():\n    return 1\n" });
  fs.writeFileSync(path.join(repo, "a.py"), "def f():\n    return 2\n");
  execFileSync("git", ["-C", repo, "commit", "-qam", "Fix f returning the wrong value"]);
  const steps = [turn(call("1", "history", { path: "a.py", function: "f" })), turn(call("2", "finish", { summary: "looked" }))];
  const p = await provider((n) => steps[n - 1]);
  try {
    await run(p, repo, { history_tool: true });
    assert.ok(p.seen[0].tools.some((t) => t.function.name === "history"));
    const toolMsg = p.seen[1].messages.find((m) => m.role === "tool");
    assert.match(toolMsg.content, /Fix f returning the wrong value/);
  } finally { p.close(); }
  const p2 = await provider(() => turn(call("1", "finish", { summary: "x" })));
  try { await run(p2, repo); assert.ok(!p2.seen[0].tools.some((t) => t.function.name === "history")); } finally { p2.close(); }
});

test("search: a grep-style pattern written without regex=true is retried as a regex when the plain text finds nothing", async () => {
  const repo = gitRepo({ "a.py": "def adapt_typehints():\n    pass\n", "b.py": "def discard_init_args():\n    pass\n" });
  const out = await runTool("search", { query: "discard_init_args|adapt_typehints" }, { cwd: repo, env: {} });
  assert.match(out, /^\(no matches as plain text; as a regex:\)/);
  assert.match(out, /a\.py:1/);
  assert.match(out, /b\.py:1/);
  assert.equal(await runTool("search", { query: "nothing|here" }, { cwd: repo, env: {} }), "(no matches, as text or as a regex)");
  assert.equal(await runTool("search", { query: "plain words" }, { cwd: repo, env: {} }), "(no matches)");
});

test("agent: an account out of credit fails at once (no retries) and reads as a provider limit, not a verdict on the model", async () => {
  const { PROVIDER_LIMIT } = await import("../lib/exec/runtime.mjs");
  let calls = 0;
  const fetchImpl = async () => { calls++; return { status: 429, headers: { get: () => null },
    text: async () => '{"error":{"code":"1113","message":"Insufficient balance or no resource package. Please recharge."}}' }; };
  const repo = gitRepo({ "a.py": "x = 1\n" });
  await assert.rejects(runAgent({ task: "fix it", base_url: "http://x/v1", model_id: "m" },
                                { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, fetchImpl, sleep: async () => {} }),
    (err) => PROVIDER_LIMIT.test(err.message) && /out of credit/.test(err.message));
  assert.equal(calls, 1, "one request, no retries");
});

test("rule e also counts what the repeats cost: uncached input tokens on calls that brought nothing new stop a run that only reads", () => {
  // gemma on kell-try (2026-10-29): 64 calls and 1.07M input tokens without an edit, looking for something that was not there
  const read = { name: "read_file", args: { path: "core.py" }, result: "1| x = 1" };
  const d = new StallDetector({ wasteTokens: 100_000 });
  assert.equal(d.observe({ ...read, stepTokens: 60_000 }), null, "the first read is new information: free");
  assert.equal(d.observe({ ...read, stepTokens: 60_000 }), null, "60k wasted");
  assert.match(d.observe({ ...read, stepTokens: 60_000 }).why, /120k uncached input tokens went to calls that brought nothing new/);
  const cheap = new StallDetector({ wasteTokens: 100_000 });
  for (let i = 0; i < 10; i++) assert.equal(cheap.observe({ ...read, stepTokens: 2_000 }), null, "cheap repeats early on stay under the budget");
  const off = new StallDetector({ wasteTokens: 0 });
  for (let i = 0; i < 10; i++) assert.equal(off.observe({ ...read, stepTokens: 60_000 }), null);
  const fresh = new StallDetector({ wasteTokens: 100_000 });
  for (let i = 0; i < 10; i++) assert.equal(fresh.observe({ name: "read_file", args: { path: `f${i}.py` }, result: "x", stepTokens: 60_000 }), null,
    "reading new code costs nothing against the budget, however big the context");
});

test("after older outputs are elided, reading the same file again is new information, not a repeat", () => {
  const read = { name: "read_file", args: { path: "core.py" }, result: "1| x = 1" };
  const d = new StallDetector({ noChangeSteps: 1, wasteTokens: 0 });          // e: 4 repeats before a change
  d.observe(read); d.observe(read); d.observe(read); d.observe(read);
  assert.equal(d.observe(read).rule, "e", "the fourth repeat stops it");
  const kept = new StallDetector({ noChangeSteps: 1, wasteTokens: 0 });
  kept.observe(read); kept.observe(read); kept.observe(read);
  kept.forget();
  assert.equal(kept.observe(read), null, "after forget(), the read is fresh");
});

test("agent, read-only: only the reading tools are offered, a write it asks for anyway is refused, and finish is the answer", async () => {
  const repo = gitRepo({ "a.py": "def f():\n    return 1\n" });
  const p = await provider((n) => n === 1 ? turn(call("c1", "write_file", { path: "a.py", content: "x" }))
    : n === 2 ? turn(call("c2", "read_file", { path: "a.py" }))
    : turn(call("c3", "finish", { summary: "f() in a.py:1 returns 1." })));
  try {
    const r = await run(p, repo, { read_only: true, task: "what does f return?" });
    assert.equal(r.message, "f() in a.py:1 returns 1.");
    assert.deepEqual(p.seen[0].tools.map((t) => t.function.name).sort(), ["finish", "list_files", "read_file", "search"]);
    assert.match(JSON.stringify(p.seen[1].messages), /write_file is not available: this run can only read/);
    assert.equal(fs.readFileSync(path.join(repo, "a.py"), "utf8"), "def f():\n    return 1\n", "the checkout is untouched");
    assert.match(p.seen[0].messages[0].content, /You answer a developer's question/);
  } finally { p.close(); }
});

test("agent: an empty reply is not an answer; it is asked to go on", async () => {
  const repo = gitRepo({ "a.py": "x = 1\n" });
  const p = await provider((n) => n === 1 ? { role: "assistant", content: "" } : turn(call("c1", "finish", { summary: "done" })));
  try {
    const r = await run(p, repo, { read_only: true });
    assert.equal(r.message, "done");
    assert.match(JSON.stringify(p.seen[1].messages), /Your last reply was empty/);
  } finally { p.close(); }
});

test("stripControl: a model's control tokens are taken out of its text; ordinary text is left as it is", async () => {
  const { stripControl } = await import("../lib/agent/step_agent.mjs");
  assert.equal(stripControl("<|channel>thought\n<channel|>The task has no referent."), "The task has no referent.");
  assert.equal(stripControl("a|b and <b>bold</b>"), "a|b and <b>bold</b>");
});
