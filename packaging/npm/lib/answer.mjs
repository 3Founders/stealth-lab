// A cheap model answers a question or a conversational turn, before the session's model runs.
//
// Why: in a coding session most turns after the first change are explanations, confirmations and questions about what
// was done ("so it writes the 0 now?"), and the session's model pays for each with the whole conversation as context
// (ct-series-v5, jsonargparse, arm A: $0.22 for the fix, $0.53 for the two turns after it). A cheap model answers
// from the session's notes instead -- what was asked, what changed and how (the diff), the code the message is about,
// the last turns -- and a strong reviewer checks the reply before the developer sees it. A reply it does not call
// good is never shown: the session's model answers, as with no kell.
//
// Which turns: the server's judge (POST /triage, the hook's call) says work=answer or work=conversation. A change goes
// to dispatch (lib/dispatch.mjs); an unclear change to the session's model, which can ask. STEALTHLAB_ANSWERS=off turns
// this off; STEALTHLAB_ANSWER_MODEL (an exec.json profile, default glm-5.3-flash) is the model.
import { spawnSync } from "node:child_process";
import { readConfig } from "./config.mjs";
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";
import { recordSpend } from "./spend.mjs";
import { reviewAnswer } from "./review.mjs";
import { locate, renderForWriter } from "./locate.mjs";
import { sessionTurns, turnsText } from "./brief.mjs";
import { sessionStartedAt } from "./dag_run.mjs";
import { changedSince } from "./dispatch.mjs";

// The environment variable wins; otherwise `answers` in ~/.stealthlab/config.json (`setup --answers off|on`), so the switch
// does not depend on the variable reaching the agent's process (Cursor and Claude Code each start hooks from their own).
export const answersEnabled = (env = process.env) => {
  let v = String(env.STEALTHLAB_ANSWERS || "").trim();
  if (!v) { try { v = String(readConfig(env).answers ?? "").trim(); } catch { v = ""; } }
  return (v || "on").toLowerCase() !== "off";
};

export const ANSWER_SYSTEM = [
  "You are the assistant in a developer's coding session, answering their latest message. You get the session's notes",
  "(what was asked so far, the files changed and the changes themselves), the code the message is about, and the last",
  "turns. Answer from those: explain, confirm or correct plainly and specifically, naming the files, functions and",
  "values involved. Do not invent code or behaviour you were not shown; if the notes and code do not settle the",
  "question, say exactly what is unknown. Do not propose to make changes yourself and do not write a patch: answer only.",
  "A message that only thanks you or closes the topic gets a one-line reply.",
].join(" ");

const DIFF_MAX = 12000;
const CODE_MAX = 10000;
const REQUESTS = 15;

function git(root, args) {
  try {
    const r = spawnSync("git", ["-C", root, ...args], { encoding: "utf8", timeout: 5000, windowsHide: true, maxBuffer: 8 * 1024 * 1024 });
    return r.status === 0 ? r.stdout : "";
  } catch { return ""; }
}

// The session's notes, from facts only (no model): the requests so far, the files changed since the session started
// and the change itself (git diff). -> text ("" when there is nothing to say)
export function sessionNotes({ root, transcriptPath, sessionId, current = "", env = process.env }) {
  const turns = sessionTurns({ transcriptPath, sessionId, current, env, n: 50 });
  const asked = turns.filter((t) => t.role === "user").slice(-REQUESTS).map((t, i) => `${i + 1}. ${t.text.replace(/\s+/g, " ").slice(0, 300)}`);
  const changed = root ? [...changedSince(root, sessionStartedAt(transcriptPath))] : [];
  const tracked = changed.filter((f) => git(root, ["ls-files", "--", f]).trim());
  let diff = tracked.length ? git(root, ["diff", "HEAD", "--", ...tracked]) : "";
  if (diff.length > DIFF_MAX) diff = `${diff.slice(0, DIFF_MAX)}\n[... the rest of the diff is cut]`;
  const parts = [];
  if (asked.length) parts.push(`Requests in this session so far:\n${asked.join("\n")}`);
  if (changed.length) parts.push(`Files changed in this session: ${changed.join(", ")}`);
  if (diff) parts.push(`The session's changes:\n${diff}`);
  return parts.join("\n\n");
}

// -> {handled: true, text, model, answer, cost_usd} | null (not answered here: the session's model does it)
export async function answerTurn({ prompt, root = null, transcriptPath = null, sessionId = null, env = process.env, fetchImpl = globalThis.fetch,
                                   log = () => {}, review = reviewAnswer, timeoutMs = 120000 }) {
  if (!answersEnabled(env)) return null;
  const started = Date.now();
  const name = String(env.STEALTHLAB_ANSWER_MODEL || "glm-5.3-flash").trim();
  const profile = (() => { try { return readExecConfig(env).profiles?.[name]; } catch { return null; } })();
  if (!profile?.openai_base_url) { log(`answer: no profile for ${name}`); return null; }
  root = root ? (git(root, ["rev-parse", "--show-toplevel"]).trim() || root) : null;      // git's paths are from the top
  const notes = sessionNotes({ root, transcriptPath, sessionId, current: prompt, env });
  const code = root ? renderForWriter(locate({ prompt, root, env }), root, { max: CODE_MAX }) : "";
  const recent = turnsText(sessionTurns({ transcriptPath, sessionId, current: prompt, env }), 6000);
  const context = [notes && `Session notes:\n${notes}`, code && `Code the message is about:\n${code}`, recent && `The last turns:\n${recent}`]
    .filter(Boolean).join("\n\n");
  const user = `${context ? `${context}\n\n` : ""}The developer's latest message:\n${String(prompt || "").trim()}`;
  let reply = "";
  try {
    const messages = profile.no_system_role ? [{ role: "user", content: `${ANSWER_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: ANSWER_SYSTEM }, { role: "user", content: user }];
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: profile.max_output_tokens || 8000, temperature: 0,
                             ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`answer: ${name} HTTP ${res.status}`); return null; }
    const body = await res.json();
    reply = String(body?.choices?.[0]?.message?.content || "").trim();
    const u = body?.usage || {};
    const cost = (profile.input_per_mtok || profile.output_per_mtok)
      ? ((u.prompt_tokens || 0) * (profile.input_per_mtok || 0) + (u.completion_tokens || 0) * (profile.output_per_mtok || 0)) / 1e6 : null;
    recordSpend(env, { what: "answer", model: name, cost_usd: cost });
  } catch (err) {
    log(`answer: ${name} ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return null;
  }
  if (!reply) { log(`answer: ${name} gave an empty reply`); return null; }
  const v = await review({ context, message: prompt, reply, env, fetchImpl, log });
  if (v?.verdict !== "good") {
    log(`answer: ${name}'s reply not shown (review: ${v ? `${v.verdict} -- ${v.why}` : "no verdict"}); the session's model answers`);
    return null;
  }
  const secs = Math.round((Date.now() - started) / 1000);
  log(`answer: ${name} answered in ${secs} s; review: good`);
  return { handled: true, model: name, answer: reply,
           text: `Answered by ${name} in ${secs} s (kell answered it before this session's model ran; checked by ${v.model || "a reviewer"}).\n\n${reply}` };
}
