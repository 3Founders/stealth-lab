#!/usr/bin/env node
// Offline replay of the per-MODEL time cap (lib/model_speed.mjs) over saved cheap-agent run records.
//
//   node scripts/replay_model_cap.mjs --runs <C_home/runs> [--mult 2] [--floor 60] [--base 150]
//
// Each run directory has run.json with its attempts (model, executor, state, duration_s). For every model, attempts are replayed in the
// order they ran: the cap an attempt would have had is clamp(mult x p90 of that model's EARLIER wins (last 20, at least 5), floor, base).
// It prints, per model, how many attempts there were, how long its wins take, and how many attempts were still going at 100 s (the number
// that singled out minimax-m2-7: 20 of the 27 attempts still running at 100 s); then, for the cap:
//   * wins it would have cut          -- each one is a cheap answer lost, so this should be small
//   * failed / timed-out attempts that ran past their cap -- the seconds they would have given back (duration - cap)
//   * attempts cancelled by a sibling's pass that ran past their cap -- cost saved, no effect on when the answer arrives
// A capped attempt is cancelled and is NOT a failure, so none of these changes a model's recorded outcome.
// The results rows (results_*/phase*.jsonl) have no per-attempt duration, so this needs the run records from the resume bundle.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { median, modelCapS, p90 } from "../lib/model_speed.mjs";

export function loadAttempts(dir) {
  const recs = [];
  for (const id of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
    try { recs.push(JSON.parse(fs.readFileSync(path.join(dir, id, "run.json"), "utf8"))); } catch { /* not a run */ }
  }
  recs.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  const out = [];
  for (const r of recs) {
    for (const a of r.attempts || []) {
      const d = Number(a.duration_s);
      if (!Number.isFinite(d) || a.usage_limited || a.did_not_run) continue;
      out.push({ run: r.run_id, unit: `${a.model}|${a.executor}`, model: a.model, state: a.state, d });
    }
  }
  return out;
}

export function replay(attempts, { mult = 2, floorS = 60, baseS = 150 } = {}) {
  const wins = new Map();
  const per = new Map();
  const out = { winsCut: 0, wins: 0, cutWins: [], longNonWins: 0, secondsSaved: 0, longCancelled: 0, models: {} };
  for (const a of attempts) {
    const m = (out.models[a.unit] ||= { attempts: 0, wins: 0, over100: 0, winTimes: [] });
    m.attempts++;
    if (a.d > 100) m.over100++;
    const prior = wins.get(a.unit) || [];
    const cap = modelCapS(prior.slice(-20), { baseS, floorS, mult });
    if (a.state === "verified") {
      out.wins++; m.wins++; m.winTimes.push(a.d);
      if (cap !== null && a.d > cap) { out.winsCut++; out.cutWins.push(`${a.run} ${a.unit}: ${a.d} s, cap ${cap} s`); }
      prior.push(a.d);
      wins.set(a.unit, prior);
    } else if (cap !== null && a.d > cap) {
      if (a.state === "cancelled") out.longCancelled++;
      else if (a.state === "failed" || a.state === "timed_out") { out.longNonWins++; out.secondsSaved += a.d - cap; }
    }
    per.set(a.unit, m);
  }
  out.secondsSaved = Math.round(out.secondsSaved);
  const total100 = Object.values(out.models).reduce((n, m) => n + m.over100, 0);
  for (const m of Object.values(out.models)) {
    m.median = median(m.winTimes);
    m.p90 = p90(m.winTimes);
    m.shareOver100 = total100 ? Math.round((100 * m.over100) / total100) : 0;
  }
  out.total100 = total100;
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : null; };
  const runs = opt("--runs");
  if (!runs) { console.error("usage: node scripts/replay_model_cap.mjs --runs <C_home/runs> [--mult 2] [--floor 60] [--base 150]"); process.exit(2); }
  const o = { mult: Number(opt("--mult")) || 2, floorS: opt("--floor") !== null ? Number(opt("--floor")) : 60, baseS: Number(opt("--base")) || 150 };
  const r = replay(loadAttempts(runs), o);
  console.table(Object.fromEntries(Object.entries(r.models).map(([u, m]) => [u, { attempts: m.attempts, wins: m.wins, "win median s": m.median, "win p90 s": m.p90, "running at 100 s": m.over100, "share of those %": m.shareOver100 }])));
  console.log(`attempts still going at 100 s: ${r.total100}`);
  console.log(`cap (mult ${o.mult}, floor ${o.floorS} s, base ${o.baseS} s): wins cut ${r.winsCut} of ${r.wins}${r.winsCut ? "\n  " + r.cutWins.join("\n  ") : ""}`);
  console.log(`failed or timed-out attempts that ran past their cap: ${r.longNonWins}; seconds given back: ${r.secondsSaved}`);
  console.log(`attempts cancelled by a sibling's pass that ran past their cap (cost only): ${r.longCancelled}`);
}
