import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  CURSOR_HOOKS, SESSION_CONTEXT, addCursorHooks, cursorCommand, cursorModel, onBeforeSubmitPrompt, onPostToolUse,
  onStop, removeCursorHooks, runCursorHook, runCursorLookupWorker, shellVerdict, uninstallCursorHooks, upsertCursorHooks,
} from "../lib/cursor_hooks.mjs";
import { sessionFile } from "../lib/capture_hook.mjs";

const tmpEnv = (extra = {}) => ({ STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "sl-cursor-")), ...extra });
const SETTINGS = { url: "https://kel.example/mcp", token: "t" };
const PROMPT = "add retry with exponential backoff to the http client in this repo";
const RESOLVED = {
  outcome: "resolved", goal: { goal_id: "g1" },
  procedures: [{ procedure_id: "p1", goal_id: "g1", name: "Retry with backoff", steps: [{ do: "wrap the call in a retry loop" }] }],
};
const noWait = async () => {};

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

// Runs beforeSubmitPrompt, then the lookup worker it would have spawned, against a fake server.
async function promptAndLookup(env, { reply = RESOLVED, payload = {}, calls = [] } = {}) {
  let job = null;
  const out = onBeforeSubmitPrompt({ conversation_id: "c1", generation_id: "g-1", prompt: PROMPT, model: "gpt-5", ...payload },
    { env, settings: SETTINGS, spawnWorker: (j) => { job = j; } });
  assert.deepEqual(out, { continue: true });
  if (job) await runCursorLookupWorker(job, { env, settings: SETTINGS, userAgent: "t", fetchImpl: fakeServer(reply, calls) });
  return job;
}

test("beforeSubmitPrompt never blocks: it answers continue and hands the lookup to a worker", () => {
  const env = tmpEnv();
  let job = null;
  const out = onBeforeSubmitPrompt({ conversation_id: "c1", generation_id: "g-1", prompt: PROMPT },
    { env, settings: SETTINGS, spawnWorker: (j) => { job = j; } });
  assert.deepEqual(out, { continue: true });
  assert.ok(job && fs.existsSync(job));
  assert.equal(JSON.parse(fs.readFileSync(job, "utf8")).prompt, PROMPT);
});

test("short prompts, slash commands, no URL and STEALTHLAB_HOOK=off start no lookup", () => {
  for (const [prompt, env, settings] of [
    ["fix it", tmpEnv(), SETTINGS],
    ["/review the whole thing please now", tmpEnv(), SETTINGS],
    [PROMPT, tmpEnv(), { url: "" }],
    [PROMPT, tmpEnv({ STEALTHLAB_HOOK: "off" }), SETTINGS],
  ]) {
    let spawned = false;
    const out = onBeforeSubmitPrompt({ conversation_id: "c1", prompt }, { env, settings, spawnWorker: () => { spawned = true; } });
    assert.deepEqual(out, { continue: true });
    assert.equal(spawned, false, prompt);
  }
});

test("the first tool call after the prompt gets the knowledge, once; the job file holding the prompt is gone", async () => {
  const env = tmpEnv();
  const calls = [];
  const job = await promptAndLookup(env, { calls });
  assert.equal(fs.existsSync(job), false, "the worker deletes the job (it holds the prompt)");
  assert.equal(calls.find((c) => c.method === "tools/call").params.arguments.query, PROMPT);
  const first = await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait });
  assert.match(first.additional_context, /Retry with backoff/);
  assert.match(first.additional_context, /wrap the call in a retry loop/);
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait }), {});
});

test("an agent that calls find_ways itself gets nothing added", async () => {
  const env = tmpEnv();
  await promptAndLookup(env);
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "MCP:find_ways" }, { env, wait: noWait }), {});
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait }), {});
});

