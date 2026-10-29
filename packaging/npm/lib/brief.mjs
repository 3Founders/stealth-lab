// The task brief: one cheap call (STEALTHLAB_BRIEF_MODEL, an exec.json profile; default gemma-4-31b-it) that turns the
// last few turns plus the prompt into
//   { task, files_hinted, acceptance, parts[], clarity, kind, size, search_terms }
// Dispatch uses only search_terms (names to search the code for: a plain-language issue names nothing the code
// spells) and parts (a request that asks for several things). It decides nothing: whether a prompt goes to the cheap
// models at all is the server's judge's call (JEV first, POST /triage, lib/dispatch.mjs policy.gate), and the cheap
// models are told the conversation and the request as the user said it (conversationPrompt), never a rewording --
// on sqlglot-4051 a confirmation was restated as an order and every cheap model built it (ct-series-v5).
// No brief (no profile, a timeout, an error) only means no search terms. STEALTHLAB_BRIEF=off skips the call.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { configDir } from "./config.mjs";
import { readExecConfig } from "./exec/store.mjs";
import { helperCall } from "./helper_call.mjs";
import { dispatchedTurns } from "./continuity.mjs";

export const KINDS = ["code", "question", "chat"];
export const SIZES = ["small", "medium", "large"];
export const CLARITIES = ["clear", "unclear"];
const TURNS = 6;
const TURN_CHARS = 700;
const CONTEXT_CHARS = 4000;
const TAIL_BYTES = 256 * 1024;

export const briefEnabled = (env = process.env) => (env.STEALTHLAB_BRIEF || "on").toLowerCase() !== "off";

export const BRIEF_SYSTEM = [
  "You turn a developer's latest message in a coding session into a precise brief for a coding assistant.",
  "You get the last few turns (oldest first) and then the message. Reply with ONE JSON object and nothing else:",
  '{"task": string, "files_hinted": [string], "acceptance": string, "parts": [string], "clarity": "clear"|"unclear",',
  '"kind": "code"|"question"|"chat", "size": "small"|"medium"|"large", "search_terms": [string]}.',
  "task: the request restated in full. Resolve 'that bug', 'it', 'the other one' from the earlier turns. Keep every name,",
  "number, value and example from the message; add no requirement it does not state.",
  "files_hinted: files or modules named or clearly implied (at most 8; may be empty).",
  "acceptance: how to tell it is done, in one sentence, only from what the message says (may be empty).",
  "parts: one string per separate thing the message asks for when there are several; otherwise [].",
  "kind: 'code' only if the message asks for a change to code, files, queries or tests in this project; 'question' if it asks",
  "for an explanation or information; 'chat' for anything else (thanks, opinions, small talk). A message that confirms,",
  "restates or asks about what the assistant already did in the earlier turns asks for no new change: not 'code'.",
  "clarity: 'unclear' if an assistant could not start without asking the user something (no target, an ambiguous 'that',",
  "no stated goal). A message that points to a file for the details ('the task is in TASK.md', 'see notes.txt') is clear",
  "when that file exists: its start may be included after the message, and the assistant can read the rest.",
  "The project's file names may follow too: a message naming a function or feature that plausibly lives in one of them",
  "('add is broken, fix it' with a calc.py) is clear; put that file in files_hinted.",
  "size: small = one function, file or query; large = many files or an open-ended redesign.",
  "search_terms: up to 8 names to search this project's code for, as the code would spell them: the identifiers, file",
  "or module names, keywords and operators the request is about, guessed from the project's file names where the",
  "message uses plain words ('ms sql server' with a tsql.py -> tsql; 'double pipe' -> ||). May be empty.",
].join(" ");

// ---- files the message names: "the task is in TASK.md" puts the details in the file, not the message ------------------

