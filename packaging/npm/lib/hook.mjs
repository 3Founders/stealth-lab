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
// Triage first: the lookup is the expensive part (a search, several judge calls), and many prompts are not
// reusable tasks at all ("explain this function", "rename foo to bar", "thanks, continue"). Before it, the hook
// asks the server one question over ONE request (POST <server>/triage, a single JEV judgment, not the three-step
// MCP handshake): does this prompt need a lookup? Only an explicit `needs_retrieval: false` skips it. A timeout
// (STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS), an error, an older server without the route, or any other answer runs the
// lookup exactly as before. STEALTHLAB_HOOK_TRIAGE=off skips the question.
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
import { DELEGATOR, claudeAlias, executorFor } from "./model_guard.mjs";
import { execConfigPath } from "./exec/store.mjs";
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
    triage: (env.STEALTHLAB_HOOK_TRIAGE || "on").toLowerCase() !== "off",
    triageTimeoutMs: Number(env.STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS || 4000),
    routing: (env.STEALTHLAB_HOOK_ROUTING || "on").toLowerCase() !== "off",
    candidates: parseCandidates(env.STEALTHLAB_HOOK_CANDIDATES, env),
    // how sure a plan must be before it trusts a cheaper model (server defaults: 0.90 / 0.90). Lower = cheaper
    // first rungs, more escalations; the local-only experiment varies it.
    reliability: num01(env.STEALTHLAB_HOOK_RELIABILITY),
    // default: the plan must be about as reliable as this session's own model alone ("match", tolerance 0.03 --
    // "match:0.05" sets another); a number in (0,1) is a fixed target instead (the server's default is 0.90)
    reliabilityMatch: matchTolerance(env.STEALTHLAB_HOOK_RELIABILITY),
    reliabilityConfidence: num01(env.STEALTHLAB_HOOK_RELIABILITY_CONFIDENCE),
    maxChars: Number(env.STEALTHLAB_HOOK_MAX_CHARS || 8000),
    mode,
    strongMode: env.STEALTHLAB_HOOK_MODE_STRONG ? modeOf(env.STEALTHLAB_HOOK_MODE_STRONG, mode) : null,
    strongModels: strong,
  };
}

// Model plan (on by default; STEALTHLAB_HOOK_ROUTING=off skips it). The hook tells find_ways which model this
// session runs (`my_model`) and which other models the client can hand work to (`candidates`), so the reply can
// say "keep it" or "give it to a cheaper/stronger model" and remember the plan under an instance_key. In Claude
// Code those are the Claude models a subagent can run; STEALTHLAB_HOOK_CANDIDATES="model|scaffold,..." replaces
// the list (an empty value sends my_model only). The server drops any unit it cannot price.
export const CLAUDE_CODE_CANDIDATES = ["claude-haiku-4-5|claude-code", "claude-sonnet-5-5|claude-code", "claude-opus-5-5|claude-code"];

function matchTolerance(raw) {
  const v = String(raw ?? "match").trim().toLowerCase();
  if (v === "" || v === "match") return { tolerance: null };
  const m = /^match:(0(?:\.\d+)?|1(?:\.0+)?)$/.exec(v);
  return m ? { tolerance: Number(m[1]) } : null;               // a number (fixed target) or anything else: no match
}

function num01(raw) {
  const v = Number(raw);
  return raw !== undefined && raw !== "" && Number.isFinite(v) && v > 0 && v < 1 ? v : null;
}

export function parseCandidateList(raw) {
  return String(raw || "").split(",").map((x) => x.trim()).filter((x) => /^[^|\s]+\|[^|\s]+$/.test(x));
}

function parseCandidates(raw, env) {
  return raw === undefined ? [...CLAUDE_CODE_CANDIDATES, ...execCandidates(env)] : parseCandidateList(raw);
}

