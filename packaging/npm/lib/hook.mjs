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
//
// Delivery mode (STEALTHLAB_HOOK_MODE, default "full"): full = everything below; lean = only a resolved (exact)
// way, nothing for near misses or related examples; off = no lookup. STEALTHLAB_HOOK_MODE_STRONG overrides the
// mode when the session's model matches STEALTHLAB_HOOK_STRONG_MODELS (default /opus|sonnet|fable/i), e.g.
// "off" to save the lookup for frontier models. Unset = the same mode for every model. Evidence as of
// 2026-09-28 (docs/findings.md): full is the only mode shown to help (open models, +8.5); on DS-1000 Sonnet
// gained nothing from full (+1.2) and an estimate of lean from rounds 5 and 7 was no better for Sonnet and
// lost half the open models' gain -- so the default stays full, and the switch exists for testing and cost.
import fs from "node:fs";
import path from "node:path";
import { claudeDir } from "./claude_exec.mjs";
import { modelFromTranscript, rememberLookup } from "./capture_hook.mjs";
import { findStealthRoot, readEntry, requestPayload, unesc, upsertRoutes } from "./library.mjs";

const ACCEPT = "application/json, text/event-stream";

const MODES = new Set(["full", "lean", "off"]);
const modeOf = (v, fallback) => (MODES.has(String(v || "").toLowerCase()) ? String(v).toLowerCase() : fallback);

export function hookPolicy(env = process.env) {
  const mode = modeOf(env.STEALTHLAB_HOOK_MODE, "full");
  let strong;
  try { strong = new RegExp(env.STEALTHLAB_HOOK_STRONG_MODELS || "opus|sonnet|fable", "i"); } catch { strong = /opus|sonnet|fable/i; }
  return {
    enabled: (env.STEALTHLAB_HOOK || "on").toLowerCase() !== "off",
    minWords: Number(env.STEALTHLAB_HOOK_MIN_WORDS || 6),
    timeoutMs: Number(env.STEALTHLAB_HOOK_TIMEOUT_MS || 25000),
    maxChars: Number(env.STEALTHLAB_HOOK_MAX_CHARS || 8000),
    mode,
    strongMode: env.STEALTHLAB_HOOK_MODE_STRONG ? modeOf(env.STEALTHLAB_HOOK_MODE_STRONG, mode) : null,
    strongModels: strong,
  };
}

// The session's model, best effort: the transcript's last assistant message (from the 2nd prompt on), else
// $ANTHROPIC_MODEL, else "model" in Claude Code's settings.json. No hook payload carries it.
export function detectModel(payload, env = process.env) {
  const fromTranscript = payload?.transcript_path ? modelFromTranscript(payload.transcript_path) : null;
  if (fromTranscript) return fromTranscript;
  if (env.ANTHROPIC_MODEL) return env.ANTHROPIC_MODEL;
  try {
    const m = JSON.parse(fs.readFileSync(path.join(claudeDir(env), "settings.json"), "utf8"))?.model;
    return typeof m === "string" && m ? m : null;
  } catch {
    return null;
  }
}