const NAMED_FILE = /(?:^|[\s`'"(])((?:[\w.-]+\/)*[\w.-]+\.(?:md|txt|rst|json|ya?ml|toml|ini|cfg|py|js|mjs|ts|tsx|jsx|sql|csv|html|css|go|rs|java|rb|sh))(?=$|[\s`'".,;:)!?])/g;
const NAMED_MAX = 2;
const NAMED_CHARS = 3000;

// [{name, text}] for at most NAMED_MAX files the message names that exist inside `cwd` (never outside it), each cut to
// NAMED_CHARS characters. Best-effort: nothing on any error.
export function namedFiles(prompt, cwd) {
  if (!cwd) return [];
  const out = [];
  const seen = new Set();
  const root = path.resolve(cwd);
  for (const m of String(prompt || "").matchAll(NAMED_FILE)) {
    const name = m[1];
    if (seen.has(name) || out.length >= NAMED_MAX) continue;
    seen.add(name);
    try {
      const abs = path.resolve(root, name);
      if (!abs.startsWith(root + path.sep)) continue;
      if (!fs.statSync(abs).isFile()) continue;
      const text = fs.readFileSync(abs, "utf-8").slice(0, NAMED_CHARS);
      if (text.includes("\u0000")) continue;                  // binary
      out.push({ name, text });
    } catch { /* not there: skip */ }
  }
  return out;
}

// The project's tracked file names (git ls-files), at most PROJECT_FILES names and PROJECT_CHARS characters, or "".
// "add is broken, fix it" is clear when the brief can see a calc.py; without the listing it cannot tell.
const PROJECT_FILES = 120;
const PROJECT_CHARS = 2000;
export function projectFiles(cwd) {
  if (!cwd) return "";
  try {
    const r = spawnSync("git", ["-C", cwd, "ls-files"], { encoding: "utf8", timeout: 3000, windowsHide: true });
    if (r.status !== 0) return "";
    const names = r.stdout.split(/\r?\n/).filter((n) => n && !n.startsWith(".kell/")).slice(0, PROJECT_FILES);
    const text = names.join("\n");
    return text.length <= PROJECT_CHARS ? text : `${text.slice(0, PROJECT_CHARS).replace(/\n[^\n]*$/, "")}\n...`;
  } catch {
    return "";
  }
}

// ---- the last turns of the session, from the transcript Claude Code points the hook at -------------------------------

function textOf(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter((b) => b?.type === "text" && typeof b.text === "string").map((b) => b.text).join("\n");
}

// -> [{role: "user"|"assistant", text}], oldest first, at most `n`, text only (no tool calls or results), shortened.
// `current` is dropped when it is the transcript's last user turn (some versions write the prompt before the hook runs).
export function recentTurns(transcriptPath, { n = TURNS, current = "" } = {}) {
  const out = [];
  try {
    const fd = fs.openSync(transcriptPath, "r");
    try {
      const size = fs.fstatSync(fd).size;
      const len = Math.min(size, TAIL_BYTES);
      const buf = Buffer.alloc(len);
      fs.readSync(fd, buf, 0, len, size - len);
      for (const line of buf.toString("utf8").split(/\r?\n/)) {
        if (!line.trim()) continue;
        let o;
        try { o = JSON.parse(line); } catch { continue; }                 // a partial first line
        const role = o?.message?.role;
        if ((role !== "user" && role !== "assistant") || o.isSidechain || o.isMeta) continue;
        const text = textOf(o.message.content).replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, "").trim();
        if (!text) continue;
        const at = Date.parse(o.timestamp || "");
        out.push({ role, text: text.length > TURN_CHARS ? `${text.slice(0, TURN_CHARS - 3)}...` : text, ...(Number.isNaN(at) ? {} : { at }) });
      }
    } finally {
      fs.closeSync(fd);
    }
  } catch { /* no transcript: no earlier turns */ }
  const cur = String(current || "").trim();
  if (out.length && out[out.length - 1].role === "user" && cur && out[out.length - 1].text.startsWith(cur.slice(0, TURN_CHARS - 3).slice(0, 80))) out.pop();
  return out.slice(-n);
}

// ---- the brief ---------------------------------------------------------------------------------------------------------

const asList = (v, max) => (Array.isArray(v) ? v.map((x) => String(x).trim()).filter(Boolean).slice(0, max) : []);

// A model's reply -> a brief, or null when it is not one. Tolerates a code fence or text around the JSON object.
export function parseBrief(text) {
  const m = /\{[\s\S]*\}/.exec(String(text || ""));
  if (!m) return null;
  let o;
  try { o = JSON.parse(m[0]); } catch { return null; }
  if (!o || typeof o !== "object") return null;
  const task = String(o.task || "").trim();
  // a question or chat has no task to restate ("hmm interesting" -> task ""): only a code brief needs one
  if (!KINDS.includes(o.kind) || !SIZES.includes(o.size) || !CLARITIES.includes(o.clarity)) return null;
  if (!task && o.kind === "code") return null;
  return { task, files_hinted: asList(o.files_hinted, 8), acceptance: String(o.acceptance || "").trim(),
           parts: asList(o.parts, 6), clarity: o.clarity, kind: o.kind, size: o.size, search_terms: asList(o.search_terms, 8) };
}

// The session's earlier turns: the transcript's, and the turns a cheap model did (lib/continuity.mjs), which the
// transcript may not hold (a dispatched prompt is blocked). In time order, the last `n`; a dispatched turn the
// transcript does hold is not counted twice.
export function sessionTurns({ transcriptPath, sessionId, current = "", env = process.env, n = TURNS }) {
  const fromTranscript = transcriptPath ? recentTurns(transcriptPath, { current, n: 50 }) : [];
  const seen = new Set(fromTranscript.map((t) => t.text.slice(0, 80)));
  const dispatched = sessionId ? dispatchedTurns(sessionId, { env }).filter((t) => !seen.has(String(t.text).slice(0, 80)))
    .map((t) => ({ ...t, text: t.text.length > TURN_CHARS ? `${t.text.slice(0, TURN_CHARS - 3)}...` : t.text })) : [];
  if (!dispatched.length) return fromTranscript.slice(-n);
  const all = [...fromTranscript.map((t, i) => ({ ...t, i })), ...dispatched.map((t, i) => ({ ...t, i: 1e6 + i }))];
  all.sort((a, b) => (a.at != null && b.at != null ? a.at - b.at : a.i - b.i));
  return all.slice(-n).map(({ i, ...t }) => t);
}

// The earlier turns as one text (oldest first, at most `max` characters, the oldest cut first): what the server's
// judge reads as context for the request (POST /triage).
export function turnsText(turns, max = 4000) {
  const t = (turns || []).map((x) => `${x.role === "user" ? "Developer" : "Assistant"}: ${x.text}`).join("\n\n");
  return t.length > max ? t.slice(t.length - max) : t;
}

// What the cheap models are told: the conversation so far as it was said, then the user's request word for word --
// what the session's own model sees, not anyone's rewording of it. Within `max` characters: the earlier turns are cut
// (oldest first) before the request is; the request alone is cut only when it is longer than `max`.
export const REQUEST_LABEL = "The request:\n";
export function conversationPrompt(turns, raw, max = 2000) {
  const request = String(raw || "").trim();
  if (request.length + REQUEST_LABEL.length >= max) return `${REQUEST_LABEL}${request}`.slice(0, max);
  let history = (turns || []).map((t) => `${t.role === "user" ? "Developer" : "Assistant"}: ${t.text}`).join("\n\n");
  const room = max - request.length - REQUEST_LABEL.length - "Earlier in this conversation:\n".length - 2;
  if (history.length > room) history = room > 200 ? `...${history.slice(history.length - room + 3)}` : "";
  return history ? `Earlier in this conversation:\n${history}\n\n${REQUEST_LABEL}${request}` : request;
}

// ---- the call -----------------------------------------------------------------------------------------------------------

function ledger(env, row) {
  try {
    fs.mkdirSync(configDir(env), { recursive: true });
    fs.appendFileSync(path.join(configDir(env), "dispatch_costs.jsonl"), JSON.stringify({ at: new Date().toISOString(), ...row }) + "\n");
  } catch { /* the ledger is best-effort */ }
}

// -> brief | null. Never throws, never waits longer than the timeout.
export async function makeBrief({ prompt, transcriptPath, cwd = null, env = process.env, fetchImpl = globalThis.fetch, log = () => {},
                                  readConfig = readExecConfig }) {
  try {
    if (!briefEnabled(env)) return null;
    const name = String(env.STEALTHLAB_BRIEF_MODEL || "gemma-4-31b-it").trim();
    const profile = readConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) { log(`brief: no profile for ${name}`); return null; }
    const turns = transcriptPath ? recentTurns(transcriptPath, { current: prompt }) : [];
    let history = turns.map((t) => `${t.role === "user" ? "Developer" : "Assistant"}: ${t.text}`).join("\n\n");
    if (history.length > CONTEXT_CHARS) history = history.slice(history.length - CONTEXT_CHARS);
    const files = namedFiles(prompt, cwd).map((f) => `--- ${f.name} (start) ---\n${f.text}`).join("\n\n");
    const listing = projectFiles(cwd);
    const user = `${history ? `Earlier turns:\n${history}\n\n` : ""}Latest message:\n${String(prompt || "").trim()}` +
      (files ? `\n\nFiles the message names:\n${files}` : "") +
      (listing ? `\n\nThe project's files (to tell where a named function or feature lives):\n${listing}` : "");
    const timeoutMs = Math.max(500, Number(env.STEALTHLAB_BRIEF_TIMEOUT_MS || 15000));
    // the first usable reply of gemma or, when it is slow, a model on another provider (lib/helper_call.mjs)
    const r = await helperCall({ what: "brief", primary: name, system: BRIEF_SYSTEM, user, maxTokens: 600, timeoutMs, parse: parseBrief,
                                 env, fetchImpl, log, readConfig, record: (row) => ledger(env, row) });
    if (!r) return null;
    return r.value;
  } catch (err) {
    log(`brief: ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return null;
  }
}

// ---- overrides: `!opus` and `!cheap` at the start of a prompt ---------------------------------------------------------

export const overridesEnabled = (env = process.env) => (env.STEALTHLAB_OVERRIDES || "on").toLowerCase() !== "off";

// -> {kind: "opus" | "cheap" | null, prompt: the prompt without the prefix}
export function parseOverride(prompt, env = process.env) {
  const p = String(prompt || "");
  if (!overridesEnabled(env)) return { kind: null, prompt: p };
  const m = /^\s*!(opus|cheap)\b[ \t]*/i.exec(p);
  return m ? { kind: m[1].toLowerCase(), prompt: p.slice(m[0].length) } : { kind: null, prompt: p };
}
