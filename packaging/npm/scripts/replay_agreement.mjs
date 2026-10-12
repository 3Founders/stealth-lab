#!/usr/bin/env node
// Offline replay for the agreement check (lib/agree.mjs): of the cheap answers that passed their checks but failed the hidden grade in the
// saved A-vs-C runs, how many would it have caught?
//
//   node scripts/replay_agreement.mjs [results_dir]     (default: experiments/abtest/results_2026-10-11; use the current system's run: phase1)
//
// It can catch an answer only when (1) a SECOND cheap answer passed too (the two are compared) and (2) the answers define Python functions the
// tests call. This prints both facts for every such row. The saved rows keep each attempt's model and final state, not the answers
// themselves, so whether two answers would have DISAGREED cannot be replayed here; the upper bound is the rows where both conditions hold.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
// the suites whose answers are Python functions called by pytest tests (see experiments/abtest/*_task.py): the others are a SQL query
// (bird), a code snippet that assigns a variable (ds1000), prose (ifeval), a yes/no file (boolq), a web app (openapi), JavaScript or a page
const FUNCTION_SUITES = new Set(["bcb", "mbpp"]);

export function replay(rows) {
  const out = [];
  for (const r of rows.filter((x) => x.arm === "C" && x.status === "done" && x.resolved === false && !x.turns)) {
    const cheap = (r.attempts || []).filter((a) => a.role !== "verifier" && !String(a.model || "").includes("claude"));
    if (!cheap.some((a) => a.state === "verified")) continue;
    out.push({ task: r.task_id, suite: r.suite, attempts: cheap.map((a) => `${a.model}:${a.state}`).join(" "),
               secondPass: cheap.filter((a) => a.state === "verified").length >= 2, functions: FUNCTION_SUITES.has(r.suite) });
  }
  return out;
}

export function loadRun(dir, run) {
  return fs.readFileSync(path.join(dir, `${run}.jsonl`), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const dir = process.argv[2] || path.join(root, "experiments", "abtest", "results_2026-10-11");
  const rows = replay(loadRun(dir, "phase1"));
  for (const r of rows) console.log(`${r.task.padEnd(12)} ${r.suite.padEnd(8)} second cheap pass: ${r.secondPass ? "yes" : "no "}  functions: ${r.functions ? "yes" : "no "}  ${r.attempts}`);
  const both = rows.filter((r) => r.secondPass && r.functions).length;
  console.log(`\ncheap answers that passed but failed the hidden grade (phase 1, current system): ${rows.length}`);
  console.log(`  with a second cheap pass: ${rows.filter((r) => r.secondPass).length}; with Python functions to compare: ${rows.filter((r) => r.functions).length}; both: ${both}`);
  console.log(`  the agreement check could have caught at most ${both} of them`);
}
