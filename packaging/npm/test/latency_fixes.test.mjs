import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch, namedImages, specFailures, triedNote, writeSpecTests } from "../lib/dispatch.mjs";
import { preloadNamedFiles } from "../lib/agent/step_agent.mjs";

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "minimax-m2-7|stealth", "gpt-oss-120b|stealth"] };
const MINE = "claude-opus-5-5|claude-code";

function setup() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "lat-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7", "gpt-oss-120b"] } } }));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "lat-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                  STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off" }, repo };
}
const failTail = (t) => `FAILED ../specs/x/test_spec.py::${t}
1 failed`;
function stub(repo, outcomes) {
  const calls = [];
  return { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) { const m = calls[Number(id.slice(2)) - 1].model; const [state, tail] = outcomes[m];
      return { run_id: id, state, cost_usd: 0.001, checks: [{ tail }] }; },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun() {} };
}

test("specFailures: the advance tests a failed run failed, or null", () => {
  assert.equal(specFailures({ state: "failed", checks: [{ tail: failTail("test_b") + "\n" + failTail("test_a") }] }), "test_a,test_b");
  assert.equal(specFailures({ state: "failed", checks: [{ tail: "AssertionError in check.test.js" }] }), null);
  assert.equal(specFailures({ state: "verified", checks: [{ tail: failTail("test_a") }] }), null);
});

test("two cheap answers failing the same advance test end the cheap tries; the session is told the test may be wrong", async () => {
  const { env, repo } = setup();
  const rt = stub(repo, { "gemma-4-31b-it": ["failed", failTail("test_digits")], "minimax-m2-7": ["failed", failTail("test_digits")],
                          "gpt-oss-120b": ["verified", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt });
  assert.equal(d.handled, false);
  assert.equal(d.sharedFail, "test_digits");
  assert.deepEqual(rt.calls.map((c) => c.model), ["gemma-4-31b-it", "minimax-m2-7"], "the third model is not started");
  assert.match(triedNote(d), /may\s+themselves be wrong/);
  const off = stub(repo, { "gemma-4-31b-it": ["failed", failTail("test_digits")], "minimax-m2-7": ["failed", failTail("test_digits")],
                           "gpt-oss-120b": ["verified", ""] });
  const d2 = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_SHARED_FAIL_STOP: "off" }, runtime: off });
  assert.equal(d2.handled, true, "switch off: the third model still runs and wins");
});

test("different failures do not stop the tries", async () => {
  const { env, repo } = setup();
  const rt = stub(repo, { "gemma-4-31b-it": ["failed", failTail("test_a")], "minimax-m2-7": ["failed", failTail("test_b")],
                          "gpt-oss-120b": ["verified", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt });
  assert.equal(d.handled, true);
});

test("a task built on an image in the project is not dispatched (text-only cheap models), unless vision is on", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(repo, "screenshot.png"), "\x89PNG");
  assert.deepEqual(namedImages("Recreate the page in screenshot.png as index.html; see logo.png", repo), ["screenshot.png"]);
  const rt = stub(repo, { "gemma-4-31b-it": ["verified", ""] });
  const prompt = "Recreate the web page shown in screenshot.png as index.html";
  assert.equal(await dispatch({ payload: { cwd: repo, prompt }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt }), null);
  assert.equal(rt.calls.length, 0);
  const d = await dispatch({ payload: { cwd: repo, prompt }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_VISION: "on", STEALTHLAB_DISPATCH_VISUAL_CHECK: "off" }, runtime: rt });
  assert.equal(d.handled, true);
});

test("the cheap agent starts with the files the task names already read", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pre-"));
  fs.writeFileSync(path.join(dir, "solution.js"), "function f() {}\n");
  fs.writeFileSync(path.join(dir, "TASK.md"), "the full task\n");
  const out = preloadNamedFiles("Implement f in solution.js (details in TASK.md); check with `node --test check.test.js`; ../x.txt", dir);
  assert.match(out, /--- solution\.js ---\n1\tfunction f\(\) \{\}/);
  assert.match(out, /--- TASK\.md ---/);
  assert.doesNotMatch(out, /check\.test\.js ---|x\.txt/);
  assert.equal(preloadNamedFiles("no files here", dir), "");
});

