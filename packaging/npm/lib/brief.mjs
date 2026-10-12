// The task brief: what the cheap models and the test writer are told instead of the raw prompt, and the rule that
// decides whether a prompt is dispatched at all.
//
// A real prompt is often not a clean task: "now do the same for the other one", "that bug again", a question, "thanks".
// Sent as it is, the cheap models guess at it, the test writer writes tests for a guess, and a question gets "answered"
// with a diff. One cheap call (gpt-oss-120b on the user's own provider key, from exec.json -- never a Claude model) turns
// the last few turns plus the prompt into
//   { task, files_hinted, acceptance, parts[], clarity: clear|unclear, kind: code|question|chat, size: small|medium|large }
// and dispatch goes ahead only for kind=code, clarity=clear, size != large (and a check, as before). Everything else is
// left to the session's own model, as with no StealthLab. Nothing here ever asks the user a question.
//
// Fails open: no profile, a timeout (8 s hard), an error or an unparseable reply give `null`, and dispatch behaves as it
// did before this module (the raw prompt). One deterministic guard only ever pushes DOWN: a prompt that is plainly a
// question or pleasantry is never a code task, whatever the model said. STEALTHLAB_BRIEF=off turns the brief and the gate
// off; STEALTHLAB_BRIEF_MODEL (default gpt-oss-120b) and STEALTHLAB_BRIEF_TIMEOUT_MS (default 8000) tune it.
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.mjs";
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";

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
  '"kind": "code"|"question"|"chat", "size": "small"|"medium"|"large"}.',
  "task: the request restated in full. Resolve 'that bug', 'it', 'the other one' from the earlier turns. Keep every name,",
  "number, value and example from the message; add no requirement it does not state.",
  "files_hinted: files or modules named or clearly implied (at most 8; may be empty).",
  "acceptance: how to tell it is done, in one sentence, only from what the message says (may be empty).",
  "parts: one string per separate thing the message asks for when there are several; otherwise [].",
  "kind: 'code' only if the message asks for a change to code, files, queries or tests in this project; 'question' if it asks",
  "for an explanation or information; 'chat' for anything else (thanks, opinions, small talk).",
  "clarity: 'unclear' if an assistant could not start without asking the user something (no target, an ambiguous 'that',",
  "no stated goal). size: small = one function, file or query; large = many files or an open-ended redesign.",
].join(" ");

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
        out.push({ role, text: text.length > TURN_CHARS ? `${text.slice(0, TURN_CHARS - 3)}...` : text });
      }
    } finally {
      fs.closeSync(fd);
    }
  } catch { /* no transcript: no earlier turns */ }
  const cur = String(current || "").trim();
  if (out.length && out[out.length - 1].role === "user" && cur && out[out.length - 1].text.startsWith(cur.slice(0, TURN_CHARS - 3).slice(0, 80))) out.pop();
  return out.slice(-n);
}

// ---- the deterministic guard: a plain question or pleasantry is never a code task -------------------------------------

// a pleasantry: only these words, at most 5 of them ("perfect, looks good", "got it, thank you", "great work")
const PLEASANTRY = new Set(("hi hello hey thanks thank you thx ty ok okay cool great nice perfect awesome lgtm got it yes yep no nope sure " +
  "good looks sounds work job well done that all so very much").split(" "));
