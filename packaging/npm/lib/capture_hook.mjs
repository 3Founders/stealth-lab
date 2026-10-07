// Claude Code capture hooks: the learning side of the knowledge hook (lib/hook.mjs).
//
// Why: reading knowledge is automatic now (the UserPromptSubmit hook), but Kel only learns which ways work when
// an agent calls report_model_run -- and DS-1000 round 4 showed models do not reliably call tools. So the WHEN
// of reporting is decided here too, deterministically:
//   1. UserPromptSubmit (lib/hook.mjs) remembers, per session, the Goal and Procedure its find_ways lookup
//      identified for this prompt (resolved way, or the suggested candidate). No lookup identity -> nothing
//      is ever reported for that prompt.
//   2. PostToolUse on Bash (`hook capture-tool`): when the command looks like a test run, the verdict is read
//      from the runner's own summary line (pytest, unittest, jest/vitest, mocha, go test, cargo test). Claude
//      Code gives a hook the output but no exit code (checked against a real transcript, 2026-09-28), so an
//      unrecognised summary is "unknown" and never counts.
//   3. Stop (`hook capture-stop`): at the end of the turn, if the last test verdict after the lookup is known,
//      ONE report_model_run is sent per prompt through the executor layer's outbox (lib/exec/evidence.mjs), by
//      a detached worker, so the user never waits. The model comes from the transcript (no hook payload
//      carries it); scaffold "claude-code"; check_kind "tests".
//
// Privacy: what leaves the machine is exactly evidence.mjs REPORT_FIELDS -- model, scaffold, accepted, ids,
// check kind. Never the prompt, commands, test output, diffs or the transcript. Locally only ids, verdicts and
// timestamps are kept (~/.stealthlab/hooks/sessions/, deleted after 24 h).
// Locally, too (no token needed, nothing sent): a RESOLVED lookup's final test verdict is counted as one OBS
// attempt on that Goal's route in the repo's .stealth/routing.md (lib/library.mjs) -- the counts find_ways'
// route_obs sends back so the next model plan reflects what worked in THIS repo. Only when .stealth/ exists.
// Off unless a token is saved (a report needs one; without it nothing is queued). STEALTHLAB_CAPTURE=off
// disables it. Every hook exits 0 and prints nothing: exit 2 on Stop would keep Claude from stopping.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { configDir } from "./config.mjs";
import { logHook } from "./subagent_hook.mjs";
import { ensureRoute, findStealthRoot, recordObs } from "./library.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");
const SESSION_TTL_MS = 24 * 3600 * 1000;
const MAX_TESTS = 20;
const TRANSCRIPT_TAIL_BYTES = 512 * 1024;
export const WORKER_TIMEOUT_MS = 30_000;

// settings.json entries this package owns (event -> {matcher?, command suffix}); see installCaptureHooks.
export const CAPTURE_HOOKS = {
  PostToolUse: { matcher: "Bash", mark: "hook capture-tool" },
  Stop: { mark: "hook capture-stop" },
};

export function captureEnabled(env = process.env) {
  return (env.STEALTHLAB_CAPTURE || "on").toLowerCase() !== "off";
}

// --- session state -------------------------------------------------------------------------------

function sessionsDir(env) {
  return path.join(configDir(env), "hooks", "sessions");
}

export function sessionFile(env, sessionId) {
  const id = String(sessionId || "");
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) return null;
  return path.join(sessionsDir(env), `${id}.json`);
}

function readSession(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
}

function writeSession(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(obj), { mode: 0o600 });
}

function sweep(env, now) {
  try {
    for (const f of fs.readdirSync(sessionsDir(env))) {
      const p = path.join(sessionsDir(env), f);
      if (now - fs.statSync(p).mtimeMs > SESSION_TTL_MS) fs.rmSync(p, { force: true });
    }
  } catch { /* nothing to sweep */ }
}

// The Goal/Procedure a find_ways reply identified, or null: a resolved way, else the suggested candidate.
// Related examples are NOT an identity (they are other Goals, "not verified to apply").
export function lookupIdentity(reply) {
  if (!reply || typeof reply !== "object") return null;
  if (reply.outcome === "resolved") {
    const p = (reply.procedures || []).find((x) => x?.procedure_id);
    const goal = p?.goal_id || reply.goal?.goal_id || reply.goal?.id;
    if (p || goal) return { outcome: "resolved", goal_id: goal || null, procedure_id: p?.procedure_id || null };
  }
  const s = reply.suggested;
  if (reply.outcome === "ambiguous" && s && (s.goal_id || s.procedure_id)) {
    return { outcome: "suggested", goal_id: s.goal_id || null, procedure_id: s.procedure_id || null };
  }
  return null;
}

