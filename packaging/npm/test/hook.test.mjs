import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { callFindWays, formatKnowledge, hookPolicy, runPromptHook, shouldLookUp } from "../lib/hook.mjs";
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
