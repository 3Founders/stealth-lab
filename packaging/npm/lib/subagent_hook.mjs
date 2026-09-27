// Claude Code SubagentStart / SubagentStop hooks: measure Claude's own subagents the same way the
// local executor layer measures external agents -- by re-running the plan node's own `check=`,
// never by trusting what the subagent said -- and record the outcome with report_model_run
// (scaffold "claude-code-subagent") through the executor layer's outbox.
//
// Payload fields used, verified against https://code.claude.com/docs/en/hooks on 2026-09-27:
//   common:        session_id, cwd, hook_event_name
//   SubagentStart: agent_id, agent_type, task_prompt
//   SubagentStop:  agent_id, agent_type, last_assistant_message  (NO task prompt, NO model, NO worktree path)
// Because SubagentStop carries neither the task nor the worktree, the NODE is found from, in order:
//   1. a `STEALTH_RESULT node=<id> cwd=<path>` line at the end of last_assistant_message (the
//      stealth-executor agent is told to end with it: lib/agents/stealth-executor.md);
//   2. the task_prompt stashed by SubagentStart for the same agent_id ("Do node N-3 ..." or a NODE| line).
// The check runs in the claimed cwd only if it is a worktree of the SAME repository as the session
// (same `git rev-parse --git-common-dir`); otherwise in the session cwd. The model is not in either
// payload, so it is reported as "claude" (spec 6.3).
//
// Never blocks or slows the user: the hook process parses stdin, stashes / hands a minimal job
// (ids, cwd, node id -- never the transcript or the full prompt) to a detached worker and exits 0
// immediately. The worker has a hard 60 s timeout. Every failure is one line in
// ~/.stealthlab/hooks.log (STEALTHLAB_HOME overrides ~/.stealthlab); nothing is ever printed to
// Claude Code, and the exit code is always 0 (exit 2 would block the subagent).
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { configDir } from "./config.mjs";

export const HARD_TIMEOUT_MS = 60_000;
const CHECK_TIMEOUT_S = 50;
const STASH_TTL_MS = 24 * 3600 * 1000;
const LOG_MAX_BYTES = 1024 * 1024;
const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");

// --- logging -----------------------------------------------------------------

export function hookLogPath(env = process.env) {
  return path.join(configDir(env), "hooks.log");
}

export function logHook(env, event, msg) {
  try {
    const file = hookLogPath(env);
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    try { if (fs.statSync(file).size > LOG_MAX_BYTES) fs.renameSync(file, `${file}.1`); } catch { /* none yet */ }
    const line = `${new Date().toISOString()} ${event} ${String(msg).replace(/[\r\n]+/g, " ").slice(0, 500)}\n`;
    fs.appendFileSync(file, line, { mode: 0o600 });
  } catch { /* logging must never fail the hook */ }
}

// --- plan-file parsing (backend/app/mcp_server/prompts.py RUN_MD_FORMAT) -------------------------
// NODE|<node_id>|<status>|<what to do>|step=<P-n>:<order>|claims=<...>|deps=<...>|check=<command>
// check= is the last field and may itself contain "|" (shell pipes), so it is everything after it.

export function parseNodeLine(line) {
  const text = String(line || "").trim();
  if (!text.startsWith("NODE|")) return null;
  const at = text.indexOf("|check=");
  const head = (at >= 0 ? text.slice(0, at) : text).split("|");
  const fields = {};
  for (const part of head.slice(4)) {
    const eq = part.indexOf("=");
    if (eq > 0) fields[part.slice(0, eq).trim()] = part.slice(eq + 1).trim();
  }
  const check = at >= 0 ? text.slice(at + 7).trim() : "";
  return { nodeId: (head[1] || "").trim(), status: (head[2] || "").trim(), what: (head[3] || "").trim(),
           fields, check: check && check !== "-" ? check : "" };
}

export function findNodeLine(text, nodeId) {
  for (const line of String(text || "").split(/\r?\n/)) {
    const n = parseNodeLine(line);
    if (n && n.nodeId === nodeId) return n;
  }
  return null;
}

