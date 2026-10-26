// The spend ledger (<kell home>/dispatch_costs.jsonl): one line per model call kell makes besides the cheap
// tries themselves -- the brief, the locate model, the reviewers, the survey's fill. Anyone totalling what kell
// cost reads this file (experiments/abtest/runner.py counts its lines in a task's time window). Before 2026-10-17 only
// the brief wrote here, so the Opus reviews, the locate model and the survey fill were missing from arm C's cost.
import fs from "node:fs";
import path from "node:path";
import { configDir } from "./config.mjs";

// a profile's price for an OpenAI-style usage object -> dollars, or null without prices
export function costOf(profile, usage) {
  if (!profile || !(profile.input_per_mtok || profile.output_per_mtok) || !usage) return null;
  return ((Number(usage.prompt_tokens) || 0) * (profile.input_per_mtok || 0) + (Number(usage.completion_tokens) || 0) * (profile.output_per_mtok || 0)) / 1e6;
}

// never throws: the ledger is best-effort
export function recordSpend(env, { what, model, cost_usd = null, tokens = null }) {
  try {
    fs.mkdirSync(configDir(env), { recursive: true });
    fs.appendFileSync(path.join(configDir(env), "dispatch_costs.jsonl"),
      JSON.stringify({ at: new Date().toISOString(), what, model, cost_usd, ...(tokens ? { tokens } : {}) }) + "\n");
  } catch { /* best effort */ }
}
