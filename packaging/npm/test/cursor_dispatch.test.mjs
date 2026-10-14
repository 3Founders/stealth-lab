import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { cursorDispatchEnabled, cursorPlanModel, onBeforeSubmitPromptDispatch, runCursorDispatchPrompt }
  from "../lib/cursor_dispatch.mjs";
import { CURSOR_DISPATCH_TIMEOUT_S, CURSOR_HOOKS, addCursorHooks, onPostToolUse, runCursorHook }
  from "../lib/cursor_hooks.mjs";
import { cursorRule } from "../lib/instructions.mjs";

const SETTINGS = { url: "https://kel.example/mcp", token: "t" };
const PROMPT = "make add_numbers in calc.py return the sum of its two arguments";
const PLAN = { status: "ok", ladder: ["gemma-4|ollama", "gpt-5|cursor"], instance_key: "ik-1", goal_id: "g1" };
const REPLY = {
  outcome: "resolved", goal: { goal_id: "g1" }, model_plan: PLAN,
  procedures: [{ procedure_id: "p1", goal_id: "g1", name: "Sum two numbers", steps: [{ do: "return a + b" }] }],
};
const noWait = async () => {};

// STEALTHLAB_HOME with the executor installed (dispatch: true, one open model) and a workspace with .stealth/.
function setup({ installed = true, extra = {} } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "sl-cdisp-"));
  if (installed) {
    fs.writeFileSync(path.join(home, "config.json"), JSON.stringify({ dispatch: true }));
    fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { ollama: { models: ["gemma-4"] } } }));
  }
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "sl-cws-"));
  fs.mkdirSync(path.join(ws, ".stealth"));
  return { env: { STEALTHLAB_HOME: home, CLAUDE_CONFIG_DIR: path.join(home, "claude"), STEALTHLAB_HOOK_TRIAGE: "off",
                  STEALTHLAB_CAPTURE: "off", ...extra }, ws };
}

function fakeServer(reply, calls = []) {
  return async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : {};
    calls.push(body);
    const h = { get: (k) => (k === "mcp-session-id" ? "S1" : null) };
    if (body.method === "initialize") {
      return { ok: true, status: 200, headers: h, text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2025-06-18" } }) };
    }
    if (body.method === "tools/call") {
      return { ok: true, status: 200, headers: h, text: async () => JSON.stringify({ jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: JSON.stringify(reply) }] } }) };
    }
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
}

const payload = (ws, extra = {}) => ({ conversation_id: "c1", generation_id: "g-1", prompt: PROMPT, model: "gpt-5",
                                       workspace_roots: [ws], ...extra });

test("dispatch is on only with the executor installed and no off switch", () => {
  assert.equal(cursorDispatchEnabled(setup().env), true);
  assert.equal(cursorDispatchEnabled(setup({ installed: false }).env), false);
  assert.equal(cursorDispatchEnabled(setup({ extra: { STEALTHLAB_CURSOR_DISPATCH: "off" } }).env), false);
  assert.equal(cursorDispatchEnabled(setup({ extra: { STEALTHLAB_DISPATCH: "off" } }).env), false);
});

test("Cursor's model slug becomes the plan's model; auto needs STEALTHLAB_CURSOR_MODEL", () => {
  assert.equal(cursorPlanModel({ model: "claude-4.5-sonnet" }, {}), "claude-sonnet-4-5");
  assert.equal(cursorPlanModel({ model: "claude-4.1-opus-thinking" }, {}), "claude-opus-4-1");
  assert.equal(cursorPlanModel({ model: "gpt-5" }, {}), "gpt-5");
  assert.equal(cursorPlanModel({ model: "auto" }, {}), null);
  assert.equal(cursorPlanModel({ model: "auto" }, { STEALTHLAB_CURSOR_MODEL: "claude-sonnet-4-5" }), "claude-sonnet-4-5");
});

