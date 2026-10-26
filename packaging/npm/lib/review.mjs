// A cheap reviewer of a cheap model's answer: one call, shown the request and the patch, says whether the patch does
// what was asked. It is evidence for accepting an answer, alongside the project's own tests -- not a check by itself.
//
// Why: the tests kell writes before the fix guess the interface or the behaviour about half the time, so as the
// only judge they accepted 11 cheap answers of which 2 were right and rejected 7 right ones (pipeline survey stage 3,
// 2026-10-16). A reviewer reads the change against the request instead; whether it helps is measured offline on stage
// 3's saved answers before any rule uses it. The prompt and the default model are fixed BEFORE that measurement
// (.scratch/acceptance_rules_preregistered_2026-10-16.md) and are not tuned on it.
// STEALTHLAB_REVIEW_MODEL names the model (an exec.json profile; default gpt-oss-120b); "off": no review.
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";
import { recordSpend } from "./spend.mjs";

export const REVIEW_SYSTEM = [
  "You review a code change made by another engineer for a request. You get the request and the change as a unified",
  "diff. Decide whether the change does what the request asks: the behaviour the request describes, its example if it",
  "gives one, and every part it asks for -- not whether the code is tidy. Be critical: a change that only touches the",
  "right place, guesses at an interface the request does not ask for, handles the example but not the general case, or",
  "changes only tests or docs does NOT do it. Reply with only a JSON object:",
  '{"verdict": "fixes" | "does_not_fix" | "unsure", "why": one sentence}.',
].join(" ");

const DIFF_MAX = 14000;

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
export async function reviewPatch({ prompt, diff, env = process.env, fetchImpl = globalThis.fetch, log = () => {}, timeoutMs = 90000,
                                    model = null, runClaude = null, signal = null }) {
  const name = String(model || env.STEALTHLAB_REVIEW_MODEL || "gpt-oss-120b").trim();
  if (name.toLowerCase() === "off" || !String(diff || "").trim()) return null;
  const d = String(diff);
  const user = `The request:\n${String(prompt || "").slice(0, 6000)}\n\nThe change:\n` +
    (d.length > DIFF_MAX ? `${d.slice(0, DIFF_MAX)}\n[... the rest of the diff is cut]` : d);
  return askReviewer({ name, system: REVIEW_SYSTEM, user, parse: parseVerdict, what: "review", env, fetchImpl, log, timeoutMs, runClaude, signal });
}

// A cheap model's reply to a question or a conversational turn (lib/answer.mjs), checked before the developer sees it.
export const ANSWER_REVIEW_SYSTEM = [
  "You check a reply another assistant wrote to a developer's latest message in a coding session. You get what the",
  "assistant knew (the session's notes, its changes, the relevant code, the earlier turns), the message and the reply.",
  "Decide whether the reply answers the message correctly and completely, agrees with the code and the changes shown,",
  "and claims nothing they do not support. A reply that guesses, dodges the question, or would mislead the developer",
  "is NOT good. Reply with only a JSON object:",
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

// One reviewer call: a Claude model through a lean `claude -p`, or an exec.json profile's endpoint. -> {verdict, why,
// model, cost_usd} | null (no model, an error, an unusable reply). Its cost goes to the spend ledger as `what`.
async function askReviewer({ name, system, user, parse, what, env, fetchImpl, log, timeoutMs, runClaude, signal }) {
  if (name.includes("claude")) {
    const { leanArgs, runClaudeAsync } = await import("./claude_call.mjs");
    const r = await (runClaude || ((a, input) => runClaudeAsync(env, a, input, signal, Math.max(timeoutMs, 120000))))(leanArgs(name, system), user);
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
    const cost = (profile.input_per_mtok || profile.output_per_mtok)
      ? ((u.prompt_tokens || 0) * (profile.input_per_mtok || 0) + (u.completion_tokens || 0) * (profile.output_per_mtok || 0)) / 1e6 : null;
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