test("an image task goes only to the cheap models whose profile can see (vision: true)", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "glm-4.6v"] } },
    profiles: { "glm-4.6v": { openai_base_url: "https://example.test/v1", key_env: "K", vision: true } } }));
  fs.writeFileSync(path.join(repo, "screenshot.png"), "\x89PNG");
  const plan = { ...PLAN, ladder: ["gemma-4-31b-it|stealth", "glm-4.6v|stealth"] };
  const logs = [];
  const rt = stub(repo, { "gemma-4-31b-it": ["verified", ""], "glm-4.6v": ["verified", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Recreate the web page shown in screenshot.png as index.html" },
    reply: { model_plan: plan }, root: repo, mine: MINE, env: { ...env, K: "k", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_VISUAL_CHECK: "off" }, runtime: rt, log: (m) => logs.push(m) });
  assert.ok(d?.handled, logs.join(" | "));
  assert.deepEqual(rt.calls.map((c) => c.model), ["glm-4.6v"], logs.join(" | "));
});

test("a model that can see gets the named images as image parts", async () => {
  const { namedImageParts } = await import("../lib/agent/step_agent.mjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "img-"));
  fs.writeFileSync(path.join(dir, "screenshot.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  const parts = namedImageParts("Recreate the page in screenshot.png; ignore missing.jpg", dir);
  assert.equal(parts.length, 1);
  assert.equal(parts[0].type, "image_url");
  assert.match(parts[0].image_url.url, /^data:image\/png;base64,iVBORw==$/);
});

test("a cancelled run that never answers does not hold back a finished answer", async () => {
  const { env, repo } = setup();
  const calls = [];
  const rt = { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const m = calls[Number(id.slice(2)) - 1].model;
      if (m === "minimax-m2-7") return new Promise(() => {});                 // stuck in a model call, forever
      return { run_id: id, state: "verified", cost_usd: 0.001, checks: [{ tail: "" }] };
    },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun() { return new Promise(() => {}); } };                     // cancelling hangs too
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: { ...PLAN, ladder: ["minimax-m2-7|stealth", "gemma-4-31b-it|stealth"] } },
    root: repo, mine: MINE, env: { ...env, STEALTHLAB_DISPATCH_PARALLEL: "on", STEALTHLAB_DISPATCH_FASTEST_FIRST: "off" }, runtime: rt });
  assert.equal(d.handled, true);
  assert.equal(d.unit, "gemma-4-31b-it|stealth");
  assert.ok(Date.now() - t0 < 20000, `delivered after ${Date.now() - t0} ms`);
  assert.ok(d.tried.some((t) => t.unit === "minimax-m2-7|stealth" && t.state === "cancelled"));
});

test("an image task finds the model that can see even when it is not among the first three candidates", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7", "gpt-oss-120b", "glm-5.3-flash"] } },
    profiles: { "glm-5.3-flash": { openai_base_url: "https://example.test/v1", key_env: "K", vision: true } } }));
  fs.writeFileSync(path.join(repo, "screenshot.png"), "\x89PNG");
  const rt = stub(repo, { "glm-5.3-flash": ["verified", ""], "gemma-4-31b-it": ["verified", ""], "minimax-m2-7": ["verified", ""], "gpt-oss-120b": ["verified", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Recreate the web page shown in screenshot.png as index.html" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env: { ...env, K: "k", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_VISUAL_CHECK: "off" }, runtime: rt });
  assert.equal(d?.handled, true);
  assert.deepEqual(rt.calls.map((c) => c.model), ["glm-5.3-flash"]);
});

test("a large task goes only to the strong cheap model (profile strong: true), first in line; without one, to the session's model", async () => {
  const { env, repo } = setup();
  const exec = { executors: { stealth: { models: ["gemma-4-31b-it", "glm-5.3-flash"] } },
    profiles: { "glm-5.3-flash": { openai_base_url: "https://example.test/v1", key_env: "K", strong: true } } };
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify(exec));
  const large = async () => ({ task: "build the whole API", files_hinted: [], acceptance: "", parts: [], clarity: "clear", kind: "code", size: "large" });
  const rt = stub(repo, { "glm-5.3-flash": ["verified", ""], "gemma-4-31b-it": ["verified", ""] });
  const e = { ...env, K: "k", STEALTHLAB_BRIEF: "on", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0" };
  const d = await dispatch({ payload: { cwd: repo, prompt: "implement every endpoint of the contract" }, reply: { model_plan: { ...PLAN, ladder: ["gemma-4-31b-it|stealth", "glm-5.3-flash|stealth"] } },
    root: repo, mine: MINE, env: e, runtime: rt, briefImpl: large });
  assert.equal(d?.handled, true);
  assert.deepEqual(rt.calls.map((c) => c.model), ["glm-5.3-flash"]);
  delete exec.profiles["glm-5.3-flash"].strong;
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify(exec));
  assert.equal(await dispatch({ payload: { cwd: repo, prompt: "implement every endpoint of the contract" }, reply: { model_plan: PLAN },
    root: repo, mine: MINE, env: e, runtime: stub(repo, {}), briefImpl: large }), null);
});

