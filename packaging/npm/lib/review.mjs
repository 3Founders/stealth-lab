// A cheap reviewer of a cheap model's answer: one call, shown the request and the patch, says whether the patch does
// what was asked. It is evidence for accepting an answer, alongside the project's own tests -- not a check by itself.
//
// Why: the tests StealthLab writes before the fix guess the interface or the behaviour about half the time, so as the
// only judge they accepted 11 cheap answers of which 2 were right and rejected 7 right ones (pipeline survey stage 3,
// 2026-10-16). A reviewer reads the change against the request instead; whether it helps is measured offline on stage
// 3's saved answers before any rule uses it. The prompt and the default model are fixed BEFORE that measurement
// (.scratch/acceptance_rules_preregistered_2026-10-16.md) and are not tuned on it.
// STEALTHLAB_REVIEW_MODEL names the model (an exec.json profile; default gpt-oss-120b); "off": no review.
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";

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

export async function reviewPatch({ prompt, diff, env = process.env, fetchImpl = globalThis.fetch, log = () => {}, timeoutMs = 90000 }) {
  const name = String(env.STEALTHLAB_REVIEW_MODEL || "gpt-oss-120b").trim();
  if (name.toLowerCase() === "off" || !String(diff || "").trim()) return null;
  try {
    const profile = readExecConfig(env).profiles?.[name];
    if (!profile?.openai_base_url) return null;
    const d = String(diff);
    const user = `The request:\n${String(prompt || "").slice(0, 6000)}\n\nThe change:\n` +
      (d.length > DIFF_MAX ? `${d.slice(0, DIFF_MAX)}\n[... the rest of the diff is cut]` : d);
    const messages = profile.no_system_role ? [{ role: "user", content: `${REVIEW_SYSTEM}\n\n${user}` }]
      : [{ role: "system", content: REVIEW_SYSTEM }, { role: "user", content: user }];
    const res = await fetchImpl(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 4000, temperature: 0, ...(profile.request_extras || {}) }),
    });
    if (!res.ok) { log(`review: HTTP ${res.status}`); return null; }
    const body = await res.json();
    const v = parseVerdict(body?.choices?.[0]?.message?.content);
    if (!v) { log("review: no usable verdict"); return null; }
    const u = body?.usage || {};
    const cost = (profile.input_per_mtok || profile.output_per_mtok)
      ? ((u.prompt_tokens || 0) * (profile.input_per_mtok || 0) + (u.completion_tokens || 0) * (profile.output_per_mtok || 0)) / 1e6 : null;
    log(`review: ${name} says ${v.verdict} (${v.why.slice(0, 120)})`);
    return { ...v, model: name, cost_usd: cost };
  } catch (err) {
    log(`review: ${err.name === "TimeoutError" ? "timed out" : err.message}`);
    return null;
  }
}

// the answer changes source code -- not only tests, docs or config (signal S of the acceptance rule)
export function changesSource(files = []) {
  const test = /(^|\/)(tests?|testing)\/|(^|\/)test_[^/]*\.py$|_test\.(py|go|js|ts)$|\.(test|spec)\.[jt]sx?$/i;
  const doc = /\.(md|rst|txt|cfg|ini|toml|ya?ml|json|lock)$|(^|\/)docs?\//i;
  return files.some((f) => !test.test(f) && !doc.test(f));
}
