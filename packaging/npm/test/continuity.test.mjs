import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { KEEP, cheapWritten, continuityFile, continuityLine, dispatchedTurns, markShown, pendingLines, recordDispatch } from "../lib/continuity.mjs";
import { sessionTurns } from "../lib/brief.mjs";
import { runPromptHook } from "../lib/hook.mjs";

const home = () => fs.mkdtempSync(path.join(os.tmpdir(), "cont-"));

test("a dispatch is remembered and said once, in the agreed words", () => {
  const env = { STEALTHLAB_HOME: home() };
  assert.deepEqual(pendingLines("s1", { env }), []);
  assert.equal(recordDispatch({ sessionId: "s1", model: "gemma-4-31b-it", files: ["calc.py"], task: "Fix the calc constant" }, { env }), true);
  assert.deepEqual(pendingLines("s1", { env }), ["Earlier in this session, gemma-4-31b-it changed calc.py to Fix the calc constant (StealthLab, check passed)."]);
  markShown("s1", { env });
  assert.deepEqual(pendingLines("s1", { env }), []);
});

test("one line per dispatch, the last 5 kept, per session", () => {
  const env = { STEALTHLAB_HOME: home() };
  for (let i = 1; i <= 7; i++) recordDispatch({ sessionId: "s1", model: "m", files: [`f${i}.py`], task: `task ${i}` }, { env });
  recordDispatch({ sessionId: "other", model: "m", files: ["z.py"], task: "elsewhere" }, { env });
  const lines = pendingLines("s1", { env });
  assert.equal(lines.length, KEEP);
  assert.match(lines[0], /f3\.py to task 3/);
  assert.match(lines[4], /f7\.py to task 7/);
  assert.equal(pendingLines("other", { env }).length, 1);
});

