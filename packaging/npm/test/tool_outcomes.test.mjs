import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { onPostToolUse, onStop } from "../lib/capture_hook.mjs";
import { noteSuggested, recordToolUse, refOf, sendOutcome, startPrompt, suggestedRefs, takeRecord, toolsOutcomeUrl }
  from "../lib/tool_outcomes.mjs";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kell-toolout-"));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

// a Claude Code config with two servers: one by package, one remote (its URL carries a key that must never leave)
const cfgDir = path.join(tmp, "claude"), proj = path.join(tmp, "proj");
fs.mkdirSync(cfgDir, { recursive: true });
fs.mkdirSync(proj, { recursive: true });
fs.writeFileSync(path.join(cfgDir, ".claude.json"), JSON.stringify({
  mcpServers: { pw: { command: "npx", args: ["-y", "@playwright/mcp@latest"], env: { K: "secret" } },
                "docs.ctx": { type: "http", url: "https://mcp.context7.com/mcp?key=SECRET" } } }));
const env = { STEALTHLAB_HOME: path.join(tmp, "home"), CLAUDE_CONFIG_DIR: cfgDir };

test("refOf and suggestedRefs name a server by package or host only", () => {
  assert.equal(refOf("package @playwright/mcp@latest"), "@playwright/mcp");
  assert.equal(refOf("remote server at mcp.context7.com"), "mcp.context7.com");
  assert.equal(refOf("https://mcp.stripe.com/x?key=1"), "mcp.stripe.com");
  assert.equal(refOf("claude mcp add pw -- npx -y @playwright/mcp"), "@playwright/mcp");
  assert.equal(refOf("something else"), "");
  assert.deepEqual(suggestedRefs({ tools: [
    { source: "installed", summary: "package @playwright/mcp@latest" },
    { source: "registry", connector_url: "https://mcp.stripe.com", install: "claude mcp add --transport http stripe https://mcp.stripe.com" },
    { source: "registry", install: "claude mcp add snyk -- npx -y snyk" }] }), ["@playwright/mcp", "mcp.stripe.com", "snyk"]);
  assert.equal(toolsOutcomeUrl("https://h/mcp"), "https://h/tools/outcome");
});

test("a prompt's record: suggested tools, then MCP and web tools used (resolved via the config), taken once", () => {
  const sid = "s-record";
  assert.ok(startPrompt(env, sid));
  noteSuggested(env, sid, "browser", { tools: [{ source: "installed", summary: "package @playwright/mcp@latest" }] });
  const base = { session_id: sid, cwd: proj };
  assert.deepEqual(recordToolUse({ ...base, tool_name: "mcp__pw__browser_navigate" }, { env }), { ref: "@playwright/mcp", tool: "browser_navigate" });
  assert.deepEqual(recordToolUse({ ...base, tool_name: "mcp__docs_ctx__query-docs" }, { env }), { ref: "mcp.context7.com", tool: "query-docs" });
  assert.deepEqual(recordToolUse({ ...base, tool_name: "WebSearch" }, { env }), { ref: "builtin-websearch", tool: "WebSearch" });
  assert.equal(recordToolUse({ ...base, tool_name: "mcp__unknown__x" }, { env }), null);       // not nameable: stays local
  recordToolUse({ ...base, tool_name: "mcp__pw__browser_navigate" }, { env });                 // a repeat is one entry
  const rec = takeRecord(env, sid);
  assert.equal(rec.goal, "browser");
  assert.deepEqual(rec.suggested, ["@playwright/mcp"]);
  assert.deepEqual(rec.used.map((u) => u.ref), ["@playwright/mcp", "mcp.context7.com", "builtin-websearch"]);
  assert.ok(!JSON.stringify(rec).includes("SECRET") && !JSON.stringify(rec).includes("secret"));
  assert.equal(takeRecord(env, sid), null);                                                    // taken: gone
});

test("capture: an MCP tool call is recorded through PostToolUse, and Stop sends it with the outcome", () => {
  const sid = "s-stop";
  startPrompt(env, sid);
  onPostToolUse({ session_id: sid, cwd: proj, tool_name: "mcp__pw__browser_click", tool_input: {} }, { env });
  const jobs = [];
  const r = onStop({ session_id: sid, transcript_path: "" }, { env, hasToken: true, spawnWorker: (f) => jobs.push(f) });
  assert.equal(r.status, "detached");
  assert.deepEqual(r.tools_outcome.used, [{ ref: "@playwright/mcp", tool: "browser_click" }]);
  assert.equal(r.tools_outcome.outcome, "unknown");                                            // no test ran
  const job = JSON.parse(fs.readFileSync(jobs[0], "utf8"));
  assert.equal(job.tools_outcome.used[0].ref, "@playwright/mcp");
  // without a token nothing is sent
  startPrompt(env, "s-notoken");
  onPostToolUse({ session_id: "s-notoken", cwd: proj, tool_name: "WebFetch", tool_input: {} }, { env });
  assert.equal(onStop({ session_id: "s-notoken", transcript_path: "" }, { env, hasToken: false, spawnWorker: () => assert.fail() }).status, "no-token");
});

test("sendOutcome posts to /tools/outcome with the bearer, and needs a token", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200, text: async () => '{"recorded": 2}' }; };
  const r = await sendOutcome({ used: [{ ref: "x.com" }], outcome: "pass" }, { url: "https://h/mcp", token: "t", fetchImpl });
  assert.equal(r.recorded, 2);
  assert.equal(calls[0].url, "https://h/tools/outcome");
  assert.equal(calls[0].init.headers.authorization, "Bearer t");
  assert.equal(await sendOutcome({}, { url: "https://h/mcp", token: "", fetchImpl }), null);
});