test("only the first tool call waits for a pending lookup, and only up to the limit", async () => {
  const env = tmpEnv({ STEALTHLAB_CURSOR_WAIT_MS: "1" });
  onBeforeSubmitPrompt({ conversation_id: "c1", generation_id: "g-1", prompt: PROMPT }, { env, settings: SETTINGS, spawnWorker: () => {} });
  let waits = 0;
  const counting = async () => { waits++; await new Promise((r) => setTimeout(r, 5)); };
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: counting }), {});
  assert.ok(waits >= 1);
  const before = waits;
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: counting }), {});
  assert.equal(waits, before, "the second tool call does not wait");
});

test("a lookup for an older prompt never fills the newer prompt's state", async () => {
  const env = tmpEnv();
  let oldJob = null;
  onBeforeSubmitPrompt({ conversation_id: "c1", generation_id: "g-1", prompt: PROMPT }, { env, settings: SETTINGS, spawnWorker: (j) => { oldJob = j; } });
  onBeforeSubmitPrompt({ conversation_id: "c1", generation_id: "g-2", prompt: PROMPT + " too" }, { env, settings: SETTINGS, spawnWorker: () => {} });
  const r = await runCursorLookupWorker(oldJob, { env, settings: SETTINGS, userAgent: "t", fetchImpl: fakeServer(RESOLVED) });
  assert.equal(r.status, "stale");
});

test("server errors and empty replies add nothing (fail open)", async () => {
  const env = tmpEnv();
  let job = null;
  onBeforeSubmitPrompt({ conversation_id: "c1", generation_id: "g-1", prompt: PROMPT }, { env, settings: SETTINGS, spawnWorker: (j) => { job = j; } });
  const down = async () => { throw new Error("ECONNREFUSED"); };
  assert.equal((await runCursorLookupWorker(job, { env, settings: SETTINGS, userAgent: "t", fetchImpl: down })).status, "empty");
  assert.deepEqual(await onPostToolUse({ conversation_id: "c1", tool_name: "Read" }, { env, wait: noWait }), {});
});

test("test verdicts: runner summary first, then Cursor's exit code; unknown is null", () => {
  assert.equal(shellVerdict(JSON.stringify({ exitCode: 1, stdout: "===== 3 passed in 0.1s =====" })), true);
  assert.equal(shellVerdict(JSON.stringify({ exitCode: 0, stdout: "1 failed, 2 passed in 0.3s" })), false);
  assert.equal(shellVerdict(JSON.stringify({ exitCode: 2, stdout: "boom" })), false);
  assert.equal(shellVerdict(JSON.stringify({ exitCode: 0, stdout: "" })), true);
  assert.equal(shellVerdict("no summary here"), null);
});

test("stop reports one outcome per prompt with Cursor's model and scaffold cursor", async () => {
  const env = tmpEnv();
  await promptAndLookup(env);
  await onPostToolUse({ conversation_id: "c1", tool_name: "Shell", tool_input: { command: "npm test" },
    tool_output: JSON.stringify({ exitCode: 0, stdout: "Tests:       7 passed, 7 total" }) }, { env, wait: noWait });
  let job = null;
  const r = onStop({ conversation_id: "c1", status: "completed", model: "gpt-5" }, { env, hasToken: true, spawnWorker: (j) => { job = j; } });
  assert.equal(r.status, "detached");
  assert.equal(r.report.scaffold, "cursor");
  assert.equal(r.report.model, "gpt-5");
  assert.equal(r.report.accepted, true);
  assert.equal(r.report.goal_id, "g1");
  assert.match(r.report.instance_key, /^cu-/);
  assert.ok(fs.existsSync(job));
  const again = onStop({ conversation_id: "c1", status: "completed", model: "gpt-5" }, { env, hasToken: true, spawnWorker: () => {} });
  assert.equal(again.status, "nothing-to-report");
  assert.ok(JSON.parse(fs.readFileSync(sessionFile(env, "c1"), "utf8")).reported);
});

