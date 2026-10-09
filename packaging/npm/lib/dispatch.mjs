// Dispatch: the model plan's cheap rungs run BEFORE the session's own model ever sees the prompt.
//
// A hook cannot switch Claude Code's model, so any plan the session itself carries out leaves that model reading,
// handing over and checking -- on a small task that costs more than the task. Here the prompt hook runs the plan's
// rungs that a local executor can run (open models through `stealthlab-mcp exec`), in a worktree, verified by the
// project's check:
//   * one passes: its change is applied, the attempt is recorded, and the prompt is stopped with a short report --
//     the session's model (Opus, say) never runs;
//   * all fail (or none can run here): the prompt goes on to the session with what was tried, and the plan resumes
//     at its next rung (usually the session's own model).
// Every attempt is reported to the plan (report_result, by the runtime), counted in .stealth/routing.md with the
// unit that ran it, and a pass becomes a library entry -- so the next plan learns from it.
//
// It needs a CHECK -- an unverified result never goes in: STEALTHLAB_DISPATCH_CHECK (the project's test command), or
// the check of the library entry the lookup matched. No check, no dispatch. STEALTHLAB_DISPATCH=off turns it off.
import { addEntry, diffFromGit, ensureRoute, readEntry, recordObs } from "./library.mjs";
import { executorFor } from "./model_guard.mjs";

const TERMINAL = new Set(["verified", "failed", "timed_out", "cancelled"]);
const TASK_MAX = 2000;

export function dispatchPolicy(env = process.env) {
  return {
    enabled: (env.STEALTHLAB_DISPATCH || "on").toLowerCase() !== "off",
    check: (env.STEALTHLAB_DISPATCH_CHECK || "").trim() || null,
    maxRungs: Math.max(1, Number(env.STEALTHLAB_DISPATCH_MAX_RUNGS || 3)),
    scope: String(env.STEALTHLAB_DISPATCH_SCOPE || "*,**/*").split(",").map((s) => s.trim()).filter(Boolean),
  };
}

// The plan's rungs, in order, that a local executor can run, up to the session's own model. Claude models count
// too when an executor runs them (headless Claude Code on Sonnet, say -- the `claude` executor): nothing cheaper
// than the session's model ever runs inside the session.
export function dispatchableRungs(plan, mine, env = process.env) {
  const out = [];
  for (const unit of plan?.ladder || []) {
    const model = String(unit).split("|")[0];
    if (mine && model === String(mine).split("|")[0]) break;
    const local = executorFor(unit, env);
    if (!local) break;
    if (!out.some((r) => r.unit === unit)) out.push({ unit, ...local });
  }
  return out;
}

// The check to verify a dispatched run with: the configured one, else the matched library entry's own check.
export function dispatchCheck(policy, reply, root) {
  if (policy.check) return policy.check;
  const match = (reply?.library_matches || []).find((m) => m?.relation === "matches" && m.id);
  if (!match || !root) return null;
  try {
    const e = readEntry(root, match.id);
    const steps = (e?.procs || []).flatMap((p) => p.steps || []);
    return steps.map((s) => s.check).filter(Boolean).pop() || null;
  } catch {
    return null;
  }
}

function taskText(prompt) {
  const t = String(prompt || "").trim();
  return t.length <= TASK_MAX ? t : `${t.slice(0, TASK_MAX - 40)}\n[...prompt truncated for the executor]`;
}

// -> {handled: true, text} when a cheap rung's verified change was applied; else {handled: false, tried, remaining}
//    (remaining: the plan's ladder after the rungs tried here), or null when dispatch does not apply.
export async function dispatch({ payload, reply, root, mine, env = process.env, fetchImpl, log = () => {}, runtime }) {
  const policy = dispatchPolicy(env);
  const plan = reply?.model_plan;
  if (!policy.enabled || !plan || plan.status !== "ok" || !root) return null;
  const rungs = dispatchableRungs(plan, mine, env).slice(0, policy.maxRungs);
  const check = dispatchCheck(policy, reply, root);
  if (!rungs.length || !check) return null;
  const rt = runtime || new (await import("./exec/runtime.mjs")).ExecRuntime({ env, fetchImpl });
  const tried = [];
  for (const rung of rungs) {
    let result;
    try {
      const started = await rt.achieve({ repo_path: payload.cwd || root, task: taskText(payload.prompt), checks: [check],
        scope: policy.scope, executor: rung.executor, model: rung.model, base: "working-tree",
        ...(plan.instance_key ? { instance_key: plan.instance_key } : {}) });
      do {
        result = await rt.runResult(started.run_id, 55);
      } while (!TERMINAL.has(result?.state));
    } catch (err) {
      log(`dispatch ${rung.unit}: ${err.message}`);
      tried.push({ unit: rung.unit, state: "error", error: String(err.message).slice(0, 200) });
      continue;
    }
    const ok = result.state === "verified";
    if (result.usage_limited) {            // the executor's account hit its limit: no verdict on the model
      tried.push({ unit: rung.unit, state: "usage_limited", run_id: result.run_id });
      continue;
    }
    try {
      if (plan.goal_id) recordObs(root, ensureRoute(root, plan.goal_id), rung.model, rung.executor, ok);
    } catch { /* the record is best-effort */ }
    tried.push({ unit: rung.unit, state: result.state, run_id: result.run_id, cost_usd: result.cost_usd,
                 tail: ok ? "" : String((result.checks || []).map((c) => c.tail || "").join("\n")).slice(-600) });
    if (!ok) continue;
    const applied = await rt.applyRun(result.run_id);
    let entry = null;
    try {
      const { promptTitle } = await import("./capture_hook.mjs");
      const diff = diffFromGit(root);
      if (diff.trim()) entry = addEntry(root, { title: promptTitle(payload.prompt), check, observed: true, diff }).id;
    } catch (err) {
      log(`dispatch: library entry not added (${err.message})`);
    }
    if (entry) {
      // the new Way's semantic code (the capture Stop hook does this otherwise; a stopped prompt has no Stop)
      try {
        const { codeWays } = await import("./library.mjs");
        const { hostedSettings } = await import("./exec/hosted.mjs");
        const { url, token } = hostedSettings(env);
        await codeWays(root, { url, token, ...(fetchImpl ? { fetchImpl } : {}) });
      } catch (err) {
        log(`dispatch: ways not coded (${err.message})`);
      }
    }
    const cost = typeof result.cost_usd === "number" ? `, $${result.cost_usd.toFixed(4)}` : "";
    return { handled: true, run_id: result.run_id, unit: rung.unit, library_entry: entry, tried,
      text: `Done by ${rung.model} (StealthLab dispatched it before this session's model ran${cost}). ` +
        `The check \`${check}\` passed in an isolated worktree and the change is applied: ` +
        `${(applied.files || []).join(", ") || "no files"}.` + (entry ? ` Recorded in the library as ${entry}.` : "") };
  }
  const triedUnits = new Set(tried.map((t) => t.unit));
  return { handled: false, tried, remaining: (plan.ladder || []).filter((u) => !triedUnits.has(u)) };
}

// The note the session gets when dispatch ran but nothing passed.
export function triedNote(d) {
  if (!d || d.handled || !d.tried?.length) return "";
  return "StealthLab first ran this on " + d.tried.map((t) => `${t.unit.split("|")[0]} (${t.state})`).join(", ") +
    " in isolated worktrees; none passed the check, and nothing was applied. " +
    (d.tried.find((t) => t.tail)?.tail ? `Last check output:\n${d.tried.find((t) => t.tail).tail}\n` : "") +
    "The plan continues with the next rung below.";
}