// Called by the UserPromptSubmit hook after its lookup. Every prompt starts a fresh record: one report per prompt.
export function rememberLookup(payload, reply, { env = process.env, now = Date.now() } = {}) {
  if (!captureEnabled(env)) return false;
  const file = sessionFile(env, payload?.session_id);
  if (!file) return false;
  sweep(env, now);
  const identity = lookupIdentity(reply);
  if (!identity) {
    fs.rmSync(file, { force: true });   // a new prompt without identity must not inherit the last one's
    return false;
  }
  const route = /^ROUTE\|(R-[0-9a-f]{4,16})\|/.exec(reply?.routing_rows?.[0] || "")?.[1] || null;
  writeSession(file, {
    prompt_key: String(payload.prompt_id || now), at: now, lookup: identity, tests: [], reported: false,
    cwd: typeof payload.cwd === "string" ? findStealthRoot(payload.cwd) : null, route,
  });
  return true;
}

// --- test runs -----------------------------------------------------------------------------------

// Superset of backend/app/services/observations.py _TEST_COMMAND_MARKERS.
const TEST_MARKERS = ["pytest", "npm test", "npm run test", "go test", "cargo test", "jest", "vitest", "mocha",
  "yarn test", "pnpm test", "python -m unittest", "tox", "phpunit", "rspec", "mvn test", "gradle test"];

export function looksLikeTest(command) {
  const c = String(command || "").toLowerCase();
  return TEST_MARKERS.some((m) => c.includes(m));
}