test("the strong cheap model is the last cheap try by default (never cut by max rungs); STRONG_FIRST=on puts it first", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7", "gpt-oss-120b", "glm-5.3"] } },
    profiles: { "glm-5.3": { openai_base_url: "https://example.test/v1", key_env: "K", strong: true } } }));
  const fail = { "gemma-4-31b-it": ["failed", "x"], "minimax-m2-7": ["failed", "y"], "gpt-oss-120b": ["failed", "z"], "glm-5.3": ["verified", ""] };
  const e = { ...env, K: "k", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_FASTEST_FIRST: "off" };
  const rt = stub(repo, fail);
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env: e, runtime: rt });
  assert.equal(d.unit, "glm-5.3|stealth");
  assert.equal(rt.calls.at(-1).model, "glm-5.3", "the strong one runs after the small ones");
  assert.equal(rt.calls.length, 4, "three small ones plus the strong one: not cut by max rungs (3)");
  const rt2 = stub(repo, fail);
  await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env: { ...e, STEALTHLAB_DISPATCH_STRONG_FIRST: "on" }, runtime: rt2 });
  assert.equal(rt2.calls[0].model, "glm-5.3");
});

test("a strong profile with first: true starts in the first wave; two shared failures still leave the strong model its try", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7", "gpt-oss-120b", "glm-5.3-flash"] } },
    profiles: { "glm-5.3-flash": { openai_base_url: "https://example.test/v1", key_env: "K", strong: true, first: true } } }));
  const e = { ...env, K: "k", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_FASTEST_FIRST: "off" };
  const rt = stub(repo, { "glm-5.3-flash": ["verified", ""], "gemma-4-31b-it": ["failed", "x"], "minimax-m2-7": ["failed", "y"], "gpt-oss-120b": ["failed", "z"] });
  await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env: e, runtime: rt });
  assert.equal(rt.calls[0].model, "glm-5.3-flash", "first: true -> first in line");

  // strong last (no first:) and the two small ones fail the same advance test: the third small one is skipped, the strong one runs and wins
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "minimax-m2-7", "gpt-oss-120b", "glm-5.3-flash"] } },
    profiles: { "glm-5.3-flash": { openai_base_url: "https://example.test/v1", key_env: "K", strong: true } } }));
  const rt2 = stub(repo, { "gemma-4-31b-it": ["failed", failTail("test_digits")], "minimax-m2-7": ["failed", failTail("test_digits")],
                           "gpt-oss-120b": ["verified", ""], "glm-5.3-flash": ["verified", ""] });
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env: e, runtime: rt2 });
  assert.equal(d.unit, "glm-5.3-flash|stealth");
  assert.deepEqual(rt2.calls.map((c) => c.model), ["gemma-4-31b-it", "minimax-m2-7", "glm-5.3-flash"], "gpt-oss skipped, the strong one tried");

  // the strong one fails the same test too: now the stop applies
  const rt3 = stub(repo, { "gemma-4-31b-it": ["failed", failTail("test_digits")], "minimax-m2-7": ["failed", failTail("test_digits")],
                           "gpt-oss-120b": ["verified", ""], "glm-5.3-flash": ["failed", failTail("test_digits")] });
  const d3 = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env: e, runtime: rt3 });
  assert.equal(d3.handled, false);
  assert.equal(d3.sharedFail, "test_digits");
});

