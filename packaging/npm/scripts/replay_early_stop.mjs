#!/usr/bin/env node
// Offline replay of the early-stop rules (lib/agent/stall.mjs) over saved cheap-agent runs: how many FAILING runs would they
// have stopped, and how much sooner, and -- the number that decides whether a rule is too eager -- how many runs that went on to
// PASS would they have stopped (it must be 0).
//
//   node scripts/replay_early_stop.mjs <runs_dir>        e.g. the bundle's C_home/runs  (each runs/<id>/ has run.json and a<N>.stdout.log)
//
// What a saved run keeps: the agent's progress lines ("step 7: edit_file calc.py (error)") and its final line -- the tool NAMES and
// whether a call errored, not their outputs or the files. So this replays what a log can show and says what it cannot:
//   rule c  exact: three "(error)" lines in a row
//   rule d  an UNDER-estimate: six read-only calls (read_file / list_files / search) in a row after the first edit or write; a
//           run_shell in between could have changed the worktree, so any run_shell resets the count
//   rule e  an UNDER-estimate too: 48 calls with no edit, write or run_shell (a shell command could have changed the worktree)
//   rules a and b need the check output and the file contents, which the log does not keep: not replayable from it. They are
//           covered by the unit tests; run the live arm to see them.
// Seconds saved are estimated as the attempt's duration x (steps after the stop / all steps): the log has no per-step times.
import fs from "node:fs";
import path from "node:path";

const READ_ONLY = new Set(["read_file", "list_files", "search"]);

export function replayLog(text, { errorRun = 3, noChangeSteps = 6 } = {}) {
  const steps = [];
  for (const line of String(text).split(/\r?\n/)) {
    const m = /^step (\d+): (\w+)(?: (\S+))?( \(error\))?$/.exec(line.trim());
    if (m && m[2] !== "asking") steps.push({ n: Number(m[1]), tool: m[2], error: Boolean(m[4]) });
  }
  let errors = 0, still = 0, started = false, shell = false;
  for (const [i, s] of steps.entries()) {
    errors = s.error ? errors + 1 : 0;
    if (errors >= errorRun) return { rule: "c", at: i + 1, of: steps.length };
    if (s.tool === "edit_file" || s.tool === "write_file") { started = true; still = 0; }
    else if (s.tool === "run_shell") { still = 0; shell = true; }
    else if (started && READ_ONLY.has(s.tool)) still++;
    if (started && still >= noChangeSteps) return { rule: "d", at: i + 1, of: steps.length };
    if (!started && !shell && i + 1 >= noChangeSteps * 8) return { rule: "e", at: i + 1, of: steps.length };
  }
  return { rule: null, at: steps.length, of: steps.length };
}

export function replayDir(dir) {
  const out = { runs: 0, failed: 0, passed: 0, stoppedFailed: 0, stoppedPassed: 0, secondsSaved: 0, passedStopped: [] };
  for (const id of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
    let rec;
    try { rec = JSON.parse(fs.readFileSync(path.join(dir, id, "run.json"), "utf8")); } catch { continue; }
    for (const [i, a] of (rec.attempts || []).entries()) {
      let log;
      try { log = fs.readFileSync(path.join(dir, id, `a${i + 1}.stdout.log`), "utf8"); } catch { continue; }
      if (!a.did_not_run && !["verified", "failed", "timed_out"].includes(a.state)) continue;
      out.runs++;
      const r = replayLog(log);
      const passed = a.state === "verified";
      passed ? out.passed++ : out.failed++;
      if (!r.rule) continue;
      if (passed) { out.stoppedPassed++; out.passedStopped.push(`${id} a${i + 1} (rule ${r.rule} at step ${r.at} of ${r.of})`); continue; }
      out.stoppedFailed++;
      const dur = Number(a.duration_s) || 0;
      out.secondsSaved += dur * (1 - r.at / Math.max(r.of, 1));
    }
  }
  out.secondsSaved = Math.round(out.secondsSaved);
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"))) {
  const dir = process.argv[2];
  if (!dir) { console.error("usage: node scripts/replay_early_stop.mjs <runs_dir>"); process.exit(2); }
  const r = replayDir(dir);
  console.log(`attempts replayed: ${r.runs} (${r.passed} passed, ${r.failed} failed)`);
  console.log(`failing attempts the rules would have stopped: ${r.stoppedFailed}; estimated seconds saved: ${r.secondsSaved}`);
  console.log(`attempts that PASSED but the rules would have stopped: ${r.stoppedPassed}${r.stoppedPassed ? "\n  " + r.passedStopped.join("\n  ") : ""}  (must be 0)`);
  process.exit(r.stoppedPassed === 0 ? 0 : 1);
}
