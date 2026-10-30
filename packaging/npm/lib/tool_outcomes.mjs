// Which outside tools a prompt was offered and used, and how it ended -- the tool router's outcome record
// (backend db/162_tool_observations.sql, POST <server>/tools/outcome; docs/tool_routing_plan.md P3).
//
// Per prompt, a small file next to the capture hook's session (hooks/sessions/<id>.tools.json): the tool goal and the
// tools the tool note suggested (lib/hook.mjs), then every MCP tool and web search/fetch the session called
// (PostToolUse, lib/capture_hook.mjs). At Stop the capture worker sends it with the prompt's test verdict, and the
// library entry for the prompt is tagged with the tools it used.
//
// What leaves the machine: each tool's server as its package or URL host (resolved from the Claude Code config, as the
// tool note does -- never the user's own name for it, a URL path, args or env), the tool's name, the goal id, the
// outcome. No prompt text.
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.mjs";
import { installedServers } from "./tool_note.mjs";

export const TOOL_MATCH = /^(mcp__|WebSearch$|WebFetch$)/;
const BUILTIN = { WebSearch: "builtin-websearch", WebFetch: "builtin-webfetch" };
const MAX_USED = 30;

export function toolsFile(env, sessionId) {
  const id = String(sessionId || "");
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) return null;
  return path.join(configDir(env), "hooks", "sessions", `${id}.tools.json`);
}

function read(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
}

function write(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(obj), { mode: 0o600 });
}

// "package @playwright/mcp@latest" -> "@playwright/mcp"; "remote server at mcp.context7.com" -> the host; a URL -> its host
export function refOf(text) {
  const t = String(text || "").trim();
  let m = /^remote server at\s+(\S+)/i.exec(t) || /^https?:\/\/([^/?#\s:]+)/i.exec(t);
  if (m) return m[1].toLowerCase();
  m = /(?:^package\s+|(?:npx -y|uvx)\s+)(@?[\w.-]+(?:\/[\w.-]+)?)/i.exec(t);
  if (m) return m[1].replace(/(.)@[^/]*$/, "$1").toLowerCase();
  return "";
}

// a /tools reply's entries -> the refs it offered (installed ones by their description, others by URL or package)
export function suggestedRefs(reply) {
  const out = [];
  for (const t of Array.isArray(reply?.tools) ? reply.tools : []) {
    const ref = t.source === "installed" ? refOf(t.summary) : refOf(t.connector_url || "") || refOf(t.install || "");
    if (ref && !out.includes(ref)) out.push(ref);
  }
  return out.slice(0, 10);
}

// every prompt starts a fresh record (a prompt that needs no tool leaves goal null, and records only what it used)
export function startPrompt(env, sessionId, { goal = null, suggested = [] } = {}) {
  const file = toolsFile(env, sessionId);
  if (!file) return false;
  write(file, { at: Date.now(), goal, suggested, used: [] });
  return true;
}

export function noteSuggested(env, sessionId, goal, reply) {
  const file = toolsFile(env, sessionId);
  const s = file && read(file);
  if (!s) return false;
  write(file, { ...s, goal, suggested: suggestedRefs(reply) });
  return true;
}

// Claude Code names an MCP tool mcp__<server>__<tool>, the server name with characters outside [A-Za-z0-9_-] replaced
const mcpName = (name) => String(name).replace(/[^A-Za-z0-9_-]/g, "_");

// PostToolUse for an MCP tool or a web search/fetch -> appended to the prompt's record (null: not ours, or no ref)
export function recordToolUse(payload, { env = process.env } = {}) {
  const name = String(payload?.tool_name || "");
  if (!TOOL_MATCH.test(name)) return null;
  const file = toolsFile(env, payload.session_id);
  const s = file && read(file);
  if (!s) return null;
  let ref = BUILTIN[name] || "";
  let tool = name;
  const m = /^mcp__(.+?)__(.+)$/.exec(name);
  if (m) {
    tool = m[2];
    const cwd = typeof payload.cwd === "string" ? payload.cwd : process.cwd();
    const srv = installedServers(cwd, env).find((x) => mcpName(x.server) === m[1]);
    ref = srv ? refOf(srv.tools?.[0]?.description) : "";
  }
  if (!ref) return null;                         // a server we cannot name across users (no package, no URL) stays local
  if (!s.used.some((u) => u.ref === ref && u.tool === tool)) s.used = [...s.used, { ref, tool }].slice(-MAX_USED);
  write(file, s);
  return { ref, tool };
}

// At Stop: the prompt's record, taken (the file is removed) -> null when nothing was offered or used
export function takeRecord(env, sessionId) {
  const file = toolsFile(env, sessionId);
  const s = file && read(file);
  if (file) fs.rmSync(file, { force: true });
  if (!s || (!s.used?.length && !s.suggested?.length)) return null;
  return s;
}

export function toolsOutcomeUrl(mcpUrl) {
  try {
    const u = new URL(mcpUrl);
    u.pathname = u.pathname.replace(/\/mcp\/?$/, "").replace(/\/$/, "") + "/tools/outcome";
    u.search = "";
    u.hash = "";
    return u.toString();
  } catch {
    return null;
  }
}

// the capture worker's send: {tool_goal, suggested, used, outcome, check_kind, goal_id} -> {recorded} | null
export async function sendOutcome(body, { url, token, fetchImpl = globalThis.fetch, timeoutMs = 15000 } = {}) {
  const target = toolsOutcomeUrl(url);
  if (!target || !token) return null;
  const res = await fetchImpl(target, {
    method: "POST", signal: AbortSignal.timeout(timeoutMs), body: JSON.stringify(body),
    headers: { "content-type": "application/json", accept: "application/json", authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`tools/outcome HTTP ${res.status}`);
  return JSON.parse(await res.text());
}
