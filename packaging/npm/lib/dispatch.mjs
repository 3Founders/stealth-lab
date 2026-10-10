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
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { readExecConfig } from "./exec/store.mjs";
import { launchOrThrow, scrubEnv } from "./executors/common.mjs";
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
    // "strict" (default): a cross-check that cannot decide counts as NOT verified -- the task goes up the ladder;
    // "on": inconclusive is accepted as before; "off": no cross-check
    crosscheck: (env.STEALTHLAB_DISPATCH_CROSSCHECK || "strict").toLowerCase(),
    // test-first: the session's own model writes a few tests from the task before any cheap rung runs; every cheap
    // answer must pass them too. "off" turns it off.
    testFirst: (env.STEALTHLAB_DISPATCH_TESTFIRST || "on").toLowerCase() !== "off",
  };
}

// ---- test-first: the session model's own tests, from the task --------------------------------------------------

const SPEC_FILES_MAX = 6000;     // bytes per file shown to the test writer
const SPEC_FILES_N = 6;

// The small top-level files that give the test writer the interface (the task file, the module to implement).
function specContext(root) {
  const out = [];
  let names = [];
  try { names = fs.readdirSync(root).filter((f) => !f.startsWith(".") && !/^test_|_test\./.test(f)); } catch { return ""; }
  for (const f of names.sort()) {
    const p = path.join(root, f);
    try {
      const st = fs.statSync(p);
      if (!st.isFile() || st.size > SPEC_FILES_MAX || !/\.(py|md|txt|toml|cfg|json)$/i.test(f)) continue;
      out.push(`--- ${f} ---\n${fs.readFileSync(p, "utf8")}`);
    } catch { /* unreadable: skip */ }
    if (out.length >= SPEC_FILES_N) break;
  }
  return out.join("\n\n");
}

function codeBlock(text) {
  const m = /```(?:python)?\s*\n([\s\S]*?)```/.exec(String(text || ""));
  return (m ? m[1] : String(text || "")).trim();
}

