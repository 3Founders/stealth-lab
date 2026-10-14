import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { dispatch, namedImages, specFailures, triedNote } from "../lib/dispatch.mjs";
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
