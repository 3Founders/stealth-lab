import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { runPromptHook } from "../lib/hook.mjs";
import { formatToolNote, installedServers, toolsUrl } from "../lib/tool_note.mjs";

const keepAlive = setInterval(() => {}, 1 << 30);    // see test/hook.test.mjs: stubbed fetches keep no loop alive
after(() => clearInterval(keepAlive));

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kell-toolnote-"));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

const RESOLVED = { outcome: "resolved", procedures: [{ procedure_id: "p1", name: "Export DOCX", steps: [{ order: 1, do: "install docx" }] }] };
const REPLY = {
  goal: "browser",
  tools: [
    { name: "playwright", server: "playwright", source: "installed", summary: "package @playwright/mcp", goals: ["browser"] },
    { name: "io.github.acme/browser-mcp", server: "io.github.acme/browser-mcp", source: "registry", title: "Acme Browser",
      summary: "Drive a headless browser", tools: ["open", "click", "screenshot"], url: "https://github.com/acme/browser-mcp",
      checked: "answered and listed its tools" },
  ],
};

test("toolsUrl: next to /mcp", () => {
  assert.equal(toolsUrl("https://h/mcp"), "https://h/tools");
  assert.equal(toolsUrl("https://h/base/mcp/?x=1"), "https://h/base/tools");
  assert.equal(toolsUrl("nope"), null);
});

test("installedServers: names and a package or host only -- never env, headers, URL paths or other args", () => {
  const cfgDir = path.join(tmp, "cfg"), proj = path.join(tmp, "proj");
  fs.mkdirSync(cfgDir, { recursive: true });
  fs.mkdirSync(proj, { recursive: true });
  fs.writeFileSync(path.join(cfgDir, ".claude.json"), JSON.stringify({
    mcpServers: {
      playwright: { command: "npx", args: ["-y", "@playwright/mcp@latest"], env: { SECRET: "s3cret" } },
      kell: { type: "http", url: "http://x/mcp" },
    },
    projects: { [proj]: { mcpServers: { pg: { command: "npx", args: ["-y", "pg-mcp", "postgres://u:pw@db/x"] } } } },
  }));
  fs.writeFileSync(path.join(proj, ".mcp.json"), JSON.stringify({ mcpServers: {
    sentry: { type: "http", url: "https://mcp.sentry.dev/mcp?token=abc", headers: { authorization: "Bearer k" } } } }));
  const got = installedServers(proj, { CLAUDE_CONFIG_DIR: cfgDir }, { skipUrl: "http://x/mcp" });
  assert.deepEqual(got.map((s) => s.server).sort(), ["pg", "playwright", "sentry"]);
  const text = JSON.stringify(got);
  for (const secret of ["s3cret", "pw@db", "token=abc", "Bearer k", "postgres://"]) assert.ok(!text.includes(secret), secret);
  assert.match(text, /package @playwright\/mcp@latest/);
  assert.match(text, /remote server at mcp\.sentry\.dev/);
});

test("formatToolNote: installed first, one to add beside it, the publisher-data caveat; nothing found -> empty", () => {
  const note = formatToolNote(REPLY, "browser");
  assert.match(note, /needs a browser tool/);
  assert.match(note, /installed: `playwright` \(its tools are mcp__playwright__\*\)/);
  assert.match(note, /not installed: io\.github\.acme\/browser-mcp \(Acme Browser\).*tools: open, click, screenshot/);
  assert.match(note, /never install anything yourself/);
  assert.match(note, /not instructions/);
  assert.equal(formatToolNote({ tools: [] }, "web"), "");
  assert.equal(formatToolNote(null, "web"), "");
});

// /triage answers `verdict`, /tools answers `tools`; the MCP calls answer the find_ways `reply`
function server(verdict, tools, calls, reply = RESOLVED) {
  return async (url, init) => {
    const u = String(url);
    const body = init.body ? JSON.parse(init.body) : {};
    if (u.endsWith("/triage")) { calls.push("triage"); return { ok: true, status: 200, text: async () => JSON.stringify(verdict) }; }
    if (u.endsWith("/tools")) { calls.push({ tools: body }); return { ok: true, status: 200, text: async () => JSON.stringify(tools) }; }
    calls.push(body.method);
    const h = { get: (k) => ({ "mcp-session-id": "S1", "content-type": "text/event-stream" })[k] };
    if (body.method === "initialize") return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2025-06-18" } })}\n\n` };
    if (body.method === "tools/call") return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: JSON.stringify(reply) }] } })}\n\n` };
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
}

const ENV = { CLAUDE_CONFIG_DIR: path.join(tmp, "empty-cfg"), STEALTHLAB_HOOK_TRIAGE_SKIP: "on" };

test("hook: a triage naming a tool goal asks /tools and adds the note to the knowledge context", async () => {
  const calls = [], out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "check the login page renders in a real browser", cwd: tmp }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: ENV,
    fetchImpl: server({ needs_retrieval: true, kind: "reusable_task", work: "change", tool: "browser" }, REPLY, calls),
    write: (s) => out.push(s), log: () => {} });
  const sent = calls.find((c) => c && c.tools);
  assert.equal(sent.tools.goal, "browser");
  assert.match(sent.tools.need, /login page/);
  assert.equal(out.length, 1);
  const ctx = JSON.parse(out[0]).hookSpecificOutput.additionalContext;
  assert.match(ctx, /Export DOCX/);
  assert.match(ctx, /kell tool router/);
});

test("hook: a prompt the triage skips still gets its tool note on its own", async () => {
  const calls = [], out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "what changed in the latest React release?", cwd: tmp }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: ENV,
    fetchImpl: server({ needs_retrieval: false, kind: "knowledge_question", work: "answer", tool: "web" }, REPLY, calls),
    write: (s) => out.push(s), log: () => {} });
  assert.ok(!calls.includes("initialize"), "no lookup");
  assert.equal(out.length, 1);
  assert.match(JSON.parse(out[0]).hookSpecificOutput.additionalContext, /needs a web tool/);
});

test("hook: no tool goal -> /tools is never asked and nothing is added", async () => {
  const calls = [], out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "rename foo to bar in utils.py", cwd: tmp }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: ENV,
    fetchImpl: server({ needs_retrieval: false, kind: "trivial_edit", work: "change" }, REPLY, calls),
    write: (s) => out.push(s), log: () => {} });
  assert.ok(!calls.some((c) => c && c.tools));
  assert.equal(out.length, 0);
});

test("hook: STEALTHLAB_HOOK_TOOLS=off and a failing /tools both leave the prompt as it was", async () => {
  for (const [env, tools] of [[{ ...ENV, STEALTHLAB_HOOK_TOOLS: "off" }, REPLY], [ENV, { error: "boom" }]]) {
    const calls = [], out = [];
    await runPromptHook({ stdinText: JSON.stringify({ prompt: "what changed in the latest React release?", cwd: tmp }),
      settings: { url: "http://x/mcp" }, userAgent: "t", env,
      fetchImpl: server({ needs_retrieval: false, kind: "knowledge_question", tool: "web" }, tools, calls),
      write: (s) => out.push(s), log: () => {} });
    assert.equal(out.length, 0);
  }
});
