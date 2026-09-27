// Shared plumbing for the executor adapters: finding an agent's binary on PATH
// (Windows .cmd shim aware), launching it WITHOUT a shell, reading its version,
// and the verification gate that makes an unverified adapter refuse to run.
//
// Terms-of-service boundary (spec constraint 5): an adapter only ever launches a
// binary the user installed on this machine, found on the user's own PATH, and
// the child inherits the user's own HOME / config dirs so it uses the user's own
// login. Nothing here reads, copies, forwards or pools an agent's credentials,
// and nothing here talks to the network. The only env we touch is removing
// STEALTHLAB_* variables (our tokens must never reach a third-party agent).
//
// Why no shell, ever: the task string is untrusted (it comes from the
// orchestrator / a procedure), and on Windows spawning a `.cmd` shim requires
// `shell: true` (Node >= 18.20.2 refuses otherwise, CVE-2024-27980), which would
// route the task through cmd.exe's parser. Instead we read the npm-generated
// shim, find the real target it launches (an .exe, or a JS entry point run by
// node), and spawn that directly with an argv array. A shim we cannot parse is
// reported as not launchable, and the adapter refuses; we never fall back to a shell.
import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";

export const VERIFIED_DATE = "2026-09-27";

// --- PATH lookup --------------------------------------------------------------

function pathVar(env, platform) {
  if (platform !== "win32") return env.PATH || "";
  const key = Object.keys(env).find((k) => k.toUpperCase() === "PATH");
  return key ? env[key] || "" : "";
}

function pathExts(env) {
  const key = Object.keys(env).find((k) => k.toUpperCase() === "PATHEXT");
  const raw = (key && env[key]) || ".COM;.EXE;.BAT;.CMD";
  return raw.split(";").map((e) => e.trim().toLowerCase()).filter(Boolean);
}

function isFile(p) {
  try { return fs.statSync(p).isFile(); } catch { return false; }
}

function isExecutablePosix(p) {
  try { fs.accessSync(p, fs.constants.X_OK); return true; } catch { return false; }
}

// Returns { path, kind } for the first match of `name` on PATH, or null.
// kind: "exe" (spawn directly), "cmd" (a .cmd/.bat shim: must be unwrapped), or
// "posix" (an executable file on macOS/Linux).
export function resolveBin(name, { env = process.env, platform = process.platform } = {}) {
  const P = platform === "win32" ? path.win32 : path.posix;
  const dirs = pathVar(env, platform).split(platform === "win32" ? ";" : ":").filter(Boolean);
  for (const dir of dirs) {
    if (platform === "win32") {
      // PATHEXT order, like cmd.exe. The extensionless file npm also drops in
      // that dir is a POSIX sh script and cannot be launched on Windows.
      for (const ext of pathExts(env)) {
        const p = P.join(dir, name + ext);
        if (isFile(p)) return { path: p, kind: ext === ".cmd" || ext === ".bat" ? "cmd" : ext === ".exe" || ext === ".com" ? "exe" : "other" };
      }
    } else {
      const p = P.join(dir, name);
      if (isFile(p) && isExecutablePosix(p)) return { path: p, kind: "posix" };
    }
  }
  return null;
}

// npm cmd-shim format (verified on this machine against opencode.cmd, codex.cmd
// and cline.cmd written by npm): the launch line references the target as
// "%dp0%\<relative path>" followed by %*. A node-script shim also mentions
// "%dp0%\node.exe" in an IF EXIST guard; that is the interpreter, not the target.
export function parseCmdShim(text, shimPath) {
  const dir = path.win32.dirname(shimPath);
  const refs = [...text.matchAll(/"%dp0%\\([^"%]+)"/gi)].map((m) => m[1]);
  const targets = refs.filter((r) => !/^node\.exe$/i.test(r));
  if (!targets.length) return null;
  const rel = targets[targets.length - 1];
  const target = path.win32.join(dir, rel);
  const localNode = path.win32.join(dir, "node.exe");
  if (/\.(exe|com)$/i.test(rel)) return { cmd: target, prefix: [] };
  if (/\.(cmd|bat|ps1)$/i.test(rel)) return null; // shim of a shim: not unwrapped, refuse
  return { cmd: isFile(localNode) ? localNode : process.execPath, prefix: [target] };
}

