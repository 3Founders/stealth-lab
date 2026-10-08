import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { callFindWays, callTriage, formatKnowledge, hookPolicy, runPromptHook, shouldLookUp, triageUrl } from "../lib/hook.mjs";
import { removeClaudeHook, upsertClaudeHook } from "../lib/clients.mjs";

const policy = hookPolicy({});

test("policy: slash commands, short prompts and STEALTHLAB_HOOK=off are skipped", () => {
  assert.equal(shouldLookUp("/compact", policy), false);
  assert.equal(shouldLookUp("fix it", policy), false);
  assert.equal(shouldLookUp("add a DOCX export to the report page with a download button", policy), true);
  assert.equal(shouldLookUp("add a DOCX export to the report page with a download button", hookPolicy({ STEALTHLAB_HOOK: "off" })), false);
});

const RESOLVED = {
  outcome: "resolved",
  procedures: [{ procedure_id: "p1", name: "Export DOCX", steps: [{ order: 1, do: "install docx" }, { order: 2, do: "render" }],
                 verified_solution: { code: "print('ok')", language: "python", task: "export a report" } }],
};

test("format: resolved way with steps and verified code; related examples labelled", () => {
  const text = formatKnowledge({ ...RESOLVED, related_examples: [
    { goal_name: "Export PDF", verified_solution: { code: "pdf()", language: "python" } }] });
  assert.match(text, /Known way/);
  assert.match(text, /1\. install docx/);
  assert.match(text, /print\('ok'\)/);
  assert.match(text, /NOT verified to apply/);
  assert.match(text, /don't call it again/);
});

test("format: ambiguous uses the suggested candidate; nothing usable -> empty", () => {
  const amb = formatKnowledge({ outcome: "ambiguous", suggested: { goal_name: "Near goal", way: "w", verified_solution: { code: "x=1" } } });
  assert.match(amb, /Closest known Goal/);
  assert.match(amb, /x=1/);
  assert.equal(formatKnowledge({ outcome: "no_match", related_examples: [] }), "");
  assert.equal(formatKnowledge(null), "");
});

test("format: capped at maxChars", () => {
  const big = { ...RESOLVED, procedures: [{ ...RESOLVED.procedures[0], verified_solution: { code: "y".repeat(50000) } }] };
  assert.ok(formatKnowledge(big, 3000).length <= 3000);
});

function fakeServer(reply, calls) {
  return async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : {};
    calls.push({ method: init.method, rpc: body.method, sid: init.headers["mcp-session-id"] });
    const headers = new Map([["mcp-session-id", "S1"], ["content-type", "text/event-stream"]]);
    const h = { get: (k) => headers.get(k) };
    if (body.method === "initialize") {
      return { ok: true, status: 200, headers: h, text: async () => `event: message\ndata: ${JSON.stringify({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2025-06-18" } })}\n\n` };
    }
    if (body.method === "tools/call") {
      assert.equal(body.params.name, "find_ways");
      return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: JSON.stringify(reply) }] } })}\n\n` };
    }
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
}

test("callFindWays: initialize -> initialized -> tools/call over one session", async () => {
  const calls = [];
  const reply = await callFindWays({ url: "http://x/mcp", userAgent: "t", query: "q", repoClaims: "", timeoutMs: 5000,
                                     fetchImpl: fakeServer(RESOLVED, calls) });
  assert.equal(reply.outcome, "resolved");
  assert.deepEqual(calls.slice(0, 3).map((c) => c.rpc), ["initialize", "notifications/initialized", "tools/call"]);
  assert.equal(calls[2].sid, "S1");
});

test("runPromptHook: writes additionalContext; fails open on errors", async () => {
  const out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "add a DOCX export to the report page please", cwd: os.tmpdir() }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: {}, fetchImpl: fakeServer(RESOLVED, []),
    write: (s) => out.push(s), log: () => {} });
  const hso = JSON.parse(out[0]).hookSpecificOutput;
  assert.equal(hso.hookEventName, "UserPromptSubmit");
  assert.match(hso.additionalContext, /Export DOCX/);

  const out2 = [], logs = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "add a DOCX export to the report page please" }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: {},
    fetchImpl: async () => { throw new Error("down"); }, write: (s) => out2.push(s), log: (m) => logs.push(m) });
  assert.equal(out2.length, 0);
  assert.match(logs[0], /no knowledge added/);
});

test("settings.json: hook upsert keeps other hooks; uninstall removes only ours", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "slhook-"));
  const file = path.join(dir, "settings.json");
  fs.writeFileSync(file, JSON.stringify({ model: "x", hooks: { UserPromptSubmit: [{ hooks: [{ type: "command", command: "other-tool" }] }],
                                                                Stop: [{ hooks: [{ type: "command", command: "s" }] }] } }));
  const spec = { command: "npx", args: ["-y", "stealthlab-mcp@latest", "hook-prompt"] };
  upsertClaudeHook(file, spec);
  upsertClaudeHook(file, spec);   // idempotent
  let doc = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.equal(doc.model, "x");
  assert.equal(doc.hooks.UserPromptSubmit.length, 2);
  assert.equal(doc.hooks.UserPromptSubmit[1].hooks[0].command, "npx -y stealthlab-mcp@latest hook-prompt");
  assert.equal(removeClaudeHook(file), true);
  doc = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.deepEqual(doc.hooks.UserPromptSubmit, [{ hooks: [{ type: "command", command: "other-tool" }] }]);
  assert.ok(doc.hooks.Stop);
  assert.equal(removeClaudeHook(file), false);
});

test("format: CC-BY content carries its credit under the title, before steps and code, in every place", () => {
  const vs = { code: "x".repeat(5000), language: "python" };
  const resolved = formatKnowledge({ outcome: "resolved", procedures: [
    { name: "W", procedure_id: "p1", attribution: "by acme, CC-BY-4.0", steps: [{ order: 1, do: "step one" }], verified_solution: vs },
  ] }, 600);
  assert.match(resolved, /W:\n {2}credit: by acme, CC-BY-4\.0\n {2}1\. step one/);
  const amb = formatKnowledge({ outcome: "ambiguous", suggested: { goal_name: "G", attribution: "by b", verified_solution: vs },
    related_examples: [{ goal_name: "R", attribution: "by c", verified_solution: vs }] });
  assert.match(amb, /G\n {2}credit: by b/);
  assert.match(amb, /R\n {2}credit: by c/);
  const cand = formatKnowledge({ outcome: "ambiguous", candidates: [{ goal: { canonical_name: "C" },
    ways: [{ attribution: "by d", steps: [{ order: 1, do: "go" }] }] }] });
  assert.match(cand, /C\n {2}credit: by d\n {2}1\. go/);
  assert.doesNotMatch(formatKnowledge({ outcome: "resolved", procedures: [{ name: "N", steps: [{ order: 1, do: "go on" }] }] }), /credit/);
});

test("format: steps are numbered by position and carry their check", () => {
  const out = formatKnowledge({ outcome: "resolved", procedures: [{ name: "W", steps: [
    { order: 0, do: "Find where paths are built", role: "plan" },
    { order: 1, do: "Run the failing test", role: "verify", check: "pytest t.py::test_a passes" }] }] });
  assert.match(out, /\n {2}1\. Find where paths are built\n {2}2\. Run the failing test \(check: pytest t\.py::test_a passes\)/);
});

test("a way graded by its source's own tests says so", () => {
  const out = formatKnowledge({ outcome: "resolved", procedures: [{ name: "Fix it", tested_by_source: true, steps: [{ do: "edit" }] }] });
  assert.match(out, /Fix it \[its solution passed the source task's own tests\]:/);
  assert.doesNotMatch(formatKnowledge({ outcome: "resolved", procedures: [{ name: "Fix it", steps: [{ do: "edit" }] }] }), /own tests/);
});

// ---- triage: one cheap question before the lookup ---------------------------------------------------------

const TASK = "add a DOCX export to the report page please";

// A server that answers /triage with `verdict` (a function of the request, or a status/throw) and the MCP calls as above.
function triageServer(verdict, reply, calls) {
  const mcp = fakeServer(reply, calls);
  return async (url, init) => {
    if (String(url).endsWith("/triage")) {
      calls.push({ triage: true, url: String(url), method: init.method, body: JSON.parse(init.body), auth: init.headers.authorization });
      return verdict();
    }
    return mcp(url, init);
  };
}
const json = (obj, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(obj) });

test("triageUrl: the route sits next to /mcp, keeping origin and base path", () => {
  assert.equal(triageUrl("https://mcp.example.com/mcp"), "https://mcp.example.com/triage");
  assert.equal(triageUrl("http://127.0.0.1:8765/mcp/"), "http://127.0.0.1:8765/triage");
  assert.equal(triageUrl("https://h/base/mcp?x=1#y"), "https://h/base/triage");
  assert.equal(triageUrl("not a url"), null);
});

test("triage says no lookup needed: find_ways is never called and the prompt is left alone", async () => {
  const calls = [], out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "explain what parse_args does in cli.py please", cwd: os.tmpdir() }),
    settings: { url: "http://x/mcp", token: "tok" }, userAgent: "t", env: {},
    fetchImpl: triageServer(() => json({ needs_retrieval: false, kind: "knowledge_question" }), RESOLVED, calls),
    write: (s) => out.push(s), log: () => {} });
  assert.equal(out.length, 0);
  assert.equal(calls.length, 1);                       // the triage POST only: no initialize, no tools/call
  assert.deepEqual([calls[0].triage, calls[0].url, calls[0].method, calls[0].auth], [true, "http://x/triage", "POST", "Bearer tok"]);
  assert.match(calls[0].body.query, /parse_args/);
});

test("triage says look it up: the lookup runs as before", async () => {
  const calls = [], out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: TASK, cwd: os.tmpdir() }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: {},
    fetchImpl: triageServer(() => json({ needs_retrieval: true, kind: "reusable_task" }), RESOLVED, calls),
    write: (s) => out.push(s), log: () => {} });
  assert.deepEqual(calls.filter((c) => !c.triage).map((c) => c.rpc).slice(0, 3), ["initialize", "notifications/initialized", "tools/call"]);
  assert.match(JSON.parse(out[0]).hookSpecificOutput.additionalContext, /Export DOCX/);
});

for (const [name, verdict] of [
  ["a server error", () => json({ error: "boom" }, 500)],
  ["an older server without the route", () => json({ detail: "Not Found" }, 404)],
  ["a body that is not JSON", () => ({ ok: true, status: 200, text: async () => "<html>" })],
  ["a verdict without the field", () => json({ kind: "conversation" })],
  ["a non-boolean answer", () => json({ needs_retrieval: "no" })],
  ["a network failure", () => { throw new Error("down"); }],
]) {
  test(`triage unavailable (${name}): fails toward the lookup`, async () => {
    const calls = [], out = [];
    await runPromptHook({ stdinText: JSON.stringify({ prompt: TASK, cwd: os.tmpdir() }),
      settings: { url: "http://x/mcp" }, userAgent: "t", env: {},
      fetchImpl: triageServer(verdict, RESOLVED, calls), write: (s) => out.push(s), log: () => {} });
    assert.ok(calls.some((c) => c.rpc === "tools/call"), "find_ways must still run");
    assert.equal(out.length, 1);
  });
}

test("triage: STEALTHLAB_HOOK_TRIAGE=off never asks; the question has its own short timeout", async () => {
  const calls = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: TASK, cwd: os.tmpdir() }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: { STEALTHLAB_HOOK_TRIAGE: "off" },
    fetchImpl: triageServer(() => json({ needs_retrieval: false }), RESOLVED, calls), write: () => {}, log: () => {} });
  assert.equal(calls.filter((c) => c.triage).length, 0);
  assert.ok(calls.some((c) => c.rpc === "tools/call"));
  assert.equal(hookPolicy({ STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS: "1500" }).triageTimeoutMs, 1500);
  assert.equal(hookPolicy({}).triageTimeoutMs, 4000);
  // a hung triage call is abandoned at its own timeout, not the lookup's 25 s
  const t0 = Date.now();
  const hung = (_u, init) => new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(new Error("aborted"))));
  assert.equal(await callTriage({ url: "http://x/mcp", userAgent: "t", query: TASK, timeoutMs: 80, fetchImpl: hung }), null);
  assert.ok(Date.now() - t0 < 2000);
});

test("triage: a skipped prompt clears the previous prompt's remembered lookup", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "slhook-"));
  const env = { STEALTHLAB_HOME: dir, STEALTHLAB_CAPTURE: "on" };
  const payload = { session_id: "S-triage-1", prompt: TASK, cwd: dir };
  const { rememberLookup, lookupIdentity } = await import("../lib/capture_hook.mjs");
  const resolved = { outcome: "resolved", procedures: [{ procedure_id: "p1", goal_id: "g1" }] };
  assert.ok(lookupIdentity(resolved));
  const before = rememberLookup(payload, resolved, { env });
  const calls = [];
  await runPromptHook({ stdinText: JSON.stringify({ ...payload, prompt: "thanks, that looks right to me, continue please" }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env,
    fetchImpl: triageServer(() => json({ needs_retrieval: false, kind: "conversation" }), RESOLVED, calls),
    write: () => {}, log: () => {} });
  // remembering "nothing" for the same session removes the file, so a second clear reports no record
  assert.equal(rememberLookup(payload, null, { env }), false);
  assert.ok(before === true || before === false);
});
