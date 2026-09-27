// Evidence client: every terminal run state becomes one hosted report_model_run call, or an outbox entry.
//
// What leaves the machine is exactly the whitelisted fields below (model, scaffold, accepted, ids, check
// kind, counters) -- every string passed through redact() first. No task text, summary, diff or check
// output is ever part of a report.
//
// Failure policy (spec section 4): offline, not logged in, HTTP 5xx/4xx, a JSON-RPC or tool error, or a
// server-side "undefined table" (routing migrations not applied yet) -> the payload is written to
// ~/.stealthlab/outbox/ and retried on the next call. That is never a failed run. A permanent refusal
// ("REFUSED: ...", e.g. an unknown goal) moves to outbox/rejected/ for audit instead of retrying forever;
// a payload the server could never accept (no goal_id/procedure_id, no model) goes to outbox/held/.
import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { callHostedTool, hostedSettings } from "./hosted.mjs";
import { redact } from "./redact.mjs";
import { ensureDir, outboxDir, readJson, writePrivate } from "./store.mjs";

export const REPORT_FIELDS = [
  "model", "scaffold", "accepted", "instance_key", "goal_id", "procedure_id", "version", "check_kind",
  "recommendation_id", "attempt_index", "pass_fraction", "tokens_in", "tokens_out", "tokens_cached",
  "cost_usd", "latency_ms", "step_order", "step_role",
];

export function sanitizeReport(payload, { secrets = [] } = {}) {
  const out = {};
  for (const k of REPORT_FIELDS) {
    const v = payload?.[k];
    if (v === undefined || v === null || v === "") continue;
    out[k] = typeof v === "string" ? redact(v, { secrets }).slice(0, 300) : v;
  }
  return out;
}

function holdReason(args) {
  if (!args.model) return "no model is known for this run (configure models in exec.json)";
  if (!args.goal_id && !args.procedure_id) return "report_model_run needs goal_id or procedure_id";
  if (!args.instance_key) return "no instance_key";
  return null;
}

function writeEntry(dir, entry) {
  const file = path.join(ensureDir(dir), `${Date.now()}-${randomUUID()}.json`);
  writePrivate(file, JSON.stringify(entry, null, 2) + "\n");
  return file;
}

function secretsFor(env) {
  const { token } = hostedSettings(env);
  return token ? [token] : [];
}

async function send(args, { env, fetchImpl, timeoutMs }) {
  const { token } = hostedSettings(env);
  if (!token) {
    const e = new Error("not logged in (run `stealthlab-mcp login --token <token>`)");
    e.transient = true;
    throw e;
  }
  const text = await callHostedTool("report_model_run", args, { env, fetchImpl, timeoutMs });
  try { return JSON.parse(text); } catch { return { raw: text.slice(0, 200) }; }
}

// flushOutbox -> {sent, remaining, rejected}. Oldest first; stops at the first transient failure so an
// offline machine does not pay one timeout per queued entry.
// Outbox access is serialized per outbox directory, so two runs finishing together never send one
// queued entry twice.
const locks = new Map();
function serialized(env, fn) {
  const key = outboxDir(env);
  const prev = locks.get(key) || Promise.resolve();
  const run = prev.then(fn, fn);
  const tail = run.catch(() => {});
  locks.set(key, tail);
  tail.then(() => { if (locks.get(key) === tail) locks.delete(key); });
  return run;
}

export function flushOutbox(opts = {}) {
  return serialized(opts.env || process.env, () => flushUnlocked(opts));
}

export function reportModelRun(payload, opts = {}) {
  return serialized(opts.env || process.env, () => reportUnlocked(payload, opts));
}

async function flushUnlocked({ env = process.env, fetchImpl, timeoutMs = 10000 } = {}) {
  const dir = outboxDir(env);
  let files = [];
  try { files = fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort(); } catch { return { sent: 0, remaining: 0, rejected: 0 }; }
  let sent = 0;
  let rejected = 0;
  for (let i = 0; i < files.length; i++) {
    const file = path.join(dir, files[i]);
    let entry;
    try { entry = readJson(file); } catch { continue; }
    if (!entry || entry.tool !== "report_model_run") continue;
    try {
      await send(entry.args, { env, fetchImpl, timeoutMs });
      fs.rmSync(file, { force: true });
      sent++;
    } catch (err) {
      if (err.transient === false) {
        writeEntry(path.join(dir, "rejected"), { ...entry, rejected_at: new Date().toISOString(), error: err.message });
        fs.rmSync(file, { force: true });
        rejected++;
        continue;
      }
      entry.attempts = (entry.attempts || 0) + 1;
      entry.last_error = redact(err.message).slice(0, 300);
      writePrivate(file, JSON.stringify(entry, null, 2) + "\n");
      return { sent, remaining: files.length - i, rejected };
    }
  }
  return { sent, remaining: 0, rejected };
}

// reportModelRun -> {reported, queued, held?, rejected?, error?, outbox_file?}
async function reportUnlocked(payload, { env = process.env, fetchImpl, timeoutMs = 10000 } = {}) {
  const args = sanitizeReport(payload, { secrets: secretsFor(env) });
  const dir = outboxDir(env);
  const held = holdReason(args);
  if (held) {
    const file = writeEntry(path.join(dir, "held"), { tool: "report_model_run", args, held: held, queued_at: new Date().toISOString() });
    return { reported: false, queued: false, held, outbox_file: file };
  }
  // Earlier failures go first, so the server sees attempts in order.
  const flushed = await flushUnlocked({ env, fetchImpl, timeoutMs }).catch(() => ({ remaining: 1 }));
  if (flushed.remaining === 0) {
    try {
      const reply = await send(args, { env, fetchImpl, timeoutMs });
      return { reported: true, queued: false, observation_id: reply?.observation_id };
    } catch (err) {
      if (err.transient === false) {
        const file = writeEntry(path.join(dir, "rejected"), { tool: "report_model_run", args, error: err.message, rejected_at: new Date().toISOString() });
        return { reported: false, queued: false, rejected: redact(err.message).slice(0, 300), outbox_file: file };
      }
      const file = writeEntry(dir, { tool: "report_model_run", args, queued_at: new Date().toISOString(), attempts: 1,
                                     last_error: redact(err.message).slice(0, 300) });
      return { reported: false, queued: true, error: redact(err.message).slice(0, 300), outbox_file: file };
    }
  }
  const file = writeEntry(dir, { tool: "report_model_run", args, queued_at: new Date().toISOString(), attempts: 0,
                                 last_error: "outbox not drained (hosted server unavailable)" });
  return { reported: false, queued: true, error: "hosted server unavailable; queued behind earlier entries", outbox_file: file };
}