// How to spawn a resolved binary without a shell: { cmd, prefix } or null.
export function launchFor(resolved) {
  if (!resolved) return null;
  if (resolved.kind === "exe" || resolved.kind === "posix") return { cmd: resolved.path, prefix: [] };
  if (resolved.kind === "cmd") {
    let text;
    try { text = fs.readFileSync(resolved.path, "utf8"); } catch { return null; }
    return parseCmdShim(text, resolved.path);
  }
  return null;
}

// Resolve + unwrap in one step; throws with an actionable message.
export function launchOrThrow(binName, { env = process.env, platform = process.platform, bin } = {}) {
  const resolved = bin ? classify(bin, platform) : resolveBin(binName, { env, platform });
  if (!resolved) throw new Error(`${binName} is not installed (not found on PATH)`);
  const launch = launchFor(resolved);
  if (!launch) throw new Error(`${resolved.path}: unrecognised launcher shim; refusing to run it through a shell`);
  return launch;
}

function classify(p, platform) {
  if (platform !== "win32") return { path: p, kind: "posix" };
  const ext = path.win32.extname(p).toLowerCase();
  return { path: p, kind: ext === ".cmd" || ext === ".bat" ? "cmd" : ext === ".exe" || ext === ".com" ? "exe" : "other" };
}

// --- version / detect -----------------------------------------------------------

export function parseVersion(text) {
  const m = String(text || "").match(/(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?/);
  return m ? m[0] : null;
}

export function majorOf(version) {
  const m = String(version || "").match(/^(\d+)\./);
  return m ? Number(m[1]) : null;
}

function run(cmd, args, { env, timeoutMs = 15000, cwd } = {}) {
  return new Promise((resolve) => {
    execFile(cmd, args, { env, timeout: timeoutMs, windowsHide: true, encoding: "utf8", cwd, maxBuffer: 1 << 20 },
      (err, stdout, stderr) => resolve({ ok: !err, code: err ? err.code : 0, timedOut: !!(err && err.killed), out: `${stdout || ""}${stderr || ""}` }));
  });
}

// detect(): is the agent on PATH, and what version does it report?
// `<bin> --version` is the only thing ever run here: no task, no tokens, no login.
export async function detectBin(binName, { env = process.env, platform = process.platform, versionArgs = ["--version"], timeoutMs = 15000 } = {}) {
  const resolved = resolveBin(binName, { env, platform });
  if (!resolved) return { installed: false, version: null, bin: null };
  const launch = launchFor(resolved);
  if (!launch) return { installed: true, version: null, bin: resolved.path, detail: "unrecognised launcher shim; not launchable without a shell" };
  const r = await run(launch.cmd, [...launch.prefix, ...versionArgs], { env: scrubEnv(env), timeoutMs });
  return {
    installed: true,
    version: parseVersion(r.out),
    bin: resolved.path,
    ...(r.timedOut ? { detail: `--version timed out after ${timeoutMs} ms` } : {}),
  };
}

// --- verification gate ------------------------------------------------------------

// The single rule the runtime must apply before spawning an adapter (spec
// constraint 6 / section 8 item 10): VERIFIED_WITH present, and the installed
// major version equals the verified one. Flags change across majors.
export function runnable(adapter, detected) {
  if (!adapter || !adapter.VERIFIED_WITH) {
    return { ok: false, reason: `${adapter && adapter.id} adapter is unverified: its headless flags were taken from docs (${adapter && adapter.DOCS_SOURCE || "no source"}) and never checked against an installed binary, so it refuses to run` };
  }
  if (!detected || !detected.installed) return { ok: false, reason: `${adapter.id} is not installed` };
  if (!detected.version) return { ok: false, reason: `${adapter.id}: could not read the installed version, so the verified flags cannot be trusted` };
  const want = majorOf(adapter.VERIFIED_WITH.version);
  const have = majorOf(detected.version);
  if (want === null || have === null || want !== have) {
    return { ok: false, reason: `${adapter.id}: installed ${detected.version} differs in major version from the verified ${adapter.VERIFIED_WITH.version}; re-verify the flags before running` };
  }
  return { ok: true, reason: null };
}

export function assertRunnable(adapter, detected) {
  const r = runnable(adapter, detected);
  if (!r.ok) throw new Error(r.reason);
}

export async function healthOf(adapter, { env = process.env } = {}) {
  const d = await adapter.detect({ env });
  const r = runnable(adapter, d);
  if (!r.ok) return { healthy: false, detail: d.detail ? `${r.reason} (${d.detail})` : r.reason };
  return { healthy: true, detail: `${adapter.id} ${d.version} at ${d.bin}` };
}

// --- env / task hygiene -----------------------------------------------------------

// Remove every STEALTHLAB_* variable (tokens, URLs, test seams). PATH, HOME,
// APPDATA, XDG_* and the agents' own config vars are inherited on purpose: the
// agent must run under the user's own login.
export function scrubEnv(env = process.env) {
  const out = {};
  for (const [k, v] of Object.entries(env)) if (!/^STEALTHLAB_/i.test(k) && v !== undefined) out[k] = v;
  return out;
}

// A task is passed as a single argv element (never through a shell). The one
// remaining parse risk is a task that starts with "-", which an agent's option
// parser would read as a flag.
export function safeTaskArg(task) {
  const t = String(task ?? "").trim();
  if (!t) throw new Error("task is empty");
  return t.startsWith("-") ? `Task: ${t}` : t;
}

// --- output parsing helpers ---------------------------------------------------------

export function jsonLines(text) {
  const out = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const s = line.trim();
    if (!s.startsWith("{")) continue;
    try { const o = JSON.parse(s); if (o && typeof o === "object") out.push(o); } catch { /* not JSON: skip */ }
  }
  return out;
}

