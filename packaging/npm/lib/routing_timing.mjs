// How long routing takes: from the moment the prompt hook has the prompt until the first cheap model starts, or until
// the decision not to dispatch. Off unless STEALTHLAB_ROUTING_TIMING=1 (docs/latencycheck.md). When on, one JSON line
// per prompt is appended to <STEALTHLAB_HOME>/routing_timing.jsonl, next to hooks.log. It never holds prompt text, only
// its length and a short hash, and it never changes, delays or blocks anything: every step here fails open.
//
// Marks (milliseconds since the hook started, from performance.now()):
//   triage_start / triage_end        the server's /triage judgment (hook.mjs callTriage)
//   find_ways_start / find_ways_end  the find_ways round trip over MCP, which carries the model plan
//   dispatch_start                   dispatch() was entered
//   gate_end                         dispatch's checks are done: plan, check, candidates (a quota lookup), the judge's verdict
//   brief_end                        the brief (a model call, STEALTHLAB_DISPATCH_BRIEF)
//   locate_end                       where the task is in the code (a model call when STEALTHLAB_DISPATCH_LOCATE is on)
//   first_rung                       the first cheap model was started
//   dispatch_end                     dispatch() returned (its whole run when a cheap model ran)
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { configDir } from "./config.mjs";

export function routingTimingEnabled(env = process.env) {
  return /^(1|on|true|yes)$/i.test(String(env.STEALTHLAB_ROUTING_TIMING || "").trim());
}

export function routingTimingFile(env = process.env) {
  return path.join(configDir(env), "routing_timing.jsonl");
}

// A timer that does nothing: what every caller gets when timing is off, so the hot path has no branches.
const OFF = Object.freeze({ on: false, mark() {}, set() {}, finish() {} });

// -> { on, mark(name), set(key, value), finish(extra) }. `surface`: "claude-code" or "cursor".
export function routingTimer({ env = process.env, prompt = "", surface = "claude-code", now = () => performance.now() } = {}) {
  if (!routingTimingEnabled(env)) return OFF;
  try {
    const t0 = now();
    const marks = {};
    // the hook process's own start-up (node, imports, settings) came before t0: kept apart, it is not routing
    const fields = { process_ms: Math.round(t0) };
    let done = false;
    const text = String(prompt || "");
    return {
      on: true,
      mark(name) { try { if (!(name in marks)) marks[name] = Math.round(now() - t0); } catch { /* fail open */ } },
      set(key, value) { try { fields[key] = value; } catch { /* fail open */ } },
      finish(extra = {}) {
        if (done) return;
        done = true;
        try {
          const row = {
            ts: new Date().toISOString(), surface, prompt_len: text.length,
            prompt_hash: crypto.createHash("sha256").update(text).digest("hex").slice(0, 12),
            marks, ...summarize(marks), ...fields, ...extra,
          };
          const file = routingTimingFile(env);
          fs.mkdirSync(path.dirname(file), { recursive: true });
          fs.appendFileSync(file, JSON.stringify(row) + "\n");
        } catch { /* fail open: timing never affects the prompt */ }
      },
    };
  } catch {
    return OFF;
  }
}

// The parts, in milliseconds, from the marks; a part that did not happen is left out.
export function summarize(m) {
  const d = (a, b) => (Number.isFinite(m[a]) && Number.isFinite(m[b]) ? m[b] - m[a] : undefined);
  const out = {
    triage_ms: d("triage_start", "triage_end"),
    find_ways_ms: d("find_ways_start", "find_ways_end"),
    gate_ms: d("dispatch_start", "gate_end"),
    brief_ms: d("gate_end", "brief_end"),
    locate_ms: d("brief_end", "locate_end"),
    setup_ms: d("locate_end", "first_rung"),
  };
  // routing ends when the first cheap model starts; without one, when the decision not to dispatch was made
  const end = Number.isFinite(m.first_rung) ? m.first_rung
    : Number.isFinite(m.dispatch_end) ? m.dispatch_end
      : Math.max(...["find_ways_end", "triage_end"].map((k) => (Number.isFinite(m[k]) ? m[k] : -1)));
  if (end >= 0) out.routing_ms = end;
  out.dispatched = Number.isFinite(m.first_rung);
  return Object.fromEntries(Object.entries(out).filter(([, v]) => v !== undefined));
}