test("a verified cheap change stops the prompt with a message to the user", async () => {
  const { env, ws } = setup();
  const calls = [];
  let got = null;
  const dispatchImpl = async (args) => {
    got = args;
    return { handled: true, unit: "gemma-4|ollama", tried: [], text: "Done by gemma-4 (StealthLab dispatched it). calc.py" };
  };
  const out = await onBeforeSubmitPromptDispatch(payload(ws), { env, settings: SETTINGS, userAgent: "t",
    fetchImpl: fakeServer(REPLY, calls), dispatchImpl });
  assert.deepEqual(out, { continue: false, user_message: "Done by gemma-4 (StealthLab dispatched it). calc.py" });
  // dispatch() gets what the Claude Code hook gives it: the prompt, the workspace, the plan, the session's unit
  assert.equal(got.payload.prompt, PROMPT);
  assert.equal(got.payload.cwd, ws);
  assert.equal(got.root, ws);
  assert.equal(got.mine, "gpt-5|cursor");
  assert.equal(got.reply.model_plan.instance_key, "ik-1");
  const args = calls.find((c) => c.method === "tools/call").params.arguments;
  assert.equal(args.my_model, "gpt-5|cursor");
  assert.deepEqual(args.candidates, ["gemma-4|ollama"]);
  // nothing waits for the agent's first tool call
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait }), {});
});

test("nothing passed: the prompt goes on and the agent gets what was tried plus the knowledge at its first tool call", async () => {
  const { env, ws } = setup();
  const dispatchImpl = async () => ({ handled: false, tried: [{ unit: "gemma-4|ollama", state: "failed" }], remaining: [] });
  const out = await onBeforeSubmitPromptDispatch(payload(ws), { env, settings: SETTINGS, userAgent: "t",
    fetchImpl: fakeServer(REPLY), dispatchImpl });
  assert.deepEqual(out, { continue: true });
  const first = await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait });
  assert.match(first.additional_context, /first tried this on gemma-4 \(failed\)/);
  assert.match(first.additional_context, /Sum two numbers/);
  assert.doesNotMatch(first.additional_context, /gemma-4\|ollama/, "the session is not shown a plan of hand-overs");
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait }), {});
});

test("not applicable (no executor, no .stealth, auto model, short prompt): null, so the plain hook runs", async () => {
  const run = (env, p) => onBeforeSubmitPromptDispatch(p, { env, settings: SETTINGS, fetchImpl: fakeServer(REPLY),
    dispatchImpl: async () => { throw new Error("must not run"); } });
  const a = setup({ installed: false });
  assert.equal(await run(a.env, payload(a.ws)), null);
  const b = setup();
  assert.equal(await run(b.env, payload(fs.mkdtempSync(path.join(os.tmpdir(), "sl-nostealth-")))), null);
  assert.equal(await run(b.env, payload(b.ws, { model: "auto" })), null);
  assert.equal(await run(b.env, payload(b.ws, { prompt: "fix it" })), null);
});

test("triage saying no knowledge is needed: dispatch still decides (a quick fix is a task), nothing reaches the context", async () => {
  // Changed 2026-10-14 with the Claude Code hook: the knowledge triage judges retrieval, not whether the cheap models
  // can do the task ("add is broken, fix it" got "not needed" and was never dispatched). Whether a prompt is a task at
  // all is dispatch's brief gate; here it says no (null), so the prompt goes on with no context added.
  const { env, ws } = setup({ extra: { STEALTHLAB_HOOK_TRIAGE: "on" } });
  const fetchImpl = async (url, init) => {
    if (String(url).endsWith("/triage")) return { ok: true, status: 200, text: async () => JSON.stringify({ needs_retrieval: false }) };
    return fakeServer(REPLY)(url, init);
  };
  let asked = 0;
  const out = await onBeforeSubmitPromptDispatch(payload(ws), { env, settings: SETTINGS, fetchImpl,
    dispatchImpl: async () => { asked++; return null; } });
  assert.deepEqual(out, { continue: true });
  assert.equal(asked, 1, "dispatch is consulted");
});

test("an error anywhere fails open: the prompt goes on", async () => {
  const { env, ws } = setup();
  const out = await runCursorDispatchPrompt(payload(ws), { env, settings: SETTINGS, fetchImpl: fakeServer(REPLY),
    dispatchImpl: async () => { throw new Error("executor exploded"); } });
  assert.deepEqual(out, { continue: true });
});

test("the hook entry without the executor keeps the old instant answer", async () => {
  const { env, ws } = setup({ installed: false });
  const out = await runCursorHook("prompt", { stdinText: JSON.stringify(payload(ws)), env, settings: { url: "" } });
  assert.deepEqual(out, { continue: true });
});

