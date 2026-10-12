#!/usr/bin/env node
// Offline replay of the learned time limit (lib/library.mjs learnedLimitS): would it have cut a cheap win, and how many seconds would it
// have saved where a cheap try ran long without winning?
//
//   node scripts/replay_learned_timeout.mjs [results_dir] [--mult 2]            coarse: the saved A-vs-C result rows
//   node scripts/replay_learned_timeout.mjs --runs <C_home/runs> [--mult 2]     accurate: the cheap agents' own run records
//
// RESULT ROWS (default). A route is a kind of task; a row only has the benchmark SUITE, so the suite stands in for the route. For each
// suite the rows are replayed in the order they were run: a delivered cheap win is "cut" when it took longer than the limit learned from the
// suite's earlier wins (last 20, at least 5; limit = max(60 s, mult x p90)). A row's time is the session's wall time, which carries the lookup
// and the test-first call, so it OVERSTATES the cheap tries' own time; the upper cap (today's 150 s + allowance) is left out for the same
// reason. The rows do not say how long a failed cheap try ran (Claude's own duration includes the hooks), so no seconds can be saved here.
//
// RUN RECORDS (--runs, from the resume bundle: runs/<id>/run.json). Each attempt has its own duration, and the run's instance_key starts with
// its Goal or routing key, which is the real route. A verified attempt is a win of that route; an attempt that did not verify (failed, or
// cancelled when the limit hit) and ran longer than the learned limit would have been cut at it, saving the difference.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { learnedLimitS } from "../lib/library.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const NO_CAP = Infinity;

const limitFrom = (prior, mult) => learnedLimitS(prior.slice(-20), { baseS: NO_CAP, mult });

export function replay(rows, { mult = 2 } = {}) {
  const bySuite = new Map();
  const out = { wins: 0, winsCut: 0, cutWins: [], suites: {} };
  for (const r of rows.filter((x) => x.arm === "C" && x.status === "done").sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)))) {
    const prior = bySuite.get(r.suite) || [];
    const s = (out.suites[r.suite] ||= { wins: 0, cut: 0 });
    const cheapWin = (r.attempts || []).some((a) => a.state === "verified" && a.role !== "verifier" && !String(a.model || "").includes("claude")) && !r.turns;
    if (!cheapWin) continue;
    const limit = limitFrom(prior, mult);
    out.wins++; s.wins++;
    if (limit !== null && Number(r.seconds) > limit) { out.winsCut++; s.cut++; out.cutWins.push(`${r.task_id} took ${r.seconds} s, limit ${limit} s`); }
    prior.push(Number(r.seconds));
    bySuite.set(r.suite, prior);
  }
  return out;
}

export function replayRuns(dir, { mult = 2 } = {}) {
  const recs = [];
  for (const id of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
    try { recs.push(JSON.parse(fs.readFileSync(path.join(dir, id, "run.json"), "utf8"))); } catch { /* not a run */ }
  }
  recs.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  const byRoute = new Map();
  const out = { wins: 0, winsCut: 0, cutWins: [], longNonWins: 0, secondsSaved: 0 };
  for (const r of recs) {
    const route = String(r.instance_key || "").split(".")[0] || "?";
    const prior = byRoute.get(route) || [];
    const limit = limitFrom(prior, mult);
    for (const a of r.attempts || []) {
      const d = Number(a.duration_s);
      if (!Number.isFinite(d) || a.usage_limited) continue;
      if (a.state === "verified") {
        out.wins++;
        if (limit !== null && d > limit) { out.winsCut++; out.cutWins.push(`${r.run_id}: ${d} s, limit ${limit} s`); }
        prior.push(d);
      } else if ((a.state === "failed" || a.state === "cancelled" || a.state === "timed_out") && limit !== null && d > limit) {
        out.longNonWins++;
        out.secondsSaved += d - limit;
      }
    }
    byRoute.set(route, prior);
  }
  out.secondsSaved = Math.round(out.secondsSaved);
  return out;
}

export function loadRows(dir) {
  const rows = new Map();
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith(".jsonl") && !x.includes(".bak"))) {
    for (const line of fs.readFileSync(path.join(dir, f), "utf8").split("\n")) {
      if (!line.trim()) continue;
      const r = JSON.parse(line);
      rows.set(`${f}|${r.task_id}|${r.arm}`, r);
    }
  }
  return [...rows.values()];
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : null; };
  const mult = Number(opt("--mult")) || 2;
  const runs = opt("--runs");
  if (runs) {
    const r = replayRuns(runs, { mult });
    console.log(`cheap wins replayed: ${r.wins}; cut by the learned limit (mult ${mult}): ${r.winsCut}${r.winsCut ? "\n  " + r.cutWins.join("\n  ") : ""}`);
    console.log(`attempts that did not win and ran past the learned limit: ${r.longNonWins}; seconds saved: ${r.secondsSaved}`);
  } else {
    const r = replay(loadRows(args[0] || path.join(root, "experiments", "abtest", "results_2026-10-11")), { mult });
    console.table(r.suites);
    console.log(`cheap wins replayed: ${r.wins}; cut by the learned limit (mult ${mult}, suite as the route): ${r.winsCut}${r.winsCut ? "\n  " + r.cutWins.join("\n  ") : ""}`);
  }
}
