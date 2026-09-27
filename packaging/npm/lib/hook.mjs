// Claude Code UserPromptSubmit hook: StealthLab knowledge reaches the agent whether or not the model
// decides to call find_ways.
//
// Why: with MCP the model decides when to call a tool, and models differ. In DS-1000 round 4
// (experiments/ds1000/PREREGISTRATION_4.md) gpt-oss never called find_ways and Sonnet skipped it on a
// quarter of tasks, while the same knowledge placed in the prompt helped significantly (+8.5 points).
// So the WHEN is decided here, deterministically: every task-like prompt gets one find_ways lookup, and
// anything useful is added to the agent's context. The model still decides WHAT to do with it.
//
// Policy (all overridable by env): skip slash commands and prompts under STEALTHLAB_HOOK_MIN_WORDS words;
// time out after STEALTHLAB_HOOK_TIMEOUT_MS; inject at most STEALTHLAB_HOOK_MAX_CHARS characters; inject
// nothing when find_ways has nothing usable. Fail open: any error means no injection, never a blocked prompt.
// STEALTHLAB_HOOK=off disables it.
import fs from "node:fs";
import path from "node:path";

const ACCEPT = "application/json, text/event-stream";

export function hookPolicy(env = process.env) {
  return {
    enabled: (env.STEALTHLAB_HOOK || "on").toLowerCase() !== "off",
    minWords: Number(env.STEALTHLAB_HOOK_MIN_WORDS || 6),
    timeoutMs: Number(env.STEALTHLAB_HOOK_TIMEOUT_MS || 25000),
    maxChars: Number(env.STEALTHLAB_HOOK_MAX_CHARS || 8000),
  };
}

export function shouldLookUp(prompt, policy) {
  const text = String(prompt || "").trim();
  if (!policy.enabled || !text || text.startsWith("/")) return false;
  return (text.match(/\w+/g) || []).length >= policy.minWords;
}

function readClaims(cwd) {
  try {
    return fs.readFileSync(path.join(cwd || process.cwd(), ".stealth", "claims.md"), "utf8").slice(0, 65536);
  } catch {
    return "";
  }
}

function parseBody(text) {
  const t = text.trim();
  if (t.startsWith("{") || t.startsWith("[")) return JSON.parse(t);
  const data = t.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).filter(Boolean);
  for (let i = data.length - 1; i >= 0; i--) {
    try {
      const obj = JSON.parse(data[i]);
      if (obj.result || obj.error) return obj;
    } catch { /* keep looking */ }
  }
  return {};
}

// One find_ways call over Streamable HTTP: initialize -> initialized -> tools/call -> close.
export async function callFindWays({ url, token, userAgent, query, repoClaims, timeoutMs, fetchImpl = globalThis.fetch }) {
  const signal = AbortSignal.timeout(timeoutMs);
  const headers = { accept: ACCEPT, "content-type": "application/json", "user-agent": userAgent };
  if (token) headers.authorization = `Bearer ${token}`;
  const post = async (body, sid, proto) => fetchImpl(url, {
    method: "POST", signal, body: JSON.stringify(body),
    headers: { ...headers, ...(sid ? { "mcp-session-id": sid } : {}), ...(proto ? { "mcp-protocol-version": proto } : {}) },
  });
  const init = await post({
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "stealthlab-hook", version: "1" } },
  });
  if (!init.ok) throw new Error(`initialize HTTP ${init.status}`);
  const sid = init.headers.get("mcp-session-id") || undefined;
  const proto = parseBody(await init.text()).result?.protocolVersion;
  await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid, proto);
  const res = await post({
    jsonrpc: "2.0", id: 2, method: "tools/call",
    params: { name: "find_ways", arguments: { query: query.slice(0, 1500), repo_claims: repoClaims } },
  }, sid, proto);
  if (!res.ok) throw new Error(`find_ways HTTP ${res.status}`);
  const body = parseBody(await res.text());
  if (sid) fetchImpl(url, { method: "DELETE", headers: { ...headers, "mcp-session-id": sid } }).catch(() => {});
  if (body.error) throw new Error(body.error.message || "find_ways error");
  const text = body.result?.content?.find((c) => c.type === "text")?.text || "";
  return text.startsWith("{") ? JSON.parse(text) : null;
}

const cut = (s, n) => {
  const t = String(s || "");
  return t.length <= n ? t : t.slice(0, n - 15) + "\n[... truncated]";
};

function steps(proc) {
  return (proc.steps || []).map((s, i) => `  ${s.order ?? i + 1}. ${s.do || s.action || s.goal || s.instruction || ""}`.trimEnd())
    .filter((l) => l.trim().length > 3).join("\n");
}

function solution(vs, n) {
  if (!vs?.code) return "";
  return `  verified solution${vs.task ? ` (it solved: ${cut(vs.task.replace(/\s+/g, " "), 240)})` : ""}:\n` +
    "```" + (vs.language || "") + "\n" + cut(vs.code, n) + "\n```";
}

// The knowledge block added to the agent's context, or "" when find_ways had nothing usable.
export function formatKnowledge(reply, maxChars = 8000) {
  if (!reply || typeof reply !== "object") return "";
  const parts = [];
  const procs = reply.outcome === "resolved" ? (reply.procedures || []) : [];
  for (const p of procs.slice(0, 2)) {
    parts.push(`Known way (verified for this Goal) -- ${p.name || p.goal_name || p.procedure_id}:\n${steps(p)}` +
      (p.verified_solution ? `\n${solution(p.verified_solution, 2400)}` : ""));
  }
  if (reply.outcome === "ambiguous") {
    const s = reply.suggested;
    if (s) {
      parts.push(`Closest known Goal (not the same as this request -- adapt it): ${s.goal_name || s.goal_id}` +
        (s.way ? `\n  way: ${s.way}` : "") + (s.verified_solution ? `\n${solution(s.verified_solution, 2000)}` : ""));
    } else {
      const c = (reply.candidates || []).find((x) => x.ways?.length);
      if (c) {
        const w = c.ways[0];
        parts.push(`Closest known Goal (not the same as this request -- adapt it): ${c.goal?.canonical_name || ""}` +
          `\n${steps(w)}` + (w.verified_solution ? `\n${solution(w.verified_solution, 2000)}` : ""));
      }
    }
  }
  for (const ex of (reply.related_examples || []).slice(0, 3)) {
    parts.push(`Similar solved problem (NOT verified to apply -- a worked example to adapt, never copy): ` +
      `${ex.goal_name || ex.goal_id}\n${solution(ex.verified_solution, 1500)}`);
  }
  if (!parts.length) return "";
  const head = "StealthLab (Kel) looked this task up before you started (find_ways already ran for it -- " +
    "don't call it again for the same request). What it knows:\n\n";
  return cut(head + parts.join("\n\n"), maxChars);
}

export async function runPromptHook({ stdinText, settings, userAgent, env = process.env, fetchImpl, write, log }) {
  const policy = hookPolicy(env);
  let payload;
  try {
    payload = JSON.parse(stdinText || "{}");
  } catch {
    return;
  }
  if (!settings.url || !shouldLookUp(payload.prompt, policy)) return;
  try {
    const reply = await callFindWays({
      url: settings.url, token: settings.token, userAgent, query: String(payload.prompt).trim(),
      repoClaims: readClaims(payload.cwd), timeoutMs: policy.timeoutMs, fetchImpl,
    });
    const context = formatKnowledge(reply, policy.maxChars);
    if (context) {
      write(JSON.stringify({ hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: context } }));
    }
  } catch (err) {
    log(`stealthlab hook: no knowledge added (${err.message})`);   // fail open
  }
}
