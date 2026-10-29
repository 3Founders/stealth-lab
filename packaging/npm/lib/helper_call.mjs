// One short call for kell's side helpers (the brief, the explanation, the Way description): the primary model first, and
// if it has not answered within `hedgeMs`, a second model on another provider too -- the first usable reply wins and the
// other is stopped.
//
// Why: all three ran on one gemma-4-31b endpoint, whose time to a first token went from 1 s to 7+ s and back over one
// evening (General Compute, 2026-10-29): the brief timed out at 8 s and again at 15 s, the explanation at 6 s on every
// dispatch. Raising timeouts only moved the wait onto the user. The hedge costs one more fraction-of-a-cent call on a slow
// day and nothing on a fast one. STEALTHLAB_HELPER_FALLBACK_MODEL (an exec.json profile, default glm-5.3-flash; "off": none)
// and STEALTHLAB_HELPER_HEDGE_MS (default 6000) tune it.
//
// A refused call says why: the provider's own error text is logged with the status (a bare "HTTP 400" left the cause
// unknown, 2026-10-29).
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";
import { costOf } from "./spend.mjs";

const ERR_MAX = 160;

// a helper call needs little reasoning: a profile that thinks (z.ai's glm, gpt-oss) is asked to think briefly. glm-5.3-flash
// cannot stop thinking ("type: disabled" is refused, 1210: "please use low, high, or max"); reasoning_effort "low" is taken
function fastExtras(profile) {
  const x = { ...(profile.request_extras || {}) };
  if (x.thinking || x.reasoning_effort) x.reasoning_effort = "low";
  return x;
}

async function callOne({ name, profile, system, user, maxTokens, signal, env, fetchImpl }) {
  const messages = profile.no_system_role
    ? [{ role: "user", content: `${system}\n\n${user}` }]
    : [{ role: "system", content: system }, { role: "user", content: user }];
  const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
    method: "POST", redirect: "error", signal,
    headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
    // a thinking model's reasoning counts against max_tokens: with the reply's budget alone, glm's reply was cut mid-sentence
    body: JSON.stringify({ model: profile.model_id, messages, temperature: 0, ...fastExtras(profile),
                           max_tokens: maxTokens + (profile.request_extras?.thinking || profile.request_extras?.reasoning_effort ? 2000 : 0) }),
  });
  if (!res.ok) {
    let why = "";
    try { why = String(await res.text()).replace(/\s+/g, " ").slice(0, ERR_MAX); } catch { /* no body */ }
    throw new Error(`${name} HTTP ${res.status}${why ? `: ${why}` : ""}`);
  }
  const body = await res.json();
  return { text: String(body?.choices?.[0]?.message?.content || ""), cost: costOf(profile, body?.usage) };
}

// -> { value, model, cost } | null. `parse(text)` -> the value, or null for an unusable reply. Never throws.
export async function helperCall({ what, primary, system, user, maxTokens = 600, timeoutMs = 15000, parse = (t) => t.trim() || null,
                                   env = process.env, fetchImpl = globalThis.fetch, log = () => {}, record = () => {},
                                   readConfig = readExecConfig, hedgeMs = null, fallback = null }) {
  let profiles = {};
  try { profiles = readConfig(env).profiles || {}; } catch { /* no config */ }
  const fb = String(fallback ?? env.STEALTHLAB_HELPER_FALLBACK_MODEL ?? "glm-5.3-flash").trim();
  const names = [primary, fb.toLowerCase() === "off" ? null : fb].filter((n, i, a) => n && profiles[n]?.openai_base_url && a.indexOf(n) === i);
  if (!names.length) { log(`${what}: no profile for ${primary}`); return null; }
  const hedge = Math.max(0, Number(hedgeMs ?? env.STEALTHLAB_HELPER_HEDGE_MS ?? 6000));
  const ctl = new AbortController();
  const total = setTimeout(() => ctl.abort(new DOMException("timed out", "TimeoutError")), Math.max(500, timeoutMs));
  const errors = [];
  try {
    return await new Promise((resolve) => {
      let left = 0, done = false, started = 0;
      const finish = (v) => { if (!done) { done = true; resolve(v); } };
      const start = (name) => {
        started++; left++;
        callOne({ name, profile: profiles[name], system, user, maxTokens, signal: ctl.signal, env, fetchImpl })
          .then(({ text, cost }) => {
            if (cost !== null && cost !== undefined) record({ what, model: name, cost_usd: cost });
            const value = parse(text);
            if (value !== null && value !== undefined) finish({ value, model: name, cost });
            else errors.push(`${name} gave no usable reply`);
          })
          .catch((err) => errors.push(err.name === "TimeoutError" || ctl.signal.aborted ? `${name} timed out` : err.message))
          .finally(() => {
            left--;
            // the primary failed before the hedge was due: the fallback starts now
            if (!done && left === 0 && started < names.length) { clearTimeout(hedgeTimer); start(names[started]); }
            else if (!done && left === 0) finish(null);
          });
      };
      start(names[0]);
      const hedgeTimer = names.length > 1 ? setTimeout(() => { if (!done && started < names.length) start(names[started]); }, hedge) : null;
      ctl.signal.addEventListener("abort", () => { clearTimeout(hedgeTimer); setTimeout(() => finish(null), 0); });
    }).then((r) => {
      if (r && r.model !== names[0]) log(`${what}: ${names[0]} was slow; ${r.model} answered`);
      if (!r) log(`${what}: ${errors.length ? errors.join("; ") : "timed out"}`);
      return r;
    });
  } finally {
    clearTimeout(total);
    ctl.abort();                           // the loser, if any, stops here
  }
}