test("the cross-check counts against the cheap tries' time limit: a hung verifier does not hold the task", async () => {
  const { env, repo } = setup();
  const calls = [], cancelled = [];
  const rt = { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      if (id === "r-1") return { run_id: id, state: "verified", cost_usd: 0.001, checks: [{ tail: "" }], diff: { worktree: repo, files: ["calc.py"] } };
      return new Promise(() => {});                        // the verifier never finishes
    },
    async applyRun() { return { files: ["calc.py"] }; },
    async cancelRun(id) { cancelled.push(id); } };
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: PLAN }, root: repo, mine: MINE,
    env: { ...env, STEALTHLAB_DISPATCH_CROSSCHECK: "strict", STEALTHLAB_DISPATCH_TIMEOUT_S: "10", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0",
           STEALTHLAB_DISPATCH_FASTEST_FIRST: "off", STEALTHLAB_DISPATCH_MODEL_CAP: "off" }, runtime: rt });
  assert.equal(d.handled, false);
  assert.ok(Date.now() - t0 < 40000, "ended near the limit, not when the verifier would have finished");
  assert.ok(calls.length >= 2, "a cross-check run was started");
  assert.ok(cancelled.includes("r-2"), "the hung cross-check run is cancelled");
  assert.ok(d.tried.some((t) => /timed out while being checked/.test(t.state)));
});

test("test-first on an open model: a late or failed reply -> the fallback Claude model writes the tests", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } },
    profiles: { "glm-5.3": { openai_base_url: "https://example.test/v1", key_env: "K", model_id: "glm-5.3" } } }));
  fs.writeFileSync(path.join(repo, "solution.py"), "def f(x):\n    pass\n");
  const asked = [];
  const runClaude = (args) => { asked.push(args[args.indexOf("--model") + 1]);
    return asked.length === 1 ? { status: 1, stdout: "", stderr: "The operation was aborted due to timeout" }
      : { stdout: JSON.stringify({ result: "```python\nfrom solution import f\n\ndef test_neg():\n    assert f(-1) == -2\n```", total_cost_usd: 0.01 }) }; };
  const e = { ...env, K: "k", STEALTHLAB_DISPATCH_TESTFIRST_MODEL: "glm-5.3" };
  const spec = await writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env: e, runClaude });
  assert.ok(spec?.file, "tests written by the fallback");
  assert.deepEqual(asked, ["glm-5.3", "claude-sonnet-5-5"]);
  assert.match(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "dispatch_costs.jsonl"), "utf8"), /"model":"claude-sonnet-5-5"/);
  asked.length = 0;
  assert.equal(await writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env: { ...e, STEALTHLAB_DISPATCH_TESTFIRST_FALLBACK: "off" },
    runClaude: (args) => { asked.push(args[args.indexOf("--model") + 1]); return { status: 1, stdout: "" }; } }), null);
  assert.deepEqual(asked, ["glm-5.3", "glm-5.3"], "fallback off: the open model twice, as before");
});

test("test-first: '# AMBIGUOUS: none' is not an ambiguity flag; a real one is", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(repo, "solution.py"), "def f(x):\n    pass\n");
  const reply = (first) => () => ({ stdout: JSON.stringify({ result: "```python\n" + first + "\nfrom solution import f\n\ndef test_neg():\n    assert f(-1) == -2\n```", total_cost_usd: 0.01 }) });
  for (const line of ["# AMBIGUOUS: none", "# AMBIGUOUS: None.", "# AMBIGUOUS: (none)", "# AMBIGUOUS: N/A"]) {
    const spec = await writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env, runClaude: reply(line) });
    assert.equal(spec.ambiguous, undefined, line);
  }
  const real = await writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env, runClaude: reply("# AMBIGUOUS: double could mean x*2 or x+x as strings") });
  assert.match(real.ambiguous, /x\*2 or x\+x/);
});

