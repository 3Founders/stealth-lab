// Continuity across a dispatched prompt: tell the session what a cheap model did for it.
//
// A dispatched prompt is BLOCKED (lib/dispatch.mjs): the session's model never sees it, so on the next prompt it knows
// nothing of the change already sitting in the working tree. "Earlier in this session, <model> changed <files> to
// <task> (kell, check passed)" is added to the next prompt's context, one line per earlier dispatch.
//
// Kept per session, under the kell home (hooks/continuity/<session_id>.json): the last 5 dispatches, each marked
// `shown` once it has been put in a prompt's context -- the session's own transcript carries it from then on, so it is
// said once, not on every later prompt. Swept after 24 h. Never leaves the machine. STEALTHLAB_CONTINUITY=off turns it off.
//
// Each dispatch also keeps the request and the reply the developer saw (dispatchedTurns): the session's transcript may
// not hold a blocked turn, and the server's judge and the cheap models read the conversation from it. And the time each
// changed file was written (cheapWritten): a follow-up on a change a cheap model made, with the file still as it left
// it, may go to the cheap models again; a file the session or the developer changed after it may not.
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.mjs";

export const KEEP = 5;                        // dispatches remembered per session
const FILES_SHOWN = 5;
const TASK_MAX = 160;
const TURN_MAX = 700;                         // the request and the reply kept per dispatch (as lib/brief.mjs keeps a turn)
const TTL_MS = 24 * 3600 * 1000;

export const continuityEnabled = (env = process.env) => (env.STEALTHLAB_CONTINUITY || "on").toLowerCase() !== "off";

function dir(env) {
  return path.join(configDir(env), "hooks", "continuity");
}

export function continuityFile(env, sessionId) {
  const id = String(sessionId || "");
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) return null;
  return path.join(dir(env), `${id}.json`);
}

function read(file) {
  try {
    const o = JSON.parse(fs.readFileSync(file, "utf8"));
    return Array.isArray(o?.entries) ? o.entries : [];
  } catch {
    return [];
  }
}

function write(file, entries) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify({ entries }), { mode: 0o600 });
}

function oneLine(text, max) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 3)}...` : t;
}

// The line for one dispatch. The model name, the files it changed (at most 5, then "and N more") and the task.
export function continuityLine(e) {
  if (e.kind === "answer") return `Earlier in this session, ${e.model} answered "${oneLine(e.task, TASK_MAX)}" (kell, checked by a reviewer).`;
  const files = (e.files || []).map(String);
  const shown = files.slice(0, FILES_SHOWN).join(", ") || "no files";
  const more = files.length > FILES_SHOWN ? ` and ${files.length - FILES_SHOWN} more` : "";
  return `Earlier in this session, ${e.model} changed ${shown}${more} to ${oneLine(e.task, TASK_MAX) || "do the task"} (kell, check passed).`;
}

// After a verified cheap answer was applied. Never throws: remembering must not fail the hook.
export function recordDispatch({ sessionId, model, files = [], task = "", prompt = "", text = "", root = null, kind = "change" },
                               { env = process.env, now = Date.now() } = {}) {
  try {
    if (!continuityEnabled(env)) return false;
    const file = continuityFile(env, sessionId);
    if (!file || !model) return false;
    const written = {};
    for (const f of files.map(String)) {
      try { if (root) written[f] = fs.statSync(path.join(root, f)).mtimeMs; } catch { /* deleted: nothing to compare */ }
    }
    const entries = [...read(file), { at: now, model: String(model), files: files.map(String), task: oneLine(task, TASK_MAX), shown: false,
                                      prompt: String(prompt || "").slice(0, TURN_MAX), text: String(text || "").slice(0, TURN_MAX), written,
                                      ...(kind === "answer" ? { kind } : {}) }];
    write(file, entries.slice(-KEEP));
    sweep(env, now);
    return true;
  } catch {
    return false;
  }
}

// The dispatched turns of this session as conversation turns, oldest first: [{at, role, text}].
export function dispatchedTurns(sessionId, { env = process.env } = {}) {
  try {
    if (!continuityEnabled(env)) return [];
    const file = continuityFile(env, sessionId);
    if (!file) return [];
    return read(file).filter((e) => e.prompt).flatMap((e) => [{ at: e.at, role: "user", text: e.prompt },
                                                               ...(e.text ? [{ at: e.at + 1, role: "assistant", text: e.text }] : [])]);
  } catch {
    return [];
  }
}

// The files a cheap model changed in this session that are still exactly as it left them (not written since).
export function cheapWritten(sessionId, root, { env = process.env } = {}) {
  const out = new Set();
  try {
    if (!continuityEnabled(env) || !root) return out;
    const file = continuityFile(env, sessionId);
    if (!file) return out;
    for (const e of read(file)) {
      for (const [f, t] of Object.entries(e.written || {})) {
        try { if (Math.abs(fs.statSync(path.join(root, f)).mtimeMs - t) < 1) out.add(f); else out.delete(f); } catch { out.delete(f); }
      }
    }
  } catch { /* none */ }
  return out;
}

// The lines not yet put in a prompt's context for this session: [] when there are none.
export function pendingLines(sessionId, { env = process.env } = {}) {
  try {
    if (!continuityEnabled(env)) return [];
    const file = continuityFile(env, sessionId);
    return file ? read(file).filter((e) => !e.shown).map(continuityLine) : [];
  } catch {
    return [];
  }
}

// They were delivered: say them once.
export function markShown(sessionId, { env = process.env } = {}) {
  try {
    const file = continuityFile(env, sessionId);
    if (!file) return;
    write(file, read(file).map((e) => ({ ...e, shown: true })));
  } catch { /* best effort */ }
}

function sweep(env, now) {
  try {
    for (const f of fs.readdirSync(dir(env))) {
      const p = path.join(dir(env), f);
      if (now - fs.statSync(p).mtimeMs > TTL_MS) fs.rmSync(p, { force: true });
    }
  } catch { /* nothing to sweep */ }
}
