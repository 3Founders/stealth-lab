// The spend ledger (<kell home>/dispatch_costs.jsonl): one line per model call kell makes besides the cheap
// tries themselves -- the brief, the locate model, the reviewers, the survey's fill. Anyone totalling what kell
// cost reads this file (experiments/abtest/runner.py counts its lines in a task's time window). Before 2026-10-17 only
// the brief wrote here, so the Opus reviews, the locate model and the survey fill were missing from arm C's cost.
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.mjs";

// the cached part of a call's input (OpenAI-style usage.prompt_tokens_details.cached_tokens): z.ai caches a repeated prefix
// on its own and bills it at a fifth (GLM-5.3-flash: $0.15 input, $0.03 cached, docs.z.ai pricing, 2026-10-28). An agent
// resends its whole conversation every turn, so most of a run's input is cached; at the full price every GLM try counted
// ~3-4x what it cost. A provider that reports nothing cached (General Compute) is billed in full, as before.
export const cachedTokensOf = (usage) => Number(usage?.prompt_tokens_details?.cached_tokens ?? usage?.cached_tokens) || 0;

// tokens -> dollars at a profile's prices (cached input at cached_input_per_mtok when the profile has it), or null
export function tokenCost(profile, { input = 0, cached = 0, output = 0 } = {}) {
  if (!profile || !(profile.input_per_mtok || profile.output_per_mtok)) return null;
  const c = typeof profile.cached_input_per_mtok === "number" ? Math.min(Number(cached) || 0, Number(input) || 0) : 0;
  return (((Number(input) || 0) - c) * (profile.input_per_mtok || 0) + c * (profile.cached_input_per_mtok || 0) +
    (Number(output) || 0) * (profile.output_per_mtok || 0)) / 1e6;
}

// a profile's price for an OpenAI-style usage object -> dollars, or null without prices
export function costOf(profile, usage) {
  if (!usage) return null;
  return tokenCost(profile, { input: usage.prompt_tokens, cached: cachedTokensOf(usage), output: usage.completion_tokens });
}

// never throws: the ledger is best-effort
export function recordSpend(env, { what, model, cost_usd = null, tokens = null }) {
  try {
    fs.mkdirSync(configDir(env), { recursive: true });
    fs.appendFileSync(path.join(configDir(env), "dispatch_costs.jsonl"),
      JSON.stringify({ at: new Date().toISOString(), what, model, cost_usd, ...(tokens ? { tokens } : {}) }) + "\n");
  } catch { /* best effort */ }
}