// PROCEDURE|P-1|<procedure_id>|v<version>|<name>|goal=<goal_name>
export function procedureFor(text, alias) {
  for (const line of String(text || "").split(/\r?\n/)) {
    const p = line.trim().split("|");
    if (p[0] === "PROCEDURE" && p[1] === alias && p[2] && p[2] !== "-") return p[2].trim();
  }
  return null;
}

export function stepRef(step) {
  const m = /^([A-Za-z][\w-]*):(\d+)$/.exec(String(step || "").trim());
  return m ? { alias: m[1], order: Number(m[2]) } : null;
}

const NODE_ID = "[A-Za-z][A-Za-z0-9]*-\\w(?:[\\w.-]*\\w)?";   // N-3, N-3a, N-3.1 -- never a trailing "."

// The last STEALTH_RESULT line of a reply. cwd runs to end of line (Windows paths may hold spaces).
export function parseStealthResult(text) {
  const re = new RegExp(`^\\s*\`?STEALTH_RESULT\\s+node=(${NODE_ID})(?:\\s+cwd=(.+?))?\`?\\s*$`, "gm");
  let m, last = null;
  while ((m = re.exec(String(text || "")))) last = m;
  if (!last) return null;
  return { nodeId: last[1], cwd: last[2] ? last[2].trim().replace(/^["']|["']$/g, "") : null };
}

// From a task prompt: a full NODE| line (with its check), else "Do node N-3" (plan_and_run's one-liner).
export function nodeRefFromPrompt(text) {
  const t = String(text || "");
  for (const line of t.split(/\r?\n/)) {
    const n = parseNodeLine(line);
    if (n?.nodeId) return { nodeId: n.nodeId, nodeLine: line.trim() };
  }
  const m = new RegExp(`\\bnode\\s+(${NODE_ID})`, "i").exec(t);
  return m ? { nodeId: m[1], nodeLine: null } : null;
}

// --- SubagentStart: remember which node this agent_id was given ------------------------------

const safeId = (id) => String(id || "").replace(/[^\w.-]/g, "_").slice(0, 120);

function stashDir(env) {
  return path.join(configDir(env), "hooks", "subagents");
}

function writePrivateJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(obj), { mode: 0o600 });
}

export function onSubagentStart(payload, { env = process.env, now = Date.now } = {}) {
  const dir = stashDir(env);
  try {   // GC stashes of subagents that never stopped
    for (const f of fs.readdirSync(dir)) {
      const p = path.join(dir, f);
      if (now() - fs.statSync(p).mtimeMs > STASH_TTL_MS) fs.rmSync(p, { force: true });
    }
  } catch { /* no dir yet */ }
  const ref = nodeRefFromPrompt(payload.task_prompt);
  if (!payload.agent_id || !ref) return { status: "skipped", reason: "no NODE reference in task_prompt" };
  // Only the node id and its NODE line are kept -- never the rest of the prompt.
  writePrivateJson(path.join(dir, `${safeId(payload.agent_id)}.json`),
                   { agent_id: payload.agent_id, node_id: ref.nodeId, node_line: ref.nodeLine, started_at: now() });
  return { status: "stashed", nodeId: ref.nodeId };
}

function takeStash(env, agentId) {
  if (!agentId) return null;
  const file = path.join(stashDir(env), `${safeId(agentId)}.json`);
  try {
    const obj = JSON.parse(fs.readFileSync(file, "utf8"));
    fs.rmSync(file, { force: true });
    return obj;
  } catch {
    return null;
  }
}

// --- SubagentStop ---------------------------------------------------------------------------

// The minimal job handed to the worker: no transcript, no full prompt, no assistant text.
export function buildStopJob(payload, { env = process.env } = {}) {
  const claimed = parseStealthResult(payload.last_assistant_message);
  const stash = takeStash(env, payload.agent_id);
  return {
    session_id: payload.session_id ? String(payload.session_id) : "",
    agent_id: payload.agent_id ? String(payload.agent_id) : "",
    agent_type: payload.agent_type ? String(payload.agent_type) : "",
    cwd: payload.cwd ? String(payload.cwd) : "",
    node_id: claimed?.nodeId || stash?.node_id || null,
    claimed_cwd: claimed?.cwd || null,
    node_line: stash?.node_id && stash.node_id === (claimed?.nodeId || stash.node_id) ? stash.node_line : null,
    started_at: stash?.started_at || null,
  };
}

export function gitCommonDir(dir) {
  try {
    const r = spawnSync("git", ["-C", dir, "rev-parse", "--git-common-dir"], { encoding: "utf8", timeout: 10_000, windowsHide: true });
    if (r.status !== 0) return null;
    const out = r.stdout.trim();
    if (!out) return null;
    const abs = path.resolve(dir, out);
    let real = abs;
    try { real = fs.realpathSync(abs); } catch { /* keep abs */ }
    return process.platform === "win32" ? real.toLowerCase() : real;
  } catch {
    return null;
  }
}

function isDir(p) {
  try { return fs.statSync(p).isDirectory(); } catch { return false; }
}

function readPlanFile(dirs, name) {
  for (const d of dirs) {
    try { return fs.readFileSync(path.join(d, ".stealth", name), "utf8"); } catch { /* next */ }
  }
  return "";
}

const scrubEnv = (env) => Object.fromEntries(Object.entries(env).filter(([k]) => !/^STEALTHLAB_/i.test(k)));

async function defaultRunChecks(args) {
  return (await import("./exec/verify.mjs")).runChecks(args);
}

async function defaultReportModelRun(payload, opts) {
  return (await import("./exec/evidence.mjs")).reportModelRun(payload, opts);
}

export async function processStopJob(job, {
  env = process.env, runChecks = defaultRunChecks, reportModelRun = defaultReportModelRun,
  commonDir = gitCommonDir, now = Date.now,
} = {}) {
  if (!job.node_id) return { status: "skipped", reason: "no NODE reference (no STEALTH_RESULT line, no stashed task_prompt)" };

  // Where the subagent worked: its claimed cwd, but only if it is a checkout of the same repository.
  let cwd = job.cwd, cwdSource = "session";
  if (job.claimed_cwd && isDir(job.claimed_cwd) && job.cwd) {
    const a = commonDir(job.claimed_cwd), b = commonDir(job.cwd);
    if (a && a === b) { cwd = job.claimed_cwd; cwdSource = "subagent"; }
  }
  if (!cwd || !isDir(cwd)) return { status: "skipped", reason: `no usable cwd (${cwd || "none"})` };

  // .stealth/ is usually untracked, so it lives in the session checkout, not the worktree.
  const dirs = [...new Set([job.cwd, cwd].filter(Boolean))];
  const fromPrompt = job.node_line ? parseNodeLine(job.node_line) : null;
  const node = fromPrompt?.check ? fromPrompt : findNodeLine(readPlanFile(dirs, "run.md"), job.node_id);
  if (!node) return { status: "skipped", reason: `NODE ${job.node_id} not found in .stealth/run.md` };
  if (!node.check) return { status: "skipped", reason: `NODE ${job.node_id} has no check=` };

  const started = now();
  const results = await runChecks({ cwd, checks: [node.check], timeoutS: CHECK_TIMEOUT_S, env: scrubEnv(env) });
  const accepted = Array.isArray(results) && results.length > 0 && results.every((r) => r.exit === 0);
  const exits = (results || []).map((r) => r.exit).join(",");

  const step = stepRef(node.fields.step);
  const procedureId = step ? procedureFor(readPlanFile(dirs, "procedures.md"), step.alias) : null;
  if (!procedureId) {
    // report_model_run refuses without a procedure_id or goal_id, so there is nothing to send.
    return { status: "checked", accepted, exits, cwdSource, reason: `no procedure_id for step=${node.fields.step || "-"}; not reported` };
  }
  const payload = {
    model: "claude",                              // neither hook payload carries the subagent's model
    scaffold: "claude-code-subagent",
    accepted,
    instance_key: `cc-${crypto.createHash("sha256").update(job.session_id || job.agent_id || "").digest("hex").slice(0, 16)}`,
    procedure_id: procedureId,
    step_order: step.order,
    check_kind: "procedure_check",
    attempt_index: 0,
    ...(job.started_at ? { latency_ms: Math.max(0, started - job.started_at) } : {}),
  };
  const evidence = await reportModelRun(payload, { env });
  return { status: "reported", accepted, exits, cwdSource, payload, evidence };
}

// --- entry points ---------------------------------------------------------------------------

export function withHardTimeout(promise, ms = HARD_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve({ status: "timeout", reason: `hard timeout ${ms} ms` }), ms); });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// lib/exec/evidence.mjs reportModelRun -> {reported, queued, held?, rejected?, error?, outbox_file?}
const evidenceWord = (e) => (e.reported ? "reported" : e.queued ? "queued" : e.held ? "held" : e.rejected ? "rejected" : "unknown");

