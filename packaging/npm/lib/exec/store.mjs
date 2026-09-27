// Local state for the executor layer: ~/.stealthlab (or $STEALTHLAB_HOME), the same root lib/config.mjs
// uses for the saved URL/token.
//
//   exec.json                     executor config: {"executors": {"<id>": {"models": [...]}}, "default_order": [...]}
//   runs/<run_id>/run.json        the run record (redacted task, state, per-attempt results)
//   runs/<run_id>/events.jsonl    state transitions, one JSON object per line (redacted)
//   runs/<run_id>/a<N>.stdout.log / a<N>.stderr.log   the executor's output, redacted line by line
//   runs/<run_id>/a<N>.patch      the attempt's diff (the product itself, kept raw so apply_run can use it)
//   outbox/*.json                 report_model_run payloads waiting to be sent (redacted)
//   outbox/held/*.json            payloads that can never be accepted as-is (no goal/procedure, no model)
//   outbox/rejected/*.json        payloads the hosted server refused permanently ("REFUSED: ...")
//
// Directories are created 0700 and files 0600, the discipline lib/config.mjs already follows.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function execHome(env = process.env) {
  return env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab");
}
export const runsDir = (env) => path.join(execHome(env), "runs");
export const runDir = (env, runId) => path.join(runsDir(env), runId);
export const outboxDir = (env) => path.join(execHome(env), "outbox");
export const execConfigPath = (env) => path.join(execHome(env), "exec.json");

export function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

export function writePrivate(file, text) {
  ensureDir(path.dirname(file));
  const tmp = `${file}.${process.pid}.${Math.random().toString(16).slice(2)}.tmp`;
  fs.writeFileSync(tmp, text, { mode: 0o600 });
  fs.renameSync(tmp, file);
  try { fs.chmodSync(file, 0o600); } catch { /* best effort on Windows */ }
}

export function appendPrivate(file, text) {
  ensureDir(path.dirname(file));
  fs.appendFileSync(file, text, { mode: 0o600 });
}

export function readJson(file, fallback = undefined) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err.code === "ENOENT") return fallback;
    throw new Error(`${file} is not valid JSON (${err.message}) -- fix or delete it`);
  }
}

export function readExecConfig(env = process.env) {
  const cfg = readJson(execConfigPath(env), {}) || {};
  return {
    executors: cfg.executors && typeof cfg.executors === "object" ? cfg.executors : {},
    default_order: Array.isArray(cfg.default_order) ? cfg.default_order.map(String) : [],
    hang_s: Number(cfg.hang_s) > 0 ? Number(cfg.hang_s) : undefined,
    race_interval_width: Number(cfg.race_interval_width) > 0 ? Number(cfg.race_interval_width) : 0.5,
    max_concurrent_runs: Number(cfg.max_concurrent_runs) > 0 ? Number(cfg.max_concurrent_runs) : 4,
  };
}

export function newRunId(now = new Date()) {
  const ts = now.toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
  return `r-${ts}-${Math.random().toString(16).slice(2, 8)}`;
}

export const RUN_ID_RE = /^r-\d{14}-[0-9a-f]{1,8}$/;

// Line-buffered, redacting log writer: a secret split across two chunks is still seen whole, because
// redaction runs on complete lines only (the tail is flushed on close()).
export function redactingLog(file, redactFn) {
  let buf = "";
  ensureDir(path.dirname(file));
  fs.writeFileSync(file, "", { mode: 0o600, flag: "a" });
  return {
    write(chunk) {
      buf += chunk;
      const i = buf.lastIndexOf("\n");
      if (i === -1) {
        if (buf.length > 65536) { fs.appendFileSync(file, redactFn(buf)); buf = ""; }
        return;
      }
      fs.appendFileSync(file, redactFn(buf.slice(0, i + 1)));
      buf = buf.slice(i + 1);
    },
    close() {
      if (buf) fs.appendFileSync(file, redactFn(buf));
      buf = "";
    },
  };
}