// -> {file, cost_usd} (a pytest file outside the repo, run from the worktree) or null. One lean, tool-less call to
// the session's model: a short system prompt, no tools, no project settings -- ~$0.02 with Opus, measured.
export function writeSpecTests({ prompt, root, mine, env = process.env, log = () => {}, runClaude }) {
  const model = String(mine || "").split("|")[0];
  if (!model.includes("claude")) return null;
  const ask = `Task:\n${String(prompt || "").trim()}\n\nProject files:\n${specContext(root)}\n\n` +
    "Write 3-6 pytest tests that check the behaviour this task asks for, edge cases included (boundaries, empty or " +
    "invalid input, exact outputs, errors it implies). Test only what the task states or clearly implies -- not your " +
    "own guesses about unspecified details. Import from the project's modules as the files above show. Reply with " +
    "only the Python test file.";
  // the prompt goes on stdin: it has many lines, and on Windows `claude` is a .cmd shim that would cut an argument at
  // the first newline -- launchOrThrow runs Claude Code's own entry point, never through a shell
  const run = runClaude || ((args, input) => {
    const launch = launchOrThrow("claude", { env });
    return spawnSync(launch.cmd, [...launch.prefix, ...args], { encoding: "utf8", timeout: 180000, windowsHide: true,
      input, env: scrubEnv(env) });
  });
  const r = run(["-p", "--model", model, "--system-prompt", "You write precise pytest test files. Output only code.",
    "--tools", "", "--setting-sources", "user", "--strict-mcp-config", "--output-format", "json"], ask);
  let j;
  try { j = JSON.parse(String(r.stdout || "").trim().split(/\r?\n/).pop()); } catch { log("test-first: no JSON reply"); return null; }
  if (j.is_error || !j.result) { log(`test-first: ${String(j.result || "error").slice(0, 120)}`); return null; }
  const code = codeBlock(j.result);
  if (!/def test_/.test(code)) return null;
  const dir = path.join(env.STEALTHLAB_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".stealthlab"),
    "specs", `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "test_spec.py");
  // run from the worktree: the project's modules import from the current directory
  fs.writeFileSync(file, "import os, sys\nsys.path.insert(0, os.getcwd())\n\n" + code + "\n");
  const cost = typeof j.total_cost_usd === "number" ? j.total_cost_usd : null;
  try {
    fs.appendFileSync(path.join(path.dirname(path.dirname(dir)), "dispatch_costs.jsonl"),
      JSON.stringify({ at: new Date().toISOString(), what: "test-first", model, cost_usd: cost }) + "\n");
  } catch { /* the ledger is best-effort */ }
  return { file, cost_usd: cost };
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
  // test-first: the session model's tests join the project's check for every cheap rung
  let spec = null;
  if (policy.testFirst && /pytest/.test(check)) {
    try { spec = writeSpecTests({ prompt: payload.prompt, root: payload.cwd || root, mine, env, log }); } catch (err) {
      log(`test-first: ${err.message}`);
    }
  }
  const fullCheck = spec ? `${check} && python -m pytest -q -p no:cacheprovider "${spec.file}"` : check;
  for (const rung of rungs) {
    let result;
    try {
      const started = await rt.achieve({ repo_path: payload.cwd || root, task: taskText(payload.prompt), checks: [fullCheck],
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
    let ok = result.state === "verified";
    if (result.usage_limited) {            // the executor's account hit its limit: no verdict on the model
      tried.push({ unit: rung.unit, state: "usage_limited", run_id: result.run_id });
      continue;
    }
    let cross = null;
    if (ok && policy.crosscheck !== "off") {
      cross = await crossCheck({ rt, rung, result, rungs: dispatchableRungs(plan, mine, env), env, payload, root, check,
                                 scope: policy.scope, log });
      if (cross.verdict === "disagree") ok = false;
      if (cross.verdict === "inconclusive" && policy.crosscheck === "strict") {
        // nothing could confirm the answer: not delivered, and not counted against the model either
        tried.push({ unit: rung.unit, state: "unconfirmed", run_id: result.run_id, cost_usd: result.cost_usd, cross });
        try { await rt.cancelRun(result.run_id); } catch { /* best effort */ }
        continue;
      }
    }
    try {
      if (plan.goal_id) recordObs(root, ensureRoute(root, plan.goal_id), rung.model, rung.executor, ok);
    } catch { /* the record is best-effort */ }
    tried.push({ unit: rung.unit, state: cross?.verdict === "disagree" ? "rejected by cross-check" : result.state,
                 run_id: result.run_id, cost_usd: result.cost_usd, ...(cross ? { cross } : {}),
                 tail: ok ? "" : (cross?.verdict === "disagree" ? cross.tail
                   : String((result.checks || []).map((c) => c.tail || "").join("\n")).slice(-600)) });
    if (!ok) {
      if (cross?.verdict === "disagree") try { await rt.cancelRun(result.run_id); } catch { /* best effort */ }
      continue;
    }
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
    return { handled: true, run_id: result.run_id, unit: rung.unit, library_entry: entry, tried, spec,
      text: `Done by ${rung.model} (StealthLab dispatched it before this session's model ran${cost}). ` +
        `The check \`${check}\` passed in an isolated worktree and the change is applied: ` +
        `${(applied.files || []).join(", ") || "no files"}.` + (entry ? ` Recorded in the library as ${entry}.` : "") };
  }
  const triedUnits = new Set(tried.map((t) => t.unit));
  return { handled: false, tried, spec, remaining: (plan.ladder || []).filter((u) => !triedUnits.has(u)) };
}

// Cross-check (differential testing): a passing cheap answer is accepted only if a DIFFERENT model, solving the same
// task on its own, writes edge-case tests that its own solution passes -- and the first answer passes them too.
// Visible tests cover part of a task; two models that read it differently (n vs frac, a dropped sign, an error the
// task implies) disagree on the cases those tests leave out, and the task goes up the ladder instead of shipping a
// wrong answer. No other model, no usable tests, or a verifier that fails the visible check: "inconclusive", and
// the answer is accepted as before. pytest projects only (the check names pytest).
export const EXTRA_TESTS = "tests_extra/test_extra.py";

