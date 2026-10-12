#!/usr/bin/env node
// Offline replay: would the brief's deterministic guard (lib/brief.mjs guardKind) have blocked any cheap answer that was
// actually delivered in the saved A-vs-C runs? Every blocked one is a lost saving, so the number must be 0.
//
//   node scripts/replay_brief_guard.mjs [results_dir]     (default: experiments/abtest/results_2026-10-11)
//
// The saved rows keep only the first ~200 characters of each prompt ("Original prompt: ..." in a blocked row's `final`),
// so the fixed tail every suite's prompt() appends ("-- Write ... into solution.py. Check it with ...") is added back from
// experiments/abtest/*_task.py. Both numbers are printed: the guard on the prefix alone (what a guard that only looked at
// the start would do) and on the full prompt (what dispatch sees).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { guardKind } from "../lib/brief.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const dir = process.argv[2] || path.join(root, "experiments", "abtest", "results_2026-10-11");

// the fixed part of each suite's prompt, from experiments/abtest/<suite>_task.py
const TAIL = {
  ds1000: "The full problem is in TASK.md. Put your code in solution.py: only the code that goes where the problem's solution placeholder is (it is inserted into the problem's own context and must produce what the problem asks, e.g. assign `result`). Check it with `python -m pytest -q test_check.py`.",
  bcb: "Implement the function in solution.py (keep its name and signature; the task is in TASK.md). Check it with `python -m pytest -q test_check.py`.",
  mbpp: "Implement the function in solution.py (keep its name and signature; the task is in TASK.md). Check it with `python -m pytest -q test_check.py`.",
  ifeval: "Write your reply to the request in TASK.md into response.md (only the reply itself). Check it with `python -m pytest -q test_check.py`.",
  bird: "Write one SQLite query that answers it into query.sql (the full question, a hint and the database path are in TASK.md). Check it with `python -m pytest -q test_check.py`.",
  boolq: "Write exactly true or false into answer.txt. Check it with `python -m pytest -q test_check.py`.",
  openapi: "(details in TASK.md). Check it with `python -m pytest -q test_check.py`.",
  multiple_js: "Implement the function in solution.js (the task is in TASK.md). Check it with `node --test check.test.js`.",
};

const seen = new Set();
const bySuite = {};
for (const f of fs.readdirSync(dir).filter((x) => x.endsWith(".jsonl") && !x.includes(".bak"))) {
  for (const line of fs.readFileSync(path.join(dir, f), "utf8").split("\n")) {
    if (!line.trim()) continue;
    const r = JSON.parse(line);
    const m = r.arm === "C" ? /Original prompt: ([\s\S]*)$/.exec(String(r.final || "")) : null;
    if (!m) continue;
    const key = `${f}|${r.task_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const prefix = m[1].trim();
    const s = (bySuite[r.suite] ||= { delivered: 0, blockedOnPrefix: 0, blockedOnFull: 0 });
    s.delivered++;
    if (guardKind(prefix.split(" -- ")[0])) s.blockedOnPrefix++;
    if (guardKind(`${prefix.split(" -- ")[0]} -- ${TAIL[r.suite] || ""}`)) s.blockedOnFull++;
  }
}
console.table(bySuite);
const total = Object.values(bySuite).reduce((a, s) => ({ d: a.d + s.delivered, p: a.p + s.blockedOnPrefix, f: a.f + s.blockedOnFull }), { d: 0, p: 0, f: 0 });
console.log(`delivered cheap answers replayed: ${total.d}; guard would block on the prompt's opening alone: ${total.p}; on the full prompt dispatch sees: ${total.f}`);
process.exit(total.f === 0 ? 0 : 1);
