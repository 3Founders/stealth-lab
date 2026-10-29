// A cheap reviewer of a cheap model's answer: one call, shown the request and the patch, says whether the patch does
// what was asked. It is evidence for accepting an answer, alongside the project's own tests -- not a check by itself.
//
// Why: the tests kell writes before the fix guess the interface or the behaviour about half the time, so as the
// only judge they accepted 11 cheap answers of which 2 were right and rejected 7 right ones (pipeline survey stage 3,
// 2026-10-16). A reviewer reads the change against the request instead; whether it helps is measured offline on stage
// 3's saved answers before any rule uses it. The prompt and the default model are fixed BEFORE that measurement
// (.scratch/acceptance_rules_preregistered_2026-10-16.md) and are not tuned on it.
// STEALTHLAB_REVIEW_MODEL names the model (an exec.json profile; default gemma-4-31b-it); "off": no review.
import fs from "node:fs";
import path from "node:path";
import { readExecConfig } from "./exec/store.mjs";
import { enclosingBlock } from "./locate.mjs";
import { profileKey } from "./executors/profile.mjs";
import { costOf, recordSpend } from "./spend.mjs";

export const REVIEW_SYSTEM = [
  "You review a code change made by another engineer for a request. You get the request and the change as a unified",
  "diff. Decide whether the change does what the request asks: the behaviour the request describes, its example if it",
  "gives one, and every part it asks for -- not whether the code is tidy. Be critical: a change that only touches the",
  "right place, guesses at an interface the request does not ask for, handles the example but not the general case, or",
  "changes only tests or docs does NOT do it. Reply with only a JSON object:",
  '{"verdict": "fixes" | "does_not_fix" | "unsure", "why": one sentence}.',
].join(" ");

// The same, plus the two ways a plausible wrong answer got past it (sqlglot-4067, cache-full-2: Sonnet said "fixes" to a change
// that made T-SQL read `||` as bitwise OR -- a meaning nobody asked for -- and wrote CONCAT(...) where the request said `+`
// is the operator that works). A deviation from the pre-registered prompt (docs/acceptance_rules_preregistered_2026-10-16.md).
export const REVIEW_SYSTEM_V2 = [
  REVIEW_SYSTEM.replace(/ Reply with only a JSON object:.*$/, ""),
  "Also NOT: a change that gives existing code, syntax or options a new meaning the request does not ask for, or that",
  "produces a different form than the one the request (or a source it cites) says is right -- when the request says what",
  "the correct behaviour or output is, the change must give exactly that. Check what the changed code actually returns for",
  "the request's own example, reading the diff line by line. Reply with only a JSON object:",
  '{"verdict": "fixes" | "does_not_fix" | "unsure", "why": one sentence}.',
].join(" ");
// v2 by default: on 15 real answers graded by their hidden tests, v2 with the changed functions accepted 7/8 right and 1/7 wrong,
// v1 (diff alone or with the functions) 7/8 and 2/7 (reviewer replay 2026-10-28). STEALTHLAB_REVIEW_PROMPT=v1: the pre-registered prompt
const reviewSystem = (env) => (String(env.STEALTHLAB_REVIEW_PROMPT || "v2").toLowerCase() === "v1" ? REVIEW_SYSTEM : REVIEW_SYSTEM_V2);

const DIFF_MAX = 14000;
const CODE_MAX = 16000;

