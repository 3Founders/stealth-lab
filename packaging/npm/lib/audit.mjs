// Audit: was a cheap answer that passed its check REALLY right? (report_audit on the hosted server.)
//
// The router decides whether to try the cheap models from how often a check lets a wrong answer through. Nothing
// else ever tells it: an accepted answer is just "accepted". So, for a share of the dispatched answers
// (STEALTHLAB_DISPATCH_AUDIT, default 0.1), a Claude model -- the test writer's -- reads the task and the applied diff
// and says CORRECT or WRONG, and the verdict is reported for that plan instance. The server turns the verdicts of one
// kind of task into that kind's own false-accept rate (routing/service.py calibrated_check_rates).
//
// It runs DETACHED, after the answer is delivered: `node audit.mjs <job.json>` -- the user never waits for it.
// A reviewer is not ground truth: when it errs it mostly calls a right answer wrong, which only makes the router more
// careful (fewer cheap tries), never ships a wrong answer.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

export const AUDIT_SYSTEM = [
  "You review a change another model made for a task. Its tests passed; decide whether the change is a CORRECT and",
  "complete solution of the task as written -- not whether it is pretty. Judge only what the task asks for. Reply with",
  "one line: CORRECT or WRONG, then a colon and at most 20 words why.",
].join(" ");

const DIFF_MAX = 12000;

export function auditRate(env = process.env) {
  // under `node --test` an unset rate is 0: a test that delivers an answer must not start a real, paid review
  const r = Number(env.STEALTHLAB_DISPATCH_AUDIT ?? (process.env.NODE_TEST_CONTEXT ? 0 : 0.1));
  return Number.isFinite(r) ? Math.min(1, Math.max(0, r)) : 0;
}

// -> true | false | null (no clear verdict)
export function parseVerdict(text) {
  const m = /^\W*(CORRECT|WRONG)\b/i.exec(String(text || "").trim());
  return m ? m[1].toUpperCase() === "CORRECT" : null;
}

// Start a detached audit of an applied answer, when this one is drawn. Never throws; returns whether one started.
export function startAudit({ instanceKey, prompt, diff, model, env = process.env, rand = Math.random, log = () => {} }) {
  try {
    if (!instanceKey || !diff?.trim() || !String(model || "").includes("claude")) return false;
    if (!(rand() < auditRate(env))) return false;
    const dir = path.join(env.STEALTHLAB_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".stealthlab"), "audits");
    fs.mkdirSync(dir, { recursive: true });
    const job = path.join(dir, `${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`);
    fs.writeFileSync(job, JSON.stringify({ instance_key: instanceKey, prompt: String(prompt || ""),
                                           diff: diff.slice(0, DIFF_MAX), model }));
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), job],
      { detached: true, stdio: "ignore", windowsHide: true, env });
    child.unref();
    return true;
  } catch (err) {
    log(`audit not started (${err.message})`);
    return false;
  }
}

// The detached part: review, report, record the cost in dispatch's ledger. `runClaude`/`report` for tests.
export async function runAudit(jobFile, { env = process.env, runClaude, report } = {}) {
  const job = JSON.parse(fs.readFileSync(jobFile, "utf-8"));
  const ask = `Task:\n${job.prompt.trim()}\n\nThe change (git diff):\n${job.diff}\n\nIs it CORRECT or WRONG?`;
  const args = ["-p", "--model", job.model, "--system-prompt", AUDIT_SYSTEM,
    "--tools", "", "--setting-sources", "user", "--strict-mcp-config", "--output-format", "json"];
  const run = runClaude || (async (a, input) => {
    const { launchOrThrow, claudeChildEnv } = await import("./executors/common.mjs");
    const launch = launchOrThrow("claude", { env });
    return await new Promise((resolve) => {
      const c = spawn(launch.cmd, [...launch.prefix, ...a], { windowsHide: true, env: claudeChildEnv(env) });
      let stdout = "";
      const timer = setTimeout(() => c.kill(), 120000);
      c.stdout.on("data", (d) => { stdout += d; });
      c.on("error", () => {});
      c.on("close", (status) => { clearTimeout(timer); resolve({ status, stdout }); });
      c.stdin.end(input);
    });
  });
  const r = await run(args, ask);
  let j = null;
  try { j = JSON.parse(String(r.stdout || "").trim().split(/\r?\n/).pop()); } catch { /* no verdict */ }
  const cost = typeof j?.total_cost_usd === "number" ? j.total_cost_usd : null;
  try {
    const home = env.STEALTHLAB_HOME || path.dirname(path.dirname(jobFile));
    fs.appendFileSync(path.join(home, "dispatch_costs.jsonl"),
      JSON.stringify({ at: new Date().toISOString(), what: "audit", model: job.model, cost_usd: cost }) + "\n");
  } catch { /* the ledger is best-effort */ }
  const correct = j && !j.is_error ? parseVerdict(j.result) : null;
  if (correct !== null) {
    const send = report || (async (args2) => {
      const { callHostedTool } = await import("./exec/hosted.mjs");
      return callHostedTool("report_audit", args2, { env });
    });
    await send({ instance_key: job.instance_key, correct });
  }
  try { fs.unlinkSync(jobFile); } catch { /* */ }
  return { correct, cost_usd: cost };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url) && process.argv[2]) {
  runAudit(process.argv[2]).catch(() => {}).finally(() => process.exit(0));
}