test("many files are shortened, a long or multi-line task is one short line", () => {
  const files = Array.from({ length: 8 }, (_, i) => `a${i}.py`);
  const line = continuityLine({ model: "m", files, task: "x".repeat(400) });
  assert.match(line, /a0\.py, a1\.py, a2\.py, a3\.py, a4\.py and 3 more to x+\.\.\. \(StealthLab, check passed\)\.$/);
  assert.ok(!line.includes("\n"));
  assert.match(continuityLine({ model: "m", files: [], task: "line one\n  line two" }), /changed no files to line one line two \(/);
});

test("off switch, bad session ids and a missing model remember nothing; nothing throws", () => {
  const env = { STEALTHLAB_HOME: home() };
  assert.equal(recordDispatch({ sessionId: "s1", model: "m" }, { env: { ...env, STEALTHLAB_CONTINUITY: "off" } }), false);
  assert.equal(recordDispatch({ sessionId: "../escape", model: "m" }, { env }), false);
  assert.equal(recordDispatch({ sessionId: "s1", model: "" }, { env }), false);
  assert.equal(continuityFile(env, "a/b"), null);
  assert.deepEqual(pendingLines(undefined, { env }), []);
  markShown(undefined, { env });
});

// ---- through the prompt hook ---------------------------------------------------------------------------------------

const REPLY = { outcome: "resolved", procedures: [{ procedure_id: "p1", name: "Export DOCX", steps: [{ order: 1, do: "install docx" }] }] };

// find_ways over the MCP wire the hook speaks (see test/hook.test.mjs)
function fakeServer(reply) {
  return async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : {};
    const headers = new Map([["mcp-session-id", "S1"], ["content-type", "text/event-stream"]]);
    const h = { get: (k) => headers.get(k) };
    if (body.method === "initialize") return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2025-06-18" } })}\n\n` };
    if (body.method === "tools/call") return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: JSON.stringify(reply) }] } })}\n\n` };
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
}

async function prompt(env, { session = "s1", text, dispatchImpl, settings = { url: "http://x/mcp" } }) {
  const out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: text, cwd: os.tmpdir(), session_id: session }), settings, userAgent: "t", env,
    fetchImpl: fakeServer(REPLY), write: (s) => out.push(JSON.parse(s)), log: () => {}, dispatchImpl });
  return out;
}

test("hook: a blocked (dispatched) prompt is remembered; the NEXT prompt's context starts with the line, then it is not repeated", async () => {
  const env = { STEALTHLAB_HOME: home() };
  const handled = async () => ({ handled: true, text: "Done by gemma-4-31b-it ...", model: "gemma-4-31b-it", files: ["calc.py"] });
  const first = await prompt(env, { text: "fix the constant in calc.py so that it returns 2 please", dispatchImpl: handled });
  assert.equal(first.length, 1);
  assert.equal(first[0].decision, "block");

  const second = await prompt(env, { text: "now add a DOCX export to the report page please", dispatchImpl: async () => null });
  const ctx = second[0].hookSpecificOutput.additionalContext;
  assert.ok(ctx.startsWith("Earlier in this session, gemma-4-31b-it changed calc.py to fix the constant in calc.py so that it returns 2 please (StealthLab, check passed)."), ctx);
  assert.match(ctx, /Export DOCX/);                       // the lookup's own knowledge follows

  const third = await prompt(env, { text: "and add a PDF export to the report page too please", dispatchImpl: async () => null });
  assert.doesNotMatch(third[0].hookSpecificOutput.additionalContext, /Earlier in this session/);
});

test("hook: a prompt that is blocked again delivers nothing yet; both lines arrive together on the next one", async () => {
  const env = { STEALTHLAB_HOME: home() };
  const handled = (model, file) => async () => ({ handled: true, text: "Done", model, files: [file] });
  await prompt(env, { text: "first fix in a.py please do it now", dispatchImpl: handled("m1", "a.py") });
  const blocked = await prompt(env, { text: "second fix in b.py please do it now", dispatchImpl: handled("m2", "b.py") });
  assert.equal(blocked.length, 1);
  assert.equal(blocked[0].decision, "block");
  const next = await prompt(env, { text: "now add a DOCX export to the report page please", dispatchImpl: async () => null });
  const ctx = next[0].hookSpecificOutput.additionalContext;
  assert.match(ctx, /m1 changed a\.py to first fix/);
  assert.match(ctx, /m2 changed b\.py to second fix/);
});

test("hook: when the lookup adds no context the line is still delivered, once; another session sees nothing", async () => {
  const env = { STEALTHLAB_HOME: home() };
  recordDispatch({ sessionId: "s1", model: "m1", files: ["a.py"], task: "fix a" }, { env });
  const none = { url: "" };                               // no server configured: the lookup returns at once
  const a = await prompt(env, { text: "thanks, that looks right to me", settings: none });
  assert.match(a[0].hookSpecificOutput.additionalContext, /^Earlier in this session, m1 changed a\.py to fix a/);
  assert.equal((await prompt(env, { text: "thanks again for that", settings: none })).length, 0);
  recordDispatch({ sessionId: "s1", model: "m3", files: ["c.py"], task: "fix c" }, { env });
  assert.equal((await prompt(env, { session: "other", text: "thanks, that looks right to me", settings: none })).length, 0);
});

test("hook: STEALTHLAB_CONTINUITY=off records and says nothing", async () => {
  const env = { STEALTHLAB_HOME: home(), STEALTHLAB_CONTINUITY: "off" };
  const handled = async () => ({ handled: true, text: "Done", model: "m1", files: ["a.py"] });
  await prompt(env, { text: "fix the constant in calc.py so that it returns 2 please", dispatchImpl: handled });
  const next = await prompt(env, { text: "now add a DOCX export to the report page please", dispatchImpl: async () => null });
  assert.doesNotMatch(next[0].hookSpecificOutput.additionalContext, /Earlier in this session/);
});


test("a dispatched turn is kept as conversation, and merged into the session's turns in time order", () => {
  const env = { STEALTHLAB_HOME: home() };
  recordDispatch({ sessionId: "s1", model: "glm-5.3-flash", files: [], task: "fix calc", prompt: "calc.x should be 2, fix it", text: "Done by glm-5.3-flash in 40 s" }, { env, now: 2000 });
  assert.deepEqual(dispatchedTurns("s1", { env }).map((t) => [t.role, t.text]), [["user", "calc.x should be 2, fix it"], ["assistant", "Done by glm-5.3-flash in 40 s"]]);
  const tr = path.join(home(), "t.jsonl");
  fs.writeFileSync(tr, [JSON.stringify({ type: "user", timestamp: new Date(1000).toISOString(), message: { role: "user", content: "hi, calc is broken" } }),
    JSON.stringify({ type: "assistant", timestamp: new Date(1500).toISOString(), message: { role: "assistant", content: [{ type: "text", text: "Which part?" }] } })].join("\n") + "\n");
  assert.deepEqual(sessionTurns({ transcriptPath: tr, sessionId: "s1", env }).map((t) => t.text),
    ["hi, calc is broken", "Which part?", "calc.x should be 2, fix it", "Done by glm-5.3-flash in 40 s"]);
});

test("cheapWritten: a file still as the cheap model left it is its change; one written since is not", () => {
  const env = { STEALTHLAB_HOME: home() };
  const root = home();
  fs.writeFileSync(path.join(root, "calc.py"), "x = 2\n");
  recordDispatch({ sessionId: "s1", model: "m", files: ["calc.py"], task: "t", prompt: "p", text: "Done", root }, { env });
  assert.deepEqual([...cheapWritten("s1", root, { env })], ["calc.py"]);
  const later = new Date(Date.now() + 5000);
  fs.utimesSync(path.join(root, "calc.py"), later, later);         // the session edited it after
  assert.deepEqual([...cheapWritten("s1", root, { env })], []);
});
