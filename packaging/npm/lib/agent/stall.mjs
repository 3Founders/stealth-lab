// Stop a cheap run that is stuck, early.
//
// Measured on the A-vs-C runs (handoff 6.6): of the 28 tasks where the cheap tries failed and the session's model took over, 20
// waited out the full time limit (median +164 s over plain Claude Code). A cheap agent that is going round in circles is not
// going to get out of it; stopping it at once hands the task to the session's model sooner and saves what it would have
// spent. The rules are deterministic and look only at what the agent did -- no model, nothing trained:
//
//   a  the same check failed twice with the same failing tests (or the same first error line) and the edits between the two
//      runs touched nothing the failure points at: the agent changed things that cannot matter
//   b  a file came back to a content it already had (an edit undone, then redone): it is oscillating
//   c  three tool calls in a row failed (an edit that never matches, a path that does not exist): it is not reading its errors
//   d  six tool calls in a row without any change to the worktree's diff, AFTER the first change was made: it has stopped
//      making progress (exploring before the first edit is never counted: the first reads and searches are the work).
//      A read, search or listing not made before is new information, not a stall: it counts only toward 3x the threshold
//   e  eight times that many tool calls (48) and still no change at all: it is only reading. gpt-oss searched and re-read
//      the same file 55-75 times without an edit in 8 of 9 runs and always ran out the full 600 s; the runs that did pass
//      made their first change within 34 calls (cache-full-1, jsonargparse, 2026-10-27)
//
// A stalled run is reported as failed (with the rule that stopped it) and counted against its model: it did fail. Thresholds:
// STEALTHLAB_EARLY_STOP_STEPS (d, default 6), STEALTHLAB_EARLY_STOP_ERRORS (c, default 3). STEALTHLAB_EARLY_STOP=off turns the
// stop off (read by the stealth executor, which passes it to the agent: the agent's own env carries no STEALTHLAB_ variable).
export const earlyStopEnabled = (env = process.env) => (env.STEALTHLAB_EARLY_STOP || "on").toLowerCase() !== "off";