// true / false from the runner's own summary; null when no known summary is found. Failure signals first.
export function testVerdict(output) {
  const t = String(output || "");
  const failed = [
    /\b\d+ (failed|errors?)\b[^\n]* in [\d.]+s\b/,     // pytest summary, with or without -q's missing ===
    /^FAILED \(/m,                                      // unittest
    /^Tests:\s.*\b\d+ failed\b/m,                      // jest / vitest
    /\b\d+ failing\b/,                                  // mocha
    /^(---\s)?FAIL\b/m,                                 // go test
    /^test result: FAILED\b/m,                          // cargo test
  ];
  if (failed.some((re) => re.test(t))) return false;
  const passed = [
    /\b\d+ passed\b[^\n]* in [\d.]+s\b/,               // pytest
    /^Ran \d+ tests? in [\d.]+s\s+OK\b/m,              // unittest
    /^Tests:\s.*\b\d+ passed\b/m,                      // jest / vitest
    /\b\d+ passing\b/,                                  // mocha
    /^ok\s+\S+/m,                                       // go test
    /^test result: ok\b/m,                              // cargo test
  ];
  return passed.some((re) => re.test(t)) ? true : null;
}

function toolOutput(resp) {
  if (!resp || typeof resp !== "object") return String(resp || "");
  if (typeof resp.text === "string") return resp.text;
  return `${resp.stdout || ""}\n${resp.stderr || ""}`;
}

export function onPostToolUse(payload, { env = process.env, now = Date.now() } = {}) {
  if (!captureEnabled(env) || payload?.tool_name !== "Bash") return null;
  if (!looksLikeTest(payload.tool_input?.command)) return null;
  const file = sessionFile(env, payload.session_id);
  const s = file && readSession(file);
  if (!s || s.reported) return null;
  const verdict = testVerdict(toolOutput(payload.tool_response));
  s.tests = [...(s.tests || []), { verdict, at: now }].slice(-MAX_TESTS);
  writeSession(file, s);
  return verdict;
}

// --- the report ----------------------------------------------------------------------------------

// The model of the last assistant message, from the tail of the transcript (read locally, never sent).
export function modelFromTranscript(file) {
  try {
    const fd = fs.openSync(file, "r");
    try {
      const size = fs.fstatSync(fd).size;
      const len = Math.min(size, TRANSCRIPT_TAIL_BYTES);
      const buf = Buffer.alloc(len);
      fs.readSync(fd, buf, 0, len, size - len);
      const lines = buf.toString("utf8").split(/\r?\n/).reverse();
      for (const line of lines) {
        if (!line.includes('"model"')) continue;
        try {
          const m = JSON.parse(line)?.message?.model;
          if (typeof m === "string" && m && m !== "<synthetic>") return m;
        } catch { /* a partial first line */ }
      }
    } finally {
      fs.closeSync(fd);
    }
  } catch { /* no transcript */ }
  return null;
}

export function buildReport(session, { sessionId, model }) {
  if (!session || session.reported || !session.lookup || !model) return null;
  const last = [...(session.tests || [])].reverse().find((x) => x.verdict !== null && x.verdict !== undefined);
  if (!last) return null;
  const key = crypto.createHash("sha256").update(`${sessionId}|${session.prompt_key}`).digest("hex").slice(0, 24);
  return {
    model, scaffold: "claude-code", accepted: last.verdict === true, instance_key: `cc-${key}`,
    goal_id: session.lookup.goal_id || undefined, procedure_id: session.lookup.procedure_id || undefined,
    check_kind: "tests", attempt_index: 0,
  };
}

function jobsDir(env) {
  return path.join(configDir(env), "hooks", "jobs");
}

function spawnDetachedWorker(file, env) {
  const child = spawn(process.execPath, [BIN, "hook", "capture-stop"], {
    detached: true, stdio: "ignore", windowsHide: true, env: { ...env, STEALTHLAB_CAPTURE_JOB: file },
  });
  child.unref();
}

// Stop: decide in milliseconds, hand the network call to a detached worker, never print anything.
// The local half: one OBS count in .stealth/routing.md per resolved prompt with a known verdict and model.
export function recordLocalObs(s, model, { now = new Date() } = {}) {
  if (!s || s.obs_recorded || s.lookup?.outcome !== "resolved" || !s.lookup.goal_id || !model || !s.cwd) return false;
  const last = [...(s.tests || [])].reverse().find((x) => x.verdict !== null && x.verdict !== undefined);
  if (!last || !fs.existsSync(path.join(s.cwd, ".stealth"))) return false;
  const route = s.route || ensureRoute(s.cwd, s.lookup.goal_id, { now });
  recordObs(s.cwd, route, model, "claude-code", last.verdict === true, { now });
  s.obs_recorded = true;
  return true;
}

export function onStop(payload, { env = process.env, hasToken, spawnWorker = spawnDetachedWorker } = {}) {
  if (!captureEnabled(env)) return { status: "disabled" };
  const file = sessionFile(env, payload?.session_id);
  const s = file && readSession(file);
  const model = s ? modelFromTranscript(payload.transcript_path) : null;
  try {
    if (s && recordLocalObs(s, model)) writeSession(file, s);
  } catch (err) {
    logHook(env, "capture-stop", `local OBS not recorded (${err.message})`);
  }
  if (!hasToken) return { status: "no-token" };
  if (!s) return { status: "no-lookup" };
  const report = buildReport(s, { sessionId: payload.session_id, model });
  if (!report) return { status: "nothing-to-report" };
  s.reported = true;
  writeSession(file, s);
  const job = path.join(jobsDir(env), `${Date.now()}-${crypto.randomUUID()}.json`);
  fs.mkdirSync(path.dirname(job), { recursive: true, mode: 0o700 });
  fs.writeFileSync(job, JSON.stringify({ report }), { mode: 0o600 });
  spawnWorker(job, env);
  return { status: "detached", report };
}

// The detached worker: `stealthlab-mcp hook capture-stop` with STEALTHLAB_CAPTURE_JOB set.
export async function runCaptureWorker(file, { env = process.env, report } = {}) {
  let job;
  try {
    job = JSON.parse(fs.readFileSync(file, "utf8"));
  } finally {
    fs.rmSync(file, { force: true });
  }
  const send = report || (await import("./exec/evidence.mjs")).reportModelRun;
  const timer = new Promise((_, rej) => setTimeout(() => rej(new Error("capture worker timed out")), WORKER_TIMEOUT_MS).unref());
  const r = await Promise.race([send(job.report, { env }), timer]);
  logHook(env, "capture-stop", `reported=${Boolean(r?.reported)} queued=${Boolean(r?.queued)} accepted=${job.report.accepted}`);
  return r;
}

// CLI entry for `hook capture-tool` / `hook capture-stop` (payload on stdin). Never throws, never prints.
export async function runCaptureHook(event, { stdinText, env = process.env, hasToken } = {}) {
  let payload;
  try {
    payload = JSON.parse(stdinText || "{}");
  } catch {
    logHook(env, event, "malformed payload");
    return { status: "malformed" };
  }
  try {
    if (event === "capture-tool") return { status: "ok", verdict: onPostToolUse(payload, { env }) };
    if (event === "capture-stop") return onStop(payload, { env, hasToken });
  } catch (err) {
    logHook(env, event, err.message);
  }
  return { status: "error" };
}

// --- settings.json -------------------------------------------------------------------------------

const isOursFor = (mark) => (group) => (group?.hooks || []).some((h) => {
  const cmd = String(h?.command || "").trimEnd();
  return cmd.includes("stealthlab-mcp") && cmd.endsWith(mark);
});

function shellJoin(spec) {
  return [spec.command, ...spec.args].map((a) => (/[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a)).join(" ");
}

// Adds (or refreshes) our PostToolUse/Stop entries inside an already-parsed settings document; the caller
// reads and writes the file. Nothing else in `doc` is touched.
export function addCaptureHooks(doc, launch, timeoutSec = 15) {
  doc.hooks = doc.hooks || {};
  for (const [event, { matcher, mark }] of Object.entries(CAPTURE_HOOKS)) {
    const groups = (doc.hooks[event] || []).filter((g) => !isOursFor(mark)(g));
    const command = shellJoin({ command: launch.command, args: [...launch.args, ...mark.split(" ")] });
    groups.push({ ...(matcher ? { matcher } : {}), hooks: [{ type: "command", command, timeout: timeoutSec }] });
    doc.hooks[event] = groups;
  }
  return doc;
}

export function removeCaptureHooks(doc) {
  if (!doc?.hooks || typeof doc.hooks !== "object") return false;
  let changed = false;
  for (const [event, { mark }] of Object.entries(CAPTURE_HOOKS)) {
    const before = doc.hooks[event];
    if (!Array.isArray(before)) continue;
    const after = before.filter((g) => !isOursFor(mark)(g));
    if (after.length === before.length) continue;
    changed = true;
    if (after.length) doc.hooks[event] = after;
    else delete doc.hooks[event];
  }
  if (changed && !Object.keys(doc.hooks).length) delete doc.hooks;
  return changed;
}
