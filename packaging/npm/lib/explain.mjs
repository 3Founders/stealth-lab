// The explanation a user reads when a cheap model did their task: 2-4 plain lines -- what changed, why, how it was
// checked -- written by a cheap model from the request and the diff, and shown in the message the prompt hook returns.
// The session's own model never runs for it: Claude Code shows a blocking hook's reason as it is, so the explanation costs
// one cheap call (~$0.0003, 1-3 s), not an Opus turn.
//
// STEALTHLAB_EXPLAIN=off: no call; the message carries the cheap agent's own closing summary instead (as short), or
// nothing. STEALTHLAB_EXPLAIN_MODEL (default gpt-oss-120b, an exec.json profile) and STEALTHLAB_EXPLAIN_TIMEOUT_MS
// (default 6000) tune it. Never throws; a failed or slow call falls back to the agent's summary.
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.mjs";
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";

export const explainEnabled = (env = process.env) => (env.STEALTHLAB_EXPLAIN || "on").toLowerCase() !== "off";

export const EXPLAIN_SYSTEM = [
  "A developer asked for a code change and another model made it; its tests passed. Explain the change to the developer",
  "in 2-4 short plain sentences: what was changed and where, how it does what they asked, and anything they should know",
  "(an assumption made, an edge case, a limitation). No greeting, no headings, no code blocks, no praise. Name files and",
  "functions as they are. Never claim more than the diff shows.",
].join(" ");

const DIFF_MAX = 8000;
const OUT_MAX = 700;

// a reply or summary as at most OUT_MAX characters of plain text, or ""
export function tidy(text) {
  const t = String(text || "").replace(/```[\s\S]*?```/g, "").replace(/\s+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  return t.length <= OUT_MAX ? t : `${t.slice(0, OUT_MAX - 3).replace(/\s+\S*$/, "")}...`;
}

function ledger(env, row) {
  try {
    fs.mkdirSync(configDir(env), { recursive: true });
    fs.appendFileSync(path.join(configDir(env), "dispatch_costs.jsonl"), JSON.stringify({ at: new Date().toISOString(), ...row }) + "\n");
  } catch { /* the ledger is best-effort */ }
}

// -> the explanation text, or the agent's own summary, or "". Never throws, never waits past the timeout.
export async function explainChange({ request, diff, check, summary = "", env = process.env, fetchImpl = globalThis.fetch,
                                      log = () => {}, readConfig = readExecConfig }) {
  const fallback = tidy(summary);
  try {
    if (!explainEnabled(env) || !String(diff || "").trim()) return fallback;
    const name = String(env.STEALTHLAB_EXPLAIN_MODEL || "gpt-oss-120b").trim();
    const profile = readConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) { log(`explain: no profile for ${name}`); return fallback; }
    const user = `Request:\n${String(request || "").trim()}\n\nCheck that passed: ${check || "(none)"}\n\n` +
      `The change (git diff):\n${String(diff).slice(0, DIFF_MAX)}` + (summary ? `\n\nThe model's own note:\n${tidy(summary)}` : "");
    const messages = profile.no_system_role
      ? [{ role: "user", content: `${EXPLAIN_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: EXPLAIN_SYSTEM }, { role: "user", content: user }];
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(Math.max(500, Number(env.STEALTHLAB_EXPLAIN_TIMEOUT_MS || 6000))),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 400, temperature: 0, ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`explain: HTTP ${res.status}`); return fallback; }
    const body = await res.json();
    const u = body?.usage;
    if (u && (profile.input_per_mtok || profile.output_per_mtok)) {
      ledger(env, { what: "explain", model: name,
        cost_usd: ((u.prompt_tokens || 0) * (profile.input_per_mtok || 0) + (u.completion_tokens || 0) * (profile.output_per_mtok || 0)) / 1e6 });
    }
    return tidy(body?.choices?.[0]?.message?.content) || fallback;
  } catch (err) {
    log(`explain: ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return fallback;
  }
}