const norm = (p) => String(p || "").replace(/\\/g, "/").replace(/^\.\//, "");

// The failing tests and the first error line of a check's output, for comparing two failures of the same command.
export function failureSignature(output) {
  const text = String(output || "");
  const names = new Set();
  for (const m of text.matchAll(/^(?:FAILED|ERROR)\s+(\S+)/gm)) names.add(m[1].replace(/\s+-\s.*$/, ""));
  for (const m of text.matchAll(/^\s*(?:✕|×|FAIL)\s+(\S.*)$/gm)) names.add(m[1].trim());            // jest / vitest
  for (const m of text.matchAll(/^--- FAIL:\s+(\S+)/gm)) names.add(m[1]);                          // go test
  for (const m of text.matchAll(/^test\s+(\S+)\s+\.\.\.\s+FAILED/gm)) names.add(m[1]);             // cargo test
  const first = (text.match(/^.*(?:Error|error|assert|Assertion|Exception|panic)\b.*$/m) || [""])[0].trim().slice(0, 200);
  return { names: [...names].sort(), first };
}

// The files a failure points at: every path with a code-ish extension in the output.
export function failurePaths(output) {
  const out = new Set();
  for (const m of String(output || "").matchAll(/([A-Za-z0-9_./\\-]+\.(?:py|m?[jt]sx?|go|rs|sql|rb|java|kt|c|cc|cpp|h|hpp|cs|php|sh))\b/g)) out.add(norm(m[1]));
  return out;
}

const touches = (edited, paths) => {
  for (const e of edited) for (const p of paths) if (p === e || p.endsWith(`/${e}`) || e.endsWith(`/${p}`)) return true;
  return false;
};

const exitOf = (result) => {
  const m = /^\[(?:exit (\d+)|timed out)\]/.exec(String(result || ""));
  if (!m) return null;
  return m[1] === undefined ? 124 : Number(m[1]);
};

export class StallDetector {
  constructor({ noChangeSteps = 6, errorRun = 3 } = {}) {
    this.noChangeSteps = noChangeSteps;
    this.errorRun = errorRun;
    this.step = 0;
    this.errors = 0;
    this.hashes = new Map();         // path -> Set of content hashes it has had
    this.last = new Map();           // path -> the hash it has now
    this.edited = new Set();         // paths edited since the last failing check
    this.lastFail = null;            // {cmd, sig, paths}
    this.fp = null;                  // the worktree's fingerprint now
    this.started = false;            // a change has been made
    this.still = 0;                  // tool calls since the fingerprint last changed (once started) that brought nothing new
    this.browse = 0;                 // reads/searches of something not read before, since the last change
    this.seenCalls = new Set();      // read/search/list calls made so far (name + arguments)
  }

  // the worktree as the run starts
  start(fingerprint) { this.fp0 = fingerprint; this.fp = fingerprint; }

  // before a file's FIRST edit: what it holds now (null: it does not exist yet), so putting it back counts as a return
  seed(file, hash) {
    const p = norm(file);
    if (!this.hashes.has(p)) { this.hashes.set(p, new Set([hash])); this.last.set(p, hash); }
  }

  // One tool call done. info: { name, args, result, hash (the edited file's content hash after a successful edit/write),
  // fingerprint (the worktree's, after an edit/write/run_shell) }. -> null | { rule, why }
  observe({ name, args = {}, result = "", hash = null, fingerprint = undefined }) {
    this.step++;
    const failed = /^error:/.test(String(result));
    this.errors = failed ? this.errors + 1 : 0;

    if ((name === "edit_file" || name === "write_file") && !failed && hash !== null) {
      const p = norm(args.path);
      const seen = this.hashes.get(p) || new Set();
      if (seen.has(hash) && this.last.get(p) !== hash) {
        return { rule: "b", why: `${p} came back to a content it already had (an edit undone and redone)` };
      }
      seen.add(hash);
      this.hashes.set(p, seen);
      this.last.set(p, hash);
      this.edited.add(p);
    }

    if (name === "run_shell" && !failed) {
      const code = exitOf(result);
      if (code === 0) this.lastFail = null;
      else if (code !== null) {
        const cmd = String(args.command || "").trim();
        const sig = failureSignature(result);
        const fails = sig.names.length > 0 || sig.first !== "";
        const paths = failurePaths(result);
        if (fails && this.lastFail && this.lastFail.cmd === cmd
            && JSON.stringify(this.lastFail.sig) === JSON.stringify(sig) && !touches(this.edited, new Set([...paths, ...this.lastFail.paths]))) {
          return { rule: "a", why: `the check failed again the same way (${sig.names.slice(0, 3).join(", ") || sig.first}) and the edits since touched nothing it points at` };
        }
        this.lastFail = fails ? { cmd, sig, paths } : null;
        this.edited = new Set();
      }
    }

    if (this.errors >= this.errorRun) return { rule: "c", why: `${this.errors} tool calls in a row failed` };

    // a read, search or listing it has not made before brings new information: on a large repository the agent often
    // writes a scratch script first and then has to read its way to the code (Modal, jsonargparse: 7 of 25 cheap runs
    // were stopped by rule d while reading new parts of the code). Only a repeat counts toward d; new reads count toward
    // a looser ceiling (3x), so endless browsing still stops.
    let fresh = false;
    if ((name === "read_file" || name === "search" || name === "list_files") && !failed) {
      const key = `${name}|${JSON.stringify(args, Object.keys(args || {}).sort())}`;
      fresh = !this.seenCalls.has(key);
      this.seenCalls.add(key);
    }
    // a shell command whose result it has not seen before is new information too: after editing, agents run the tests
    // and probes 5-6 times in a row while debugging, and rule d stopped every one of them mid-way (Modal, sqlglot-4051:
    // 4 of 4 stalled runs). The same command with the same output again is the loop rule d is for.
    if (name === "run_shell") {
      const key = `run_shell|${String(args.command || "").trim()}|${String(result).slice(0, 4000)}`;
      fresh = !this.seenCalls.has(key);
      this.seenCalls.add(key);
    }
    if (fingerprint !== undefined && fingerprint !== null) {
      if (fingerprint !== this.fp) { this.fp = fingerprint; this.started = true; this.still = 0; this.browse = 0; }
      else if (this.started) { if (fresh) this.browse++; else this.still++; }
    } else if (this.started) { if (fresh) this.browse++; else this.still++; }
    if (this.started && this.browse + this.still >= this.noChangeSteps * 3) {
      return { rule: "d", why: `${this.browse + this.still} tool calls in a row without any change to the worktree` };
    }
    if (!this.started && this.fp !== null && this.fp0 !== undefined && this.fp !== this.fp0) this.started = true;
    if (!this.started && this.step >= this.noChangeSteps * 8) {
      return { rule: "e", why: `${this.step} tool calls without any change to the worktree` };
    }
    if (this.started && this.still >= this.noChangeSteps) {
      return { rule: "d", why: `${this.still} tool calls in a row without any change to the worktree` };
    }
    return null;
  }
}