test("the cheap model's task carries the likely place; an area where cheap models lost 3 times is not dispatched", async () => {
  const { env, repo } = setup();
  const { recordArea } = await import("../lib/areas.mjs");
  const e = { ...env, STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_EXPLORE: "0" };
  const rt = stub(repo, { "gemma-4-31b-it": ["verified", ""], "minimax-m2-7": ["verified", ""], "gpt-oss-120b": ["verified", ""] });
  await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env: e, runtime: rt });
  assert.match(rt.calls[0].task, /Likely place in this repository[^\n]*calc\.py:\d+-\d+/);
  const fresh = setup().repo;                          // the first dispatch won in calc.py there: a win keeps an area open
  for (let i = 0; i < 3; i++) recordArea(fresh, "calc.py", false);
  const rt2 = stub(fresh, { "gemma-4-31b-it": ["verified", ""] });
  assert.equal(await dispatch({ payload: { cwd: fresh, prompt: "Fix calc.py so x is 3" }, reply: { model_plan: PLAN }, root: fresh, mine: MINE, env: e, runtime: rt2 }), null);
  assert.equal(rt2.calls.length, 0);
  const rt3 = stub(fresh, { "gemma-4-31b-it": ["verified", ""], "minimax-m2-7": ["verified", ""], "gpt-oss-120b": ["verified", ""] });
  const d = await dispatch({ payload: { cwd: fresh, prompt: "Fix calc.py so x is 3" }, reply: { model_plan: PLAN }, root: fresh, mine: MINE,
    env: { ...e, STEALTHLAB_DISPATCH_AREA_SKIP: "off" }, runtime: rt3 });
  assert.equal(d.handled, true, "switch off: dispatched as before");
});

test("the strong model's extra time ends when it has finished: a hung small model is cut at the plain limit", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it", "glm-5.3-flash"] } },
    profiles: { "glm-5.3-flash": { openai_base_url: "https://example.test/v1", key_env: "K", strong: true, first: true } } }));
  const calls = [];
  const rt = { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const m = calls[Number(id.slice(2)) - 1].model;
      if (m === "glm-5.3-flash") return { run_id: id, state: "failed", cost_usd: 0.001, checks: [{ tail: "x" }] };
      return new Promise(() => {});                         // the small model never finishes
    },
    async applyRun() { return { files: [] }; }, async cancelRun() {} };
  const t0 = Date.now();
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: { ...PLAN, ladder: ["gemma-4-31b-it|stealth", "glm-5.3-flash|stealth"] } },
    root: repo, mine: MINE, env: { ...env, K: "k", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0", STEALTHLAB_DISPATCH_TIMEOUT_S: "10",
      STEALTHLAB_DISPATCH_PARALLEL: "on", STEALTHLAB_DISPATCH_MODEL_CAP: "off" }, runtime: rt });
  assert.equal(d.handled, false);
  assert.ok(Date.now() - t0 < 60000, `ended at the plain limit, not 240 s later (${Date.now() - t0} ms)`);
});

test("test-first hedged: a slow open writer gets the fallback beside it after the hedge time; the first usable tests win", async () => {
  const { env, repo } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } },
    profiles: { "glm-5.3": { openai_base_url: "https://example.test/v1", key_env: "K", model_id: "glm-5.3" } } }));
  fs.writeFileSync(path.join(repo, "solution.py"), "def f(x):\n    pass\n");
  const good = (cost) => ({ stdout: JSON.stringify({ result: "```python\nfrom solution import f\n\ndef test_neg():\n    assert f(-1) == -2\n```", total_cost_usd: cost }) });
  const asked = [];
  const runClaude = (args) => {
    const m = args[args.indexOf("--model") + 1];
    asked.push(m);
    return m === "glm-5.3" ? new Promise((res) => setTimeout(() => res(good(0.001)), 3000)) : good(0.03);   // the open one is slow
  };
  const e = { ...env, K: "k", STEALTHLAB_DISPATCH_TESTFIRST_MODEL: "glm-5.3", STEALTHLAB_DISPATCH_TESTFIRST_HEDGE_S: "1" };
  const t0 = Date.now();
  const spec = await writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env: e, runClaude });
  assert.ok(spec?.file && Date.now() - t0 < 2900, "the fallback's tests, without waiting for the slow writer");
  assert.deepEqual(asked, ["glm-5.3", "claude-sonnet-5-5"]);
  await new Promise((r) => setTimeout(r, 2500));
  const led = fs.readFileSync(path.join(env.STEALTHLAB_HOME, "dispatch_costs.jsonl"), "utf8");
  assert.match(led, /"what":"test-first","model":"claude-sonnet-5-5"/);
  assert.match(led, /"what":"test-first-unused","model":"glm-5.3","cost_usd":0.001/, "the slower call is on the ledger too");
  asked.length = 0;
  const fast = await writeSpecTests({ prompt: "double x", root: repo, mine: MINE, env: { ...e, STEALTHLAB_DISPATCH_TESTFIRST_HEDGE_S: "10" }, runClaude });
  assert.ok(fast?.file);
  assert.deepEqual(asked, ["glm-5.3"], "done within the hedge time: only the open writer is asked");
});