function pytestRun(cwd, args, timeoutMs = 300000) {
  const r = spawnSync("python", ["-m", "pytest", "-q", "-p", "no:cacheprovider", ...args],
    { cwd, encoding: "utf8", timeout: timeoutMs, windowsHide: true });
  return { status: r.status, out: `${r.stdout || ""}${r.stderr || ""}` };
}

export async function crossCheck({ rt, rung, result, rungs, env, payload, root, check, scope, log = () => {} }) {
  if (!/pytest/.test(check)) return { verdict: "inconclusive", reason: "not a pytest check" };
  const verifier = rungs.find((r) => r.model !== rung.model && r.executor !== "claude")
    || (() => {
      try {
        const cfg = readExecConfig(env);
        for (const [ex, spec] of Object.entries(cfg.executors || {})) {
          if (ex === "claude") continue;
          const m = (spec?.models || []).map(String).find((x) => x !== rung.model);
          if (m) return { unit: `${m}|${ex}`, executor: ex, model: m };
        }
      } catch { /* no config */ }
      return null;
    })();
  if (!verifier) return { verdict: "inconclusive", reason: "no other model to cross-check with" };
  const aDir = result.diff?.worktree;
  if (!aDir || !fs.existsSync(aDir)) return { verdict: "inconclusive", reason: "the first run's worktree is gone" };
  let v;
  try {
    const started = await rt.achieve({ repo_path: payload.cwd || root, scope, executor: verifier.executor,
      model: verifier.model, base: "working-tree", checks: [check],
      task: taskText(`${payload.prompt}\n\nAlso write 3-6 extra pytest tests in ${EXTRA_TESTS} for behaviour this task ` +
        "describes or implies, especially edge cases the existing check may miss: boundary values, negative, empty " +
        "or invalid input, exact expected outputs, errors that should be raised. Keep the existing tests as they are.") });
    do { v = await rt.runResult(started.run_id, 55); } while (!TERMINAL.has(v?.state));
  } catch (err) {
    log(`cross-check: ${err.message}`);
    return { verdict: "inconclusive", reason: `verifier error: ${String(err.message).slice(0, 120)}` };
  }
  const done = async (out) => { try { await rt.cancelRun(v.run_id); } catch { /* best effort */ } return { verifier: verifier.unit, cost_usd: v.cost_usd ?? null, ...out }; };
  const bDir = v.diff?.worktree;
  if (v.state !== "verified" || !bDir || !fs.existsSync(path.join(bDir, EXTRA_TESTS))) {
    return done({ verdict: "inconclusive", reason: v.state !== "verified" ? "the verifier failed the check" : "no extra tests written" });
  }
  // the verifier's tests that its OWN solution passes (a test it fails itself is the test's fault)
  const own = pytestRun(bDir, ["-rA", EXTRA_TESTS]);
  const passed = [...own.out.matchAll(/^PASSED (\S+::\S+)/gm)].map((m) => m[1]);
  if (!passed.length) return done({ verdict: "inconclusive", reason: "none of the verifier's tests pass its own solution" });
  fs.mkdirSync(path.join(aDir, path.dirname(EXTRA_TESTS)), { recursive: true });
  fs.copyFileSync(path.join(bDir, EXTRA_TESTS), path.join(aDir, EXTRA_TESTS));
  const theirs = pytestRun(aDir, passed);
  try { fs.rmSync(path.join(aDir, path.dirname(EXTRA_TESTS)), { recursive: true, force: true }); } catch { /* best effort */ }
  if (theirs.status === 0) return done({ verdict: "agree", tests: passed.length });
  return done({ verdict: "disagree", tests: passed.length, tail: theirs.out.slice(-600) });
}

// The note the session gets when dispatch ran but nothing passed.
export function triedNote(d) {
  if (!d || d.handled || !d.tried?.length) return "";
  return "StealthLab first ran this on " + d.tried.map((t) => `${t.unit.split("|")[0]} (${t.state})`).join(", ") +
    " in isolated worktrees; none passed the check, and nothing was applied. " +
    (d.tried.find((t) => t.tail)?.tail ? `Last check output:\n${d.tried.find((t) => t.tail).tail}\n` : "") +
    "The plan continues with the next rung below.";
}