function describe(event, job, r) {
  const who = `agent=${job?.agent_type || "?"} node=${job?.node_id || "-"}`;
  const extra = [r.accepted !== undefined ? `accepted=${r.accepted}` : "", r.exits ? `exit=${r.exits}` : "",
                 r.cwdSource ? `cwd=${r.cwdSource}` : "", r.reason || "",
                 r.evidence && typeof r.evidence === "object" ? `evidence=${evidenceWord(r.evidence)}` : ""];
  return `${who} ${r.status} ${extra.filter(Boolean).join(" ")}`.trim();
}

function spawnDetachedWorker(file, env) {
  const child = spawn(process.execPath, [BIN, "hook", "subagent-stop"], {
    detached: true, stdio: "ignore", windowsHide: true, env: { ...env, STEALTHLAB_HOOK_WORKER_FILE: file },
  });
  child.unref();
}

// What `stealthlab-mcp hook <event>` runs. Never throws; the caller always exits 0.
// detach=false runs the check in-process (tests, and STEALTHLAB_HOOK_SYNC=1).
export async function runSubagentHook(event, {
  stdinText = "", env = process.env, detach = env.STEALTHLAB_HOOK_SYNC !== "1", spawnWorker = spawnDetachedWorker, ...deps
} = {}) {
  let job;
  try {
    if (event !== "subagent-start" && event !== "subagent-stop") throw new Error(`unknown hook event "${event}"`);
    let payload;
    try {
      payload = JSON.parse(stdinText || "");
    } catch (err) {
      throw new Error(`malformed hook payload (${err.message})`);
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("malformed hook payload (not an object)");

    if (event === "subagent-start") {
      const r = onSubagentStart(payload, { env });
      if (r.status !== "skipped") logHook(env, event, `agent=${payload.agent_type || "?"} node=${r.nodeId} stashed`);
      return r;
    }
    job = buildStopJob(payload, { env });
    if (!job.node_id) return { status: "skipped", reason: "no NODE reference" };   // ordinary subagent: stay silent
    if (detach) {
      const file = path.join(configDir(env), "hooks", "jobs", `${safeId(job.agent_id) || "job"}-${Date.now()}.json`);
      writePrivateJson(file, job);
      spawnWorker(file, env);
      return { status: "detached", file };
    }
    const r = await withHardTimeout(processStopJob(job, { env, ...deps }), deps.timeoutMs);
    logHook(env, event, describe(event, job, r));
    return r;
  } catch (err) {
    logHook(env, event || "hook", `error: ${err.message}`);
    return { status: "error", reason: err.message };
  }
}

// The detached worker: `stealthlab-mcp hook subagent-stop` with STEALTHLAB_HOOK_WORKER_FILE set.
export async function runStopWorker(file, { env = process.env, ...deps } = {}) {
  let job;
  try {
    job = JSON.parse(fs.readFileSync(file, "utf8"));
    fs.rmSync(file, { force: true });
    const r = await withHardTimeout(processStopJob(job, { env, ...deps }), deps.timeoutMs);
    logHook(env, "subagent-stop", describe("subagent-stop", job, r));
    return r;
  } catch (err) {
    try { fs.rmSync(file, { force: true }); } catch { /* gone */ }
    logHook(env, "subagent-stop", `${job ? `node=${job.node_id} ` : ""}error: ${err.message}`);
    return { status: "error", reason: err.message };
  }
}