test("hooks.json: with dispatch the prompt hook gets minutes, the others keep their limits", () => {
  const launch = { command: "node", args: ["/x/bin/stealthlab-mcp.mjs"] };
  const plain = addCursorHooks({}, launch, "linux");
  assert.equal(plain.hooks.beforeSubmitPrompt[0].timeout, CURSOR_HOOKS.beforeSubmitPrompt.timeout);
  const withDispatch = addCursorHooks({}, launch, "linux", { dispatch: true });
  assert.equal(withDispatch.hooks.beforeSubmitPrompt[0].timeout, CURSOR_DISPATCH_TIMEOUT_S);
  assert.equal(withDispatch.hooks.postToolUse[0].timeout, CURSOR_HOOKS.postToolUse.timeout);
  // re-installing replaces our entry, never duplicates it
  assert.equal(addCursorHooks(withDispatch, launch, "linux").hooks.beforeSubmitPrompt.length, 1);
});

test("the Cursor rule tells the agent about changes a cheaper model made", () => {
  assert.match(cursorRule(), /Done by <model>/);
});

test("end to end with the real dispatch(): a stub executor's verified change is applied and the prompt is stopped", async () => {
  const { dispatch } = await import("../lib/dispatch.mjs");
  const { env, ws } = setup({ extra: { STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                                       STEALTHLAB_DISPATCH_CROSSCHECK: "off" } });
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const g = (...a) => execFileSync("git", ["-C", ws, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(ws, "calc.py"), "x = 1\n");
  fs.writeFileSync(path.join(ws, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  const calls = [];
  const runtime = {
    async achieve(input) { calls.push(input); return { run_id: "r-1" }; },
    async runResult(id) { return { run_id: id, state: "verified", cost_usd: 0.001, checks: [] }; },
    async applyRun() { fs.writeFileSync(path.join(ws, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
  };
  const reply = { ...REPLY, model_plan: { ...PLAN, ladder: ["gemma-4-31b-it|stealth", "gpt-5|cursor"] } };
  const out = await onBeforeSubmitPromptDispatch(payload(ws), { env, settings: SETTINGS, userAgent: "t",
    fetchImpl: fakeServer(reply), dispatchImpl: (args) => dispatch({ ...args, runtime }) });
  assert.equal(out.continue, false);
  assert.match(out.user_message, /^Done by gemma-4-31b-it/);
  assert.match(out.user_message, /calc\.py/);
  assert.equal(fs.readFileSync(path.join(ws, "calc.py"), "utf8"), "x = 2\n");
  assert.equal(calls[0].repo_path, ws);
  assert.deepEqual(calls[0].checks, ["python -m pytest -q"]);
});

// ---- the install, through the real CLI ---------------------------------------------------------------------------

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");

test("install --client cursor --with-exec writes the prompt hook with minutes and turns dispatch on; without --with-exec it keeps the short limit; uninstall removes the hooks", () => {
  const run = (args, env) => spawnSync(process.execPath, [BIN, ...args, "--url", "https://kel.example/mcp"], { encoding: "utf8", env: { ...process.env, ...env } });
  const hooksOf = (home) => JSON.parse(fs.readFileSync(path.join(home, ".cursor", "hooks.json"), "utf8")).hooks;

  const a = { STEALTHLAB_TEST_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "cur-home-")), STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "cur-sl-")) };
  assert.equal(run(["install", "--client", "cursor", "--with-exec"], a).status, 0);
  assert.equal(hooksOf(a.STEALTHLAB_TEST_HOME).beforeSubmitPrompt[0].timeout, CURSOR_DISPATCH_TIMEOUT_S);
  assert.equal(JSON.parse(fs.readFileSync(path.join(a.STEALTHLAB_HOME, "config.json"), "utf8")).dispatch, true);
  assert.equal(run(["uninstall", "--client", "cursor"], a).status, 0);
  assert.ok(!fs.existsSync(path.join(a.STEALTHLAB_TEST_HOME, ".cursor", "hooks.json")) || !hooksOf(a.STEALTHLAB_TEST_HOME).beforeSubmitPrompt?.length);

  const b = { STEALTHLAB_TEST_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "cur-home-")), STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "cur-sl-")) };
  assert.equal(run(["install", "--client", "cursor"], b).status, 0);
  assert.equal(hooksOf(b.STEALTHLAB_TEST_HOME).beforeSubmitPrompt[0].timeout, CURSOR_HOOKS.beforeSubmitPrompt.timeout);
  assert.equal(JSON.parse(fs.readFileSync(path.join(b.STEALTHLAB_HOME, "config.json"), "utf8")).dispatch, undefined);
});