// The last JSON object in the text: the whole text if it parses, else the last line that does.
export function lastJsonObject(text) {
  const s = String(text || "").trim();
  if (s.startsWith("{")) { try { return JSON.parse(s); } catch { /* fall through */ } }
  const lines = jsonLines(s);
  return lines.length ? lines[lines.length - 1] : null;
}

const MAX_FINAL = 4000; // the runtime trims to 1,200 and redacts; we only bound memory here

export function tailText(text, n = MAX_FINAL) {
  const s = String(text || "").trim();
  return s.length > n ? s.slice(s.length - n) : s;
}

// The executor contract (plan_and_run prompt) asks the agent to reply with
// "result, proof, anything you learned". Pull up to 5 items from a
// "learned"/"surprises" section of the final message. Heuristic by design:
// no section -> [] (never invented).
export function extractLearned(text, max = 5) {
  const lines = String(text || "").split(/\r?\n/);
  const header = /^\s*(?:#+\s*)?(?:[-*]\s*)?(?:\*\*|__)?\s*(?:anything\s+(?:you\s+|i\s+)?learned|what\s+i\s+learned|learned|lessons?(?:\s+learned)?|surprises?(?:\s*\/\s*fixes)?)\s*(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*(.*)$/i;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(header);
    if (!m) continue;
    const items = [];
    const inline = m[1].trim();
    if (inline && !/^none\.?$/i.test(inline)) items.push(inline);
    for (let j = i + 1; j < lines.length && items.length < max; j++) {
      const l = lines[j];
      const b = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (b) { if (b[1].trim()) items.push(b[1].trim()); continue; }
      if (!l.trim()) { if (items.length) break; continue; }
      if (/^\s*#/.test(l) || /^\s*\*\*[^*]+\*\*\s*:?/.test(l)) break;
      if (!items.length || inline) { items.push(l.trim()); continue; }
      break;
    }
    return items.slice(0, max).map((s) => (s.length > 300 ? s.slice(0, 300) : s));
  }
  return [];
}

export function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function plainResult(stdout, stderr) {
  const finalMessage = tailText(stdout) || tailText(stderr);
  return { finalMessage, learned: extractLearned(finalMessage), tokens: null, costUsd: null };
}
