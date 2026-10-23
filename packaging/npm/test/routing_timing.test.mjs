import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { runPromptHook } from "../lib/hook.mjs";
import { dispatch } from "../lib/dispatch.mjs";
import { routingTimer, routingTimingEnabled, routingTimingFile, summarize } from "../lib/routing_timing.mjs";
import { LEGACY_POLICY } from "./helpers/legacy_policy.mjs";

// a stub that waits for its timeout opens no socket: keep the loop alive, as hook.test.mjs does
const keepAlive = setInterval(() => {}, 1 << 30);
after(() => clearInterval(keepAlive));

const PROMPT = "add a DOCX export to the report page please";
const REPLY = { outcome: "resolved", procedures: [{ procedure_id: "p1", name: "Export DOCX", steps: [{ do: "install docx" }] }],
                model_plan: { status: "ok", ladder: ["x|stealth"], plan_ms: 7 } };

// /triage answers with `triage()`; the MCP calls answer find_ways with REPLY
function server(triage) {
  return async (url, init) => {
    if (String(url).endsWith("/triage")) return triage(init);
    const body = init.body ? JSON.parse(init.body) : {};
    const h = { get: (k) => ({ "mcp-session-id": "S1" })[k] };
    if (body.method === "initialize") {
      return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2025-06-18" } })}\n\n` };
    }
    if (body.method === "tools/call") {
      return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: JSON.stringify(REPLY) }] } })}\n\n` };
    }
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
}
const json = (obj) => ({ ok: true, status: 200, text: async () => JSON.stringify(obj) });
const home = () => fs.mkdtempSync(path.join(os.tmpdir(), "rt-home-"));
const hook = (env, fetchImpl, out = []) => runPromptHook({
  stdinText: JSON.stringify({ prompt: PROMPT, cwd: os.tmpdir() }), settings: { url: "http://x/mcp" }, userAgent: "t", env,
  fetchImpl, write: (s) => out.push(s), log: () => {},
});

test("off by default: only STEALTHLAB_ROUTING_TIMING=1 (or on) turns it on", () => {
  assert.equal(routingTimingEnabled({}), false);
  assert.equal(routingTimingEnabled({ STEALTHLAB_ROUTING_TIMING: "0" }), false);
  assert.equal(routingTimingEnabled({ STEALTHLAB_ROUTING_TIMING: "1" }), true);
  assert.equal(routingTimingEnabled({ STEALTHLAB_ROUTING_TIMING: "on" }), true);
  assert.equal(routingTimer({ env: {} }).on, false);
});

test("off: a whole hook run writes nothing", async () => {
  const h = home();
  const out = [];
  await hook({ STEALTHLAB_HOME: h }, server(() => json({ needs_retrieval: true, kind: "reusable_task" })), out);
  assert.equal(out.length, 1, "the hook works as before");
  assert.equal(fs.existsSync(routingTimingFile({ STEALTHLAB_HOME: h })), false);
  assert.deepEqual(fs.readdirSync(h).filter((f) => /timing/.test(f)), []);
});

test("on: one line per prompt, with the parts, plan_ms, and no prompt text", async () => {
  const h = home();
  const env = { STEALTHLAB_HOME: h, STEALTHLAB_ROUTING_TIMING: "1" };
  await hook(env, server(() => json({ needs_retrieval: true, kind: "reusable_task" })));
  const lines = fs.readFileSync(routingTimingFile(env), "utf8").trim().split("\n");
  assert.equal(lines.length, 1);
  const row = JSON.parse(lines[0]);
  assert.equal(row.surface, "claude-code");
  assert.equal(row.prompt_len, PROMPT.length);
  assert.match(row.prompt_hash, /^[0-9a-f]{12}$/);
  assert.ok(!lines[0].includes("DOCX"), "the prompt's text is never written");
  for (const k of ["triage_ms", "find_ways_ms", "routing_ms"]) assert.ok(Number.isFinite(row[k]), `${k} is a number`);
  assert.equal(row.plan_ms, 7);
  assert.equal(row.dispatched, false);
});

test("on: a triage call that times out still fails open toward the lookup, and is timed", async () => {
  const h = home();
  const env = { STEALTHLAB_HOME: h, STEALTHLAB_ROUTING_TIMING: "1", STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS: "150" };
  const hung = (init) => new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(new Error("aborted"))));
  const out = [];
  await hook(env, server(hung), out);
  assert.equal(out.length, 1, "the lookup still ran and added its knowledge");
  assert.match(JSON.parse(out[0]).hookSpecificOutput.additionalContext, /Export DOCX/);
  const row = JSON.parse(fs.readFileSync(routingTimingFile(env), "utf8").trim());
  assert.equal(row.triage, null, "no verdict");
  assert.ok(row.triage_ms >= 140, `the wait is in triage_ms (${row.triage_ms})`);
});

test("on: a timing file that cannot be written never fails the hook", async () => {
  const h = home();
  fs.mkdirSync(path.join(h, "routing_timing.jsonl"));      // a directory where the file should be: appendFileSync throws
  const out = [];
  await hook({ STEALTHLAB_HOME: h, STEALTHLAB_ROUTING_TIMING: "1" }, server(() => json({ needs_retrieval: true })), out);
  assert.equal(out.length, 1);
});

test("summarize: routing ends at the first cheap model, else at the decision, else at the last call", () => {
  assert.equal(summarize({ triage_start: 0, triage_end: 900, find_ways_start: 900, find_ways_end: 1300, dispatch_start: 1300,
                           gate_end: 1302, brief_end: 2500, locate_end: 2600, first_rung: 2700, dispatch_end: 90000 }).routing_ms, 2700);
  const s = summarize({ triage_start: 0, triage_end: 900, find_ways_start: 900, find_ways_end: 1300, dispatch_start: 1300, dispatch_end: 1310 });
  assert.deepEqual([s.routing_ms, s.dispatched, s.find_ways_ms], [1310, false, 400]);
  assert.equal(summarize({ triage_start: 5, triage_end: 800 }).routing_ms, 800);
});

// a git repo with a .stealth/ and one file a cheap model "fixes"
function newRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "rt-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".stealth")); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return repo;
}
const passingRuntime = (repo) => ({
  async achieve() { return { run_id: "r-1" }; },
  async runResult(id) { return { run_id: id, state: "verified", cost_usd: 0.001, checks: [] }; },
  async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
  async cancelRun() {},
});

test("dispatch marks its steps up to the first cheap model, and a timing object that throws changes nothing", async () => {
  const h = home();
  fs.writeFileSync(path.join(h, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const env = { STEALTHLAB_HOME: h, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off" };
  const plan = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth"] };
  const call = (repo, timing) => dispatch({ payload: { cwd: repo, prompt: "Fix calc.py" }, reply: { model_plan: plan }, root: repo,
    mine: "claude-opus-5-5|claude-code", env, runtime: passingRuntime(repo), timing });
  const marks = [];
  const d = await call(newRepo(), { mark: (m) => marks.push(m) });
  assert.equal(d.handled, true);
  assert.deepEqual(marks, ["gate_end", "brief_end", "locate_end", "first_rung"]);
  const d2 = await call(newRepo(), { mark() { throw new Error("broken timer"); } });
  assert.equal(d2.handled, true, "a broken timer is ignored");
});