// With the local executor installed (`install --with-exec`: the stealth-delegator agent exists), every model
// ~/.stealthlab/exec.json configures for an executor is a unit a plan may choose: "model|executor". The model guard
// hands such a step to the delegator (lib/model_guard.mjs). A malformed exec.json adds nothing.
export function execCandidates(env = process.env) {
  try {
    if (!fs.existsSync(path.join(claudeDir(env), "agents", "stealth-delegator.md"))) return [];
    const cfg = JSON.parse(fs.readFileSync(execConfigPath(env), "utf8"));
    const out = [];
    for (const [id, spec] of Object.entries(cfg?.executors || {})) {
      for (const m of Array.isArray(spec?.models) ? spec.models : []) {
        const u = `${m}|${id}`;
        if (/^[^|\s]+\|[^|\s]+$/.test(u) && !out.includes(u)) out.push(u);
      }
    }
    return out;
  } catch {
    return [];
  }
}

// The find_ways arguments that ask for a model plan, or {} when routing is off or the model is unknown.
export function routingArgs(policy, model, scaffold = "claude-code") {
  if (!policy.routing || !model) return {};
  const mine = `${model}|${scaffold}`;
  const others = policy.candidates.filter((c) => c !== mine);
  const constraints = {
    ...(policy.reliability !== null && policy.reliability !== undefined ? { reliability_target: policy.reliability } : {}),
    ...(policy.reliabilityMatch && (policy.reliability === null || policy.reliability === undefined)
      ? { reliability_baseline: mine,
          ...(policy.reliabilityMatch.tolerance !== null ? { reliability_tolerance: policy.reliabilityMatch.tolerance } : {}) }
      : {}),
    ...(policy.reliabilityConfidence !== null && policy.reliabilityConfidence !== undefined
      ? { reliability_confidence: policy.reliabilityConfidence } : {}),
  };
  return { my_model: mine, ...(others.length ? { candidates: others } : {}),
           ...(Object.keys(constraints).length ? { model_constraints: constraints } : {}) };
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

const CLAIMS_BUDGET = 64000; // the server's MAX_REPO_CLAIMS_BYTES

// Repository facts for find_ways: claims.md, then the claims/<unit>.md pages of the units this prompt is
// about (named in the prompt by path, name or slug, or the unit the agent's cwd is in), within the
// server's byte budget. A small repository sends every page. Finds .stealth/ from a sub-directory too.
export function readClaims(cwd, prompt = "") {
  let dir = path.resolve(cwd || process.cwd());
  let sdir = null;
  for (;;) {
    if (fs.existsSync(path.join(dir, ".stealth", "claims.md"))) { sdir = path.join(dir, ".stealth"); break; }
    const up = path.dirname(dir);
    if (up === dir) return "";
    dir = up;
  }
  const read = (rel) => { try { return fs.readFileSync(path.join(sdir, rel), "utf8"); } catch { return ""; } };
  let out = read("claims.md");
  const units = [];
  for (const line of read(path.join("index", "units.idx")).split(/\r?\n/)) {
    if (!line.startsWith("UNIT|")) continue;
    const f = line.split("|");
    const kv = Object.fromEntries(f.slice(3).map((x) => [x.slice(0, x.indexOf("=")), x.slice(x.indexOf("=") + 1)]));
    if (f[2] !== "." && kv.page?.startsWith("claims/")) units.push({ slug: f[1], path: f[2], name: kv.name, page: kv.page });
  }
  if (!units.length) return out.slice(0, CLAIMS_BUDGET);
  const pages = units.map((u) => ({ ...u, text: read(u.page) }));
  const total = Buffer.byteLength(out) + pages.reduce((s, p) => s + Buffer.byteLength(p.text), 0);
  const rel = path.relative(dir, path.resolve(cwd || process.cwd())).split(path.sep).join("/");
  const p = String(prompt || "").toLowerCase();
  const score = (u) => {
    if (total <= CLAIMS_BUDGET * 0.75) return 1; // small repository: every page
    let s = 0;
    if (rel && (rel === u.path || rel.startsWith(u.path + "/"))) s += 4;
    if (p.includes(u.path.toLowerCase())) s += 3;
    if (u.name && u.name.length >= 3 && p.includes(u.name.toLowerCase())) s += 2;
    if (u.slug.length >= 3 && new RegExp(`\\b${u.slug.replace(/[^a-z0-9-]/g, "")}\\b`).test(p)) s += 1;
    return s;
  };
  const chosen = pages.map((u) => ({ u, s: score(u) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s || b.u.path.length - a.u.path.length);
  for (const { u } of chosen) {
    if (Buffer.byteLength(out) + Buffer.byteLength(u.text) + 1 > CLAIMS_BUDGET) continue;
    out += (out.endsWith("\n") ? "" : "\n") + u.text;
  }
  return out.slice(0, CLAIMS_BUDGET);
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

// The triage route sits next to the MCP endpoint: https://host/mcp -> https://host/triage.
export function triageUrl(mcpUrl) {
  try {
    const u = new URL(mcpUrl);
    u.pathname = u.pathname.replace(/\/mcp\/?$/, "").replace(/\/$/, "") + "/triage";
    u.search = "";
    u.hash = "";
    return u.toString();
  } catch {
    return null;
  }
}

// Does this prompt need a lookup? One POST, one judgment. Returns the server's verdict object, or null when there is
// none (timeout, error, a server without the route): the caller then looks the prompt up, as it always did.
export async function callTriage({ url, token, userAgent, query, timeoutMs, fetchImpl = globalThis.fetch }) {
  const target = triageUrl(url);
  if (!target) return null;
  try {
    const res = await fetchImpl(target, {
      method: "POST", signal: AbortSignal.timeout(timeoutMs), body: JSON.stringify({ query: String(query).slice(0, 1500) }),
      headers: { accept: "application/json", "content-type": "application/json", "user-agent": userAgent,
                 ...(token ? { authorization: `Bearer ${token}` } : {}) },
    });
    if (!res.ok) return null;
    const body = JSON.parse(await res.text());
    return body && typeof body === "object" && !Array.isArray(body) ? body : null;
  } catch {
    return null;
  }
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
// One short paragraph for an ok model plan: who does the work first, the fallback, and how to report.
// The plan as instructions. A hook cannot switch the session's own model, so a plan whose first rung is another
// model only saves anything if the agent hands the work over: say so plainly, per rung, with the exact call.
// `mine` is the session's own unit ("model|scaffold"); a rung on it means "do it yourself".
export function planPart(plan, env = process.env, mine = null) {
  if (!plan || plan.status !== "ok" || !Array.isArray(plan.ladder) || !plan.ladder.length) return "";
  const p = (u) => {
    const st = (plan.steps?.[0]?.ladder || []).find((x) => x.unit === u);
    return typeof st?.p_ok_mean === "number" ? ` (p_ok ${st.p_ok_mean.toFixed(2)})` : "";
  };
  const name = (u) => u.split("|")[0];
  const own = (u) => mine && name(u) === name(mine);
  const how = (u) => {
    if (own(u)) return `do it yourself${p(u)}`;
    const alias = claudeAlias(u);
    if (alias) return `call the Agent tool with model: "${alias}" and the whole task${p(u)} -- ${name(u)}`;
    const l = executorFor(u, env);
    if (l) {
      return `call the Agent tool with subagent_type: "${DELEGATOR}", the whole task and the line "Run it with ` +
        `executor=${l.executor} model=${l.model} instance_key=${plan.instance_key}."${p(u)} -- ${name(u)}, an open ` +
        "model; it runs and checks the work in a worktree and reports to the plan itself (no report_result for " +
        "it); apply a verified run with the command its reply gives";
    }
    return `use ${name(u)}${p(u)}`;
  };
  const [first, ...rest] = plan.ladder;
  const lead = own(first) ? `Model plan (${plan.basis || "prior"}): ${how(first)}.`
    : `Model plan (${plan.basis || "prior"}) -- follow it: make the hand-over your FIRST action, before reading ` +
      `files or trying anything yourself: ${how(first)}. Pass the task as given; the delegate reads the files. ` +
      "When it returns, run the check once to confirm -- do not redo its work.";
  return lead +
    (rest.map((u, i) => ` If that fails its check, ${i ? "then " : "next "}${how(u)}.`).join("")) +
    ` After each attempt you ran or delegated to a Claude subagent, check it and call ` +
    `report_result(instance_key="${plan.instance_key}", accepted=<passed?>) -- its reply names the next model if it ` +
    "failed.";
}

export function formatKnowledge(reply, maxChars = 8000, { mode = "full", root = null, mine = null } = {}) {
  if (!reply || typeof reply !== "object" || mode === "off") return "";
  const parts = libraryParts(reply, root, mode, 2000);
  const procs = reply.outcome === "resolved" ? (reply.procedures || []) : [];
  for (const p of procs.slice(0, 2)) {
    const tested = p.tested_by_source ? " [its solution passed the source task's own tests]" : "";
    parts.push(`Known way (verified for this Goal) -- ${p.name || p.goal_name || p.procedure_id}${tested}:${credit(p)}\n${steps(p)}` +
      (p.verified_solution ? `\n${solution(p.verified_solution, 2400)}` : ""));
  }
  if (mode === "lean") {
    // lean (strong session models) keeps the knowledge short, but never drops the model plan: a strong model is
    // exactly where handing work to a cheaper one saves the most
    const leanPlan = planPart(reply.model_plan, process.env, mine);
    if (leanPlan) parts.push(leanPlan);
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
  const plan = planPart(reply.model_plan, process.env, mine);
  if (plan) parts.push(plan);
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
  const model = policy.strongMode || policy.routing ? detectModel(payload, env) : null;
  const mode = deliveryMode(policy, policy.strongMode ? model : null);
  if (mode === "off") return;
  if (policy.triage) {
    const verdict = await callTriage({
      url: settings.url, token: settings.token, userAgent, query: String(payload.prompt).trim(),
      timeoutMs: policy.triageTimeoutMs, fetchImpl,
    });
    if (verdict && verdict.needs_retrieval === false) {
      // No lookup for this prompt. Clear the previous prompt's remembered Goal so the capture hooks
      // (lib/capture_hook.mjs) never report this prompt's outcome against it.
      try { rememberLookup(payload, null, { env }); } catch { /* capture is best-effort */ }
      return;
    }
  }
  try {
    const root = findStealthRoot(payload.cwd || process.cwd());   // null: no .stealth here, a plain lookup
    let extra = {};
    try { if (root) extra = requestPayload(root, { env }); } catch { /* no library: plain lookup */ }
    extra = { ...extra, ...routingArgs(policy, model) };
    const reply = await callFindWays({
      url: settings.url, token: settings.token, userAgent, query: String(payload.prompt).trim(),
      repoClaims: readClaims(payload.cwd, payload.prompt), timeoutMs: policy.timeoutMs, extra, fetchImpl,
    });
    // The route the server chose for this Goal, kept locally so outcomes can be counted against it.
    try {
      if (root && Array.isArray(reply?.routing_rows) && reply.routing_rows.length) upsertRoutes(root, reply.routing_rows);
    } catch { /* routing.md is best-effort */ }
    // For the capture hooks (lib/capture_hook.mjs): which Goal/Procedure this prompt is about. Never fails the hook.
    try { rememberLookup(payload, reply, { env, mine: model ? `${model}|claude-code` : null }); } catch { /* capture is best-effort */ }
    const context = formatKnowledge(reply, policy.maxChars, { mode, root, mine: model ? `${model}|claude-code` : null });
    if (context) {
      write(JSON.stringify({ hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: context } }));
    }
  } catch (err) {
    log(`stealthlab hook: no knowledge added (${err.message})`);   // fail open
  }
}