const isPleasantry = (p) => {
  const words = p.toLowerCase().match(/[a-z']+/g) || [];
  return words.length > 0 && words.length <= 5 && words.every((w) => PLEASANTRY.has(w));
};
const QUESTION = /^\s*(?:explain|describe|tell me|what(?:'s| is| are| does| do)|why\b|how (?:does|do|is|are)\b|where (?:is|are|does)\b|who\b|which\b|when\b|is there\b|does\b|do you\b)/i;
// Anything that asks for a change ANYWHERE in the prompt, or names a file: such a prompt is never ruled out. This matters:
// the benchmark's own task prompts open with a question ("What is the percentage of borderless cards? -- Write one SQLite
// query ...") and a guard that looked only at the start would have blocked 18 of the 210 cheap wins on the saved runs.
const ASKS_FOR_CHANGE = /\b(?:fix|add|write|create|refactor|rename|remove|delete|implement|modify|replace|convert|generate|install|extract|merge|save|edit|patch)\b|\b[\w./-]+\.(?:py|js|mjs|ts|tsx|jsx|sql|md|json|ya?ml|toml|txt|html|css|csv|sh)\b/i;

// -> "chat" | "question" | null (no opinion: leave it to the model)
export function guardKind(prompt) {
  const p = String(prompt || "").trim();
  if (!p) return "chat";
  if (p.length <= 60 && isPleasantry(p)) return "chat";
  if (ASKS_FOR_CHANGE.test(p)) return null;
  if (QUESTION.test(p)) return "question";
  return null;
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
  if (!task || !KINDS.includes(o.kind) || !SIZES.includes(o.size) || !CLARITIES.includes(o.clarity)) return null;
  return { task, files_hinted: asList(o.files_hinted, 8), acceptance: String(o.acceptance || "").trim(),
           parts: asList(o.parts, 6), clarity: o.clarity, kind: o.kind, size: o.size };
}

// -> {ok: boolean, reason}. `brief` null = no brief could be made: today's behaviour, except what the guard rules out.
export function briefGate(brief, prompt) {
  const guard = guardKind(prompt);
  if (guard) return { ok: false, reason: `the prompt is a ${guard}, not a code task` };
  if (!brief) return { ok: true, reason: "no brief (raw prompt)" };
  if (brief.kind !== "code") return { ok: false, reason: `kind=${brief.kind}` };
  if (brief.clarity !== "clear") return { ok: false, reason: "the request is unclear" };
  if (brief.size === "large") return { ok: false, reason: "size=large" };
  return { ok: true, reason: "clear, small or medium code task" };
}

// What the cheap models and the test writer get. All of the brief, nothing but the brief.
export function briefText(brief) {
  const lines = [brief.task];
  if (brief.parts.length > 1) lines.push("", "This is several things; do all of them:", ...brief.parts.map((p, i) => `${i + 1}. ${p}`));
  if (brief.files_hinted.length) lines.push("", `Files likely involved: ${brief.files_hinted.join(", ")}`);
  if (brief.acceptance) lines.push("", `Done when: ${brief.acceptance}`);
  return lines.join("\n");
}

// ---- the call -----------------------------------------------------------------------------------------------------------

function ledger(env, row) {
  try {
    fs.mkdirSync(configDir(env), { recursive: true });
    fs.appendFileSync(path.join(configDir(env), "dispatch_costs.jsonl"), JSON.stringify({ at: new Date().toISOString(), ...row }) + "\n");
  } catch { /* the ledger is best-effort */ }
}

// -> brief | null. Never throws, never waits longer than the timeout.
export async function makeBrief({ prompt, transcriptPath, env = process.env, fetchImpl = globalThis.fetch, log = () => {}, readConfig = readExecConfig }) {
  try {
    if (!briefEnabled(env)) return null;
    const name = String(env.STEALTHLAB_BRIEF_MODEL || "gpt-oss-120b").trim();
    const profile = readConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) { log(`brief: no profile for ${name}`); return null; }
    const turns = transcriptPath ? recentTurns(transcriptPath, { current: prompt }) : [];
    let history = turns.map((t) => `${t.role === "user" ? "Developer" : "Assistant"}: ${t.text}`).join("\n\n");
    if (history.length > CONTEXT_CHARS) history = history.slice(history.length - CONTEXT_CHARS);
    const user = `${history ? `Earlier turns:\n${history}\n\n` : ""}Latest message:\n${String(prompt || "").trim()}`;
    const messages = profile.no_system_role
      ? [{ role: "user", content: `${BRIEF_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: BRIEF_SYSTEM }, { role: "user", content: user }];
    const timeoutMs = Math.max(500, Number(env.STEALTHLAB_BRIEF_TIMEOUT_MS || 8000));
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 600, temperature: 0, ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`brief: HTTP ${res.status}`); return null; }
    const body = await res.json();
    const u = body?.usage;
    if (u && (profile.input_per_mtok || profile.output_per_mtok)) {
      ledger(env, { what: "brief", model: name,
        cost_usd: ((u.prompt_tokens || 0) * (profile.input_per_mtok || 0) + (u.completion_tokens || 0) * (profile.output_per_mtok || 0)) / 1e6 });
    }
    const brief = parseBrief(body?.choices?.[0]?.message?.content);
    if (!brief) log("brief: the reply was not a brief");
    return brief;
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