// The mode for this prompt: the strong-model override applies only when set and the model is known and matches.
export function deliveryMode(policy, model) {
  if (policy.strongMode && model && policy.strongModels.test(model)) return policy.strongMode;
  return policy.mode;
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
// `extra`: this repo's library arguments (lib/library.mjs requestPayload) -- sent only when they exist.
export async function callFindWays({ url, token, userAgent, query, repoClaims, timeoutMs, extra = {}, fetchImpl = globalThis.fetch }) {
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
    params: { name: "find_ways", arguments: { query: query.slice(0, 1500), repo_claims: repoClaims, ...extra } },
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

// A step's check (how to tell it worked) rides on the same line, so the agent can verify each step without another
// lookup; it is what .stealth/run.md's `check=` wants.
function steps(proc) {
  return (proc.steps || []).map((s, i) => {
    const text = `${s.do || s.action || s.goal || s.instruction || s.description || ""}`.trim();
    const check = typeof s.check === "string" && s.check.trim() ? ` (check: ${cut(s.check.trim(), 160)})` : "";
    return text ? `  ${i + 1}. ${text}${check}` : "";   // position, not `order` (0- or 1-based by source)
  }).filter(Boolean).join("\n");
}

// CC-BY content must carry its credit wherever it is shared (BLOCKERS I7). It sits right under the title, before
// the steps and the code, so the length cut can never keep the content and drop its credit.
function credit(x) {
  return x?.attribution ? `\n  credit: ${cut(String(x.attribution).replace(/\s+/g, " "), 400)}` : "";
}

function solution(vs, n) {
  if (!vs?.code) return "";
  return `  verified solution${vs.task ? ` (it solved: ${cut(vs.task.replace(/\s+/g, " "), 240)})` : ""}:\n` +
    "```" + (vs.language || "") + "\n" + cut(vs.code, n) + "\n```";
}

// This repo's own solved problems (find_ways' library_matches), read from the local library: the entry's steps
// and the diff that solved it. First in the block -- the evidence says they are what helps most (plan §0).
// mode "lean": only entries the judge called a match.
function libraryParts(reply, root, mode, budget) {
  const out = [];
  if (!root) return out;
  const matches = (reply.library_matches || []).filter((m) => (mode === "lean" ? m.relation === "matches" : true));
  for (const m of matches.slice(0, 2)) {
    let entry = null;
    try { entry = readEntry(root, m.id); } catch { /* the library moved or is unreadable: skip it */ }
    if (!entry) continue;
    const steps = entry.lines.filter((l) => l.startsWith("STEP|")).map((l, i) => {
      const f = l.split("|");
      const check = (f[4] || "").startsWith("check=") && f[4] !== "check=-" ? ` (check: ${unesc(f[4].slice(6))})` : "";
      return `  ${i + 1}. ${unesc(f[3] || "")}${check}`;
    });
    const sol = entry.lines.map((l) => /\|solution=([^|]+)\|/.exec(l)?.[1]).find((x) => x && x !== "-");
    let diff = "";
    if (sol) {
      try { diff = fs.readFileSync(path.join(root, ".stealth", "library", sol), "utf8"); } catch { /* no diff kept */ }
    }
    const how = m.judged ? `${m.relation}, confidence ${m.confidence}` : "not judged: matched by words only";
    const stale = m.status === "stale" ? " -- STALE: files it touched changed since; re-check before reusing" : "";
    out.push(`Solved before in THIS repo (${how}) -- ${m.title}${m.unit && m.unit !== "." ? ` [${m.unit}]` : ""}` +
      ` (${m.outcome}, ${m.verified_at || "unverified"})${stale}:\n${steps.join("\n")}` +
      (diff ? `\n  the diff that solved it (.stealth/library/${sol}):\n` + "```diff\n" + cut(diff, budget) + "\n```" : ""));
  }
  return out;
}

// The knowledge block added to the agent's context, or "" when find_ways had nothing usable.
// mode "lean": only a resolved way (the exact Goal); near misses and related examples are left out.
// `root`: the repo whose .stealth/library.md holds the library matches (none -> they are not shown).
export function formatKnowledge(reply, maxChars = 8000, { mode = "full", root = null } = {}) {
  if (!reply || typeof reply !== "object" || mode === "off") return "";
  const parts = libraryParts(reply, root, mode, 2000);
  const procs = reply.outcome === "resolved" ? (reply.procedures || []) : [];
  for (const p of procs.slice(0, 2)) {
    const tested = p.tested_by_source ? " [its solution passed the source task's own tests]" : "";
    parts.push(`Known way (verified for this Goal) -- ${p.name || p.goal_name || p.procedure_id}${tested}:${credit(p)}\n${steps(p)}` +
      (p.verified_solution ? `\n${solution(p.verified_solution, 2400)}` : ""));
  }
  if (mode === "lean") {
    if (!parts.length) return "";
    return cut("StealthLab (Kel) looked this task up before you started (find_ways already ran for it -- " +
      "don't call it again for the same request). What it knows:\n\n" + parts.join("\n\n"), maxChars);
  }
  if (reply.outcome === "ambiguous") {
    const s = reply.suggested;
    if (s) {
      parts.push(`Closest known Goal (not the same as this request -- adapt it): ${s.goal_name || s.goal_id}` +
        credit(s) + (s.way ? `\n  way: ${s.way}` : "") + (s.verified_solution ? `\n${solution(s.verified_solution, 2000)}` : ""));
    } else {
      const c = (reply.candidates || []).find((x) => x.ways?.length);
      if (c) {
        const w = c.ways[0];
        parts.push(`Closest known Goal (not the same as this request -- adapt it): ${c.goal?.canonical_name || ""}` +
          credit(w) + `\n${steps(w)}` + (w.verified_solution ? `\n${solution(w.verified_solution, 2000)}` : ""));
      }
    }
  }
  for (const ex of (reply.related_examples || []).slice(0, 3)) {
    parts.push(`Similar solved problem (NOT verified to apply -- a worked example to adapt, never copy): ` +
      `${ex.goal_name || ex.goal_id}${credit(ex)}\n${solution(ex.verified_solution, 1500)}`);
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
  const mode = deliveryMode(policy, policy.strongMode ? detectModel(payload, env) : null);
  if (mode === "off") return;
  try {
    const root = findStealthRoot(payload.cwd || process.cwd());   // null: no .stealth here, a plain lookup
    let extra = {};
    try { if (root) extra = requestPayload(root, { env }); } catch { /* no library: plain lookup */ }
    const reply = await callFindWays({
      url: settings.url, token: settings.token, userAgent, query: String(payload.prompt).trim(),
      repoClaims: readClaims(payload.cwd), timeoutMs: policy.timeoutMs, extra, fetchImpl,
    });
    // The route the server chose for this Goal, kept locally so outcomes can be counted against it.
    try {
      if (root && Array.isArray(reply?.routing_rows) && reply.routing_rows.length) upsertRoutes(root, reply.routing_rows);
    } catch { /* routing.md is best-effort */ }
    // For the capture hooks (lib/capture_hook.mjs): which Goal/Procedure this prompt is about. Never fails the hook.
    try { rememberLookup(payload, reply, { env }); } catch { /* capture is best-effort */ }
    const context = formatKnowledge(reply, policy.maxChars, { mode, root });
    if (context) {
      write(JSON.stringify({ hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: context } }));
    }
  } catch (err) {
    log(`stealthlab hook: no knowledge added (${err.message})`);   // fail open
  }
}