// The functions a diff changes, as they read after the change (from the answer's worktree), with line numbers: a reviewer
// shown only the diff guessed at what the code around it does -- "I can't tell from the diff whether the parser already
// defines ..." (unsure on a wrong answer), and a real fix rejected for code it could not see (sqlglot-4067, jsonargparse-560;
// reviewer replay 2026-10-28). Test files are left out; each function once, at most CODE_MAX characters in all.
export function changedCode(worktree, diff, { max = CODE_MAX } = {}) {
  if (!worktree) return "";
  const parts = [];
  let used = 0;
  for (const block of String(diff || "").split(/(?=^diff --git )/m)) {
    const file = /^\+\+\+ b\/(\S+)/m.exec(block)?.[1];
    if (!file || /(^|\/)(tests?|testing)\/|(^|\/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$/.test(file)) continue;
    let lines;
    try { lines = fs.readFileSync(path.join(worktree, file), "utf8").split(/\r?\n/); } catch { continue; }
    const seen = [];
    for (const m of block.matchAll(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/gm)) {
      const b = enclosingBlock(lines, Number(m[1]) + Math.min(3, Math.max(0, Number(m[2] || 1) - 1)));
      if (seen.some((s) => !(b.end < s.start || b.start > s.end))) continue;
      seen.push(b);
      const body = lines.slice(b.start - 1, b.end).map((l, i) => `${b.start + i}\t${l}`).join("\n");
      const piece = `--- ${file} lines ${b.start}-${b.end}${b.symbol ? ` (${b.symbol})` : ""}, after the change ---\n${body}`;
      if (used + piece.length > max) return parts.join("\n\n");
      parts.push(piece);
      used += piece.length;
    }
  }
  return parts.join("\n\n");
}

// -> {verdict: "fixes"|"does_not_fix"|"unsure", why} | null (no model, an error, an unusable reply)
export function parseVerdict(text) {
  const m = /\{[\s\S]*\}/.exec(String(text || ""));
  if (!m) return null;
  try {
    const o = JSON.parse(m[0]);
    const v = String(o.verdict || "").toLowerCase();
    if (!["fixes", "does_not_fix", "unsure"].includes(v)) return null;
    return { verdict: v, why: String(o.why || "").slice(0, 300) };
  } catch { return null; }
}

// model: an exec.json profile (one chat call on its endpoint), or a Claude model (one lean, tool-less `claude -p` under
// the user's own login: a strong reviewer reads only the request and the diff, a small share of what solving costs).
// runClaude: for tests, a stand-in for lib/claude_call.mjs runClaudeAsync.
export async function reviewPatch({ prompt, diff, worktree = null, env = process.env, fetchImpl = globalThis.fetch, log = () => {}, timeoutMs = 90000,
                                    model = null, runClaude = null, signal = null }) {
  const name = String(model || env.STEALTHLAB_REVIEW_MODEL || "gemma-4-31b-it").trim();
  if (name.toLowerCase() === "off" || !String(diff || "").trim()) return null;
  const d = String(diff);
  // STEALTHLAB_REVIEW_CODE=off: the request and the diff alone, as before
  const code = (env.STEALTHLAB_REVIEW_CODE || "on").toLowerCase() !== "off" ? changedCode(worktree, d) : "";
  const user = `The request:\n${String(prompt || "").slice(0, 6000)}\n\nThe change:\n` +
    (d.length > DIFF_MAX ? `${d.slice(0, DIFF_MAX)}\n[... the rest of the diff is cut]` : d) +
    (code ? `\n\nThe changed functions as they read after the change:\n${code}` : "");
  return askReviewer({ name, system: reviewSystem(env), user, parse: parseVerdict, what: "review", env, fetchImpl, log, timeoutMs, runClaude, signal });
}

// A cheap model's reply to a question or a conversational turn (lib/answer.mjs), checked before the developer sees it.
export const ANSWER_REVIEW_SYSTEM = [
  "You check a reply another assistant wrote to a developer's latest message in a coding session. You get what the",
  "assistant knew (the session's notes, its changes, the relevant code, the earlier turns), the message and the reply.",
  "Decide whether the reply answers the message correctly and completely, agrees with the code and the changes shown,",
  "and claims nothing they do not support. A reply that guesses, dodges the question, or would mislead the developer",
  "is NOT good. Nor is one that cannot answer from what it was shown and asks the developer for something an assistant",
  "working in the repository could find itself (by reading the project's files or running a command there): the",
  "session's own assistant, which can look, answers instead. Reply with only a JSON object:",
  '{"verdict": "good" | "bad" | "unsure", "why": one sentence}.',
].join(" ");

export function parseAnswerVerdict(text) {
  const m = /\{[\s\S]*\}/.exec(String(text || ""));
  if (!m) return null;
  try {
    const o = JSON.parse(m[0]);
    const v = String(o.verdict || "").toLowerCase();
    if (!["good", "bad", "unsure"].includes(v)) return null;
    return { verdict: v, why: String(o.why || "").slice(0, 300) };
  } catch { return null; }
}

export async function reviewAnswer({ context, message, reply, env = process.env, fetchImpl = globalThis.fetch, log = () => {},
                                     timeoutMs = 90000, model = null, runClaude = null, signal = null }) {
  const name = String(model || env.STEALTHLAB_REVIEW_STRONG_MODEL || "claude-sonnet-5-5").trim();
  if (name.toLowerCase() === "off" || !String(reply || "").trim()) return null;
  const user = `What the assistant knew:\n${String(context || "").slice(0, 24000)}\n\nThe developer's message:\n` +
    `${String(message || "").slice(0, 6000)}\n\nThe reply:\n${String(reply).slice(0, 12000)}`;
  return askReviewer({ name, system: ANSWER_REVIEW_SYSTEM, user, parse: parseAnswerVerdict, what: "answer-review", env, fetchImpl, log,
                       timeoutMs, runClaude, signal });
}

// A cheap agent that finished without changing anything and says why ("the typo is not there", "there is no CLI example
// to extend") is reporting a finding, not failing. Before this, kell counted it as a failed try and went on to the next
// model: on kell-try 2026-10-29, two models found correctly that a typo did not exist, both were thrown away, and the
// dispatch ran 15 minutes before the session's model said the same. The reviewer checks the claim in the repository
// itself (read-only tools), since a finding is a claim about the code and the reviewer has no diff to read.
export const FINDING_REVIEW_SYSTEM = [
  "A developer asked a coding agent for a change. The agent made no change and reports why. You are in the repository",
  "with read-only tools (Read, Grep, Glob). Check the report against the code: look for what the request refers to",
  "yourself, the way the agent should have. The report is good only if it is correct (what it says is or is not in the",
  "code is so), it answers the request (it says plainly why no change was made, and what the developer could ask for",
  "instead when the request rests on something that is not there), and a change really is not what the request needs:",
  "an agent that gave up on a doable change, or misread the request, is bad. Reply with only a JSON object:",
  '{"verdict": "good" | "bad" | "unsure", "why": one sentence}.',
].join(" ");

export async function reviewFinding({ root, request, finding, env = process.env, log = () => {}, timeoutMs = 180000, model = null,
                                      runClaude = null, signal = null }) {
  const name = String(model || env.STEALTHLAB_REVIEW_STRONG_MODEL || "claude-sonnet-5-5").trim();
  if (name.toLowerCase() === "off" || !name.includes("claude") || !String(finding || "").trim() || !root) return null;
  const { readOnlyArgs, runClaudeAsync } = await import("./claude_call.mjs");
  const user = `The developer's request:\n${String(request || "").slice(0, 6000)}\n\nThe agent's report (no change made):\n` +
    `${String(finding).slice(0, 6000)}`;
  const r = await (runClaude || ((a, input) => runClaudeAsync(env, a, input, signal, timeoutMs, root)))(readOnlyArgs(name, FINDING_REVIEW_SYSTEM), user);
  let j = null;
  try { j = JSON.parse(String(r?.stdout || "").trim().split(/\r?\n/).pop()); } catch { /* below */ }
  const v = j && !j.is_error ? parseAnswerVerdict(j.result) : null;
  if (!v) { log(`finding-review: ${name} gave no usable verdict (exit ${r?.status ?? "?"})`); return null; }
  log(`finding-review: ${name} says ${v.verdict} (${v.why.slice(0, 120)})`);
  const cost = typeof j.total_cost_usd === "number" ? j.total_cost_usd : null;
  recordSpend(env, { what: "finding-review", model: name, cost_usd: cost });
  return { ...v, model: name, cost_usd: cost };
}

// a Claude reviewer's limit: 120 s cut a review of a 60-line type-hint diff with no verdict, which delayed the end of the
// cheap tries by 5 minutes (kell-try, 2026-10-30; a 20-line one took 60 s). STEALTHLAB_REVIEW_TIMEOUT_S, default 240.
const reviewLimitMs = (env) => Math.max(30, Number(env.STEALTHLAB_REVIEW_TIMEOUT_S) || 240) * 1000;

// One reviewer call: a Claude model through a lean `claude -p`, or an exec.json profile's endpoint. -> {verdict, why,
// model, cost_usd} | null (no model, an error, an unusable reply). Its cost goes to the spend ledger as `what`.
async function askReviewer({ name, system, user, parse, what, env, fetchImpl, log, timeoutMs, runClaude, signal }) {
  if (name.includes("claude")) {
    const { leanArgs, runClaudeAsync } = await import("./claude_call.mjs");
    const r = await (runClaude || ((a, input) => runClaudeAsync(env, a, input, signal, Math.max(timeoutMs, reviewLimitMs(env)))))(leanArgs(name, system), user);
    let j = null;
    try { j = JSON.parse(String(r?.stdout || "").trim().split(/\r?\n/).pop()); } catch { /* below */ }
    const v = j && !j.is_error ? parse(j.result) : null;
    if (!v) { log(`${what}: ${name} gave no usable verdict (exit ${r?.status ?? "?"})`); return null; }
    log(`${what}: ${name} says ${v.verdict} (${v.why.slice(0, 120)})`);
    const cost = typeof j.total_cost_usd === "number" ? j.total_cost_usd : null;
    recordSpend(env, { what, model: name, cost_usd: cost });
    return { ...v, model: name, cost_usd: cost };
  }
  try {
    const profile = readExecConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) return null;
    const messages = profile.no_system_role ? [{ role: "user", content: `${system}\n\n${user}` }]
      : [{ role: "system", content: system }, { role: "user", content: user }];
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 4000, temperature: 0, ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`${what}: HTTP ${res.status}`); return null; }
    const body = await res.json();
    const v = parse(body?.choices?.[0]?.message?.content);
    if (!v) { log(`${what}: no usable verdict`); return null; }
    const u = body?.usage || {};
    const cost = costOf(profile, u);
    log(`${what}: ${name} says ${v.verdict} (${v.why.slice(0, 120)})`);
    recordSpend(env, { what, model: name, cost_usd: cost });
    return { ...v, model: name, cost_usd: cost };
  } catch (err) {
    log(`${what}: ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return null;
  }
}

// the answer changes source code -- not only tests, docs or config (signal S of the acceptance rule)
export function changesSource(files = []) {
  const test = /(^|\/)(tests?|testing)\/|(^|\/)test_[^/]*\.py$|_test\.(py|go|js|ts)$|\.(test|spec)\.[jt]sx?$/i;
  const doc = /\.(md|rst|txt|cfg|ini|toml|ya?ml|json|lock)$|(^|\/)docs?\//i;
  return files.some((f) => !test.test(f) && !doc.test(f));
}