test("stop reports nothing without a token, an identified Goal, a verdict, a real model, or a completed turn", async () => {
  const env = tmpEnv();
  await promptAndLookup(env);
  const p = { conversation_id: "c1", status: "completed", model: "gpt-5" };
  assert.equal(onStop(p, { env, hasToken: false }).status, "no-token");
  assert.equal(onStop(p, { env, hasToken: true, spawnWorker: () => {} }).status, "nothing-to-report", "no test verdict yet");
  await onPostToolUse({ conversation_id: "c1", tool_name: "Shell", tool_input: { command: "pytest -q" },
    tool_output: JSON.stringify({ exitCode: 1, stdout: "1 failed in 0.2s" }) }, { env, wait: noWait });
  assert.equal(onStop({ ...p, status: "aborted" }, { env, hasToken: true }).status, "not-completed");
  assert.equal(onStop({ ...p, model: "auto" }, { env, hasToken: true, spawnWorker: () => {} }).status, "nothing-to-report");
  assert.equal(cursorModel({ model: "default" }), null);
  const env2 = tmpEnv();
  await promptAndLookup(env2, { reply: { outcome: "no_match" } });
  assert.equal(onStop(p, { env: env2, hasToken: true }).status, "no-lookup");
});

test("CLI entry: session context, prompt always continues, malformed input fails open", async () => {
  const env = tmpEnv();
  assert.equal((await runCursorHook("session", { stdinText: "{}", env })).additional_context, SESSION_CONTEXT);
  assert.deepEqual(await runCursorHook("session", { stdinText: "{}", env: { ...env, STEALTHLAB_CURSOR_SESSION: "off" } }), {});
  assert.deepEqual(await runCursorHook("prompt", { stdinText: "not json", env, settings: SETTINGS }), { continue: true });
  assert.deepEqual(await runCursorHook("tool", { stdinText: "not json", env }), {});
});

test("hooks.json: ours added next to the user's own, refreshed not duplicated, removed alone", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sl-cursor-cfg-"));
  const file = path.join(dir, "hooks.json");
  const mine = { command: "./audit.sh" };
  fs.writeFileSync(file, JSON.stringify({ version: 1, hooks: { beforeSubmitPrompt: [mine] } }));
  const launch = { command: "/usr/bin/node", args: ["/opt/stealthlab-mcp/bin/stealthlab-mcp.mjs"] };
  upsertCursorHooks(file, launch, "linux");
  upsertCursorHooks(file, launch, "linux");
  const doc = JSON.parse(fs.readFileSync(file, "utf8"));
  for (const [event, { mark }] of Object.entries(CURSOR_HOOKS)) {
    const ours = doc.hooks[event].filter((e) => e.command.endsWith(mark));
    assert.equal(ours.length, 1, event);
  }
  assert.deepEqual(doc.hooks.beforeSubmitPrompt[0], mine);
  assert.ok(doc.hooks.postToolUse[0].timeout > 15, "postToolUse may wait for the lookup");
  assert.equal(uninstallCursorHooks(file), true);
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), { version: 1, hooks: { beforeSubmitPrompt: [mine] } });
  assert.equal(uninstallCursorHooks(file), false);
});

test("hooks.json that does not parse is left untouched", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sl-cursor-cfg-"));
  const file = path.join(dir, "hooks.json");
  fs.writeFileSync(file, "{ broken");
  assert.throws(() => upsertCursorHooks(file, { command: "node", args: ["x/stealthlab-mcp.mjs"] }), /not valid JSON/);
  assert.equal(fs.readFileSync(file, "utf8"), "{ broken");
});

test("Windows: a node path with spaces becomes `node` so PowerShell and cmd can both run it", () => {
  const launch = { command: "C:\\Program Files\\nodejs\\node.exe", args: ["C:\\Users\\a\\stealthlab-mcp\\bin\\stealthlab-mcp.mjs"] };
  assert.equal(cursorCommand(launch, "hook cursor-tool", "win32"),
    'node "C:\\Users\\a\\stealthlab-mcp\\bin\\stealthlab-mcp.mjs" hook cursor-tool');   // quoted: bash keeps the backslashes
  const doc = addCursorHooks({}, { command: "/usr/bin/node", args: ["/x/stealthlab-mcp.mjs"] }, "linux");
  assert.equal(doc.version, 1);
  assert.ok(removeCursorHooks(doc));
});
