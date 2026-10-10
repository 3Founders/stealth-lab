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
    // "on": the cheap rungs run at once, the first answer that passes wins and the rest are cancelled (faster, same
    // cost); default: one after another
    parallel: (env.STEALTHLAB_DISPATCH_PARALLEL || "off").toLowerCase() === "on",
    // Claude models run by the claude executor (headless Claude Code): off by default -- on a small task their cost is
    // Claude Code's own overhead, about what the session model costs to do the task, so a failed try only adds cost
    claude: (env.STEALTHLAB_DISPATCH_CLAUDE || "off").toLowerCase() === "on",
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
      if (!st.isFile() || st.size > SPEC_FILES_MAX || !/\.(py|md|txt|toml|cfg|json|ya?ml|js|mjs|cjs|ts|tsx|jsx|sql|html|css)$/i.test(f)) continue;
      out.push(`--- ${f} ---\n${fs.readFileSync(p, "utf8")}`);
    } catch { /* unreadable: skip */ }
    if (out.length >= SPEC_FILES_N) break;
  }
  return out.join("\n\n");
}

export function codeBlock(text) {
  const m = /```(?:python|py)?[ \t]*\r?\n([\s\S]*?)```/.exec(String(text || ""));
  if (m) return m[1].trim();
  // no complete fenced block (an opening or closing fence missing): drop any fence lines left over
  return String(text || "").split(/\r?\n/).filter((l) => !/^\s*```/.test(l)).join("\n").trim();
}

// A test file that does not parse would fail every answer: such a file is dropped (null when it parses).
function pythonSyntaxError(file) {
  const r = spawnSync("python", ["-c", "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())", file],
    { encoding: "utf8", timeout: 60000, windowsHide: true });
  return r.status === 0 ? null : String(r.stderr || "syntax error").trim().split(/\r?\n/).pop();
}

// The spec runs from outside the project, with the project root as the working directory: a path anchored on the
// test file's own location (the usual pytest habit) would point into the specs folder and fail every answer.
const FILE_ANCHORS = [
  /(?:pathlib\.)?Path\(\s*__file__\s*\)(?:\.resolve\(\)|\.absolute\(\))?\.parent/g,
  /os\.path\.dirname\(\s*os\.path\.(?:abspath|realpath)\(\s*__file__\s*\)\s*\)/g,
  /os\.path\.dirname\(\s*__file__\s*\)/g,
];
export function rootRelative(code) {
  let out = String(code || "");
  out = out.replace(FILE_ANCHORS[0], "__import__('pathlib').Path(__import__('os').getcwd())");
  for (const re of FILE_ANCHORS.slice(1)) out = out.replace(re, "__import__('os').getcwd()");
  return out;
}

// The test writer's brief. One line: it is passed as an argument.
export const SPEC_SYSTEM = [
  "You write the acceptance tests that decide whether a cheaper model's change does a task correctly. Its change is",
  "accepted only if your tests and the project's own check pass, so a weak test ships a bug and a wrong test throws",
  "away a correct answer. Write 4-6 short, focused pytest tests, without comments or docstrings. (1) Test what the task is FOR, not only the example it",
  "shows: an example's values often hide a wrong method, so cover the cases where a plausible but wrong",
  "implementation would differ -- both sides of a sign, threshold, boundary or branch; empty, single and repeated",
  "elements; odd and even sizes; inputs other than the one shown. (2) Where the task could be read two ways (a count",
  "or a fraction, inclusive or exclusive, in place or a copy, which order), include an input on which the readings",
  "give different answers and assert the one the task means. (3) Assert exact values where the task determines them.",
  "Where the output depends on details the task leaves open (random draws under a seed, tie order, rounding),",
  "compute the expected value in the test with a short reference implementation that takes the most direct reading",
  "of the task and uses the names, imports and libraries the project files already give. (4) Do not test what the",
  "task does not ask for: wording of messages, formatting, speed, internal structure, extra features. (5) Use the",
  "project's interface exactly as its files show it (module names, function signatures, variables a snippet must",
  "set). The test file is not inside the project: it runs with the project root as the current directory, so reach",
  "project files through os.getcwd() or plain relative paths, never through __file__. (6) The tests are pytest",
  "files whatever the project's language: for a project that is not Python, drive it through its own runtime from",
  "the test the way its check does (subprocess: node for JavaScript, the database for SQL, a headless browser for a",
  "web page when the project uses one), and assert on what it outputs. Output only code.",
].join(" ");

// test-first's tests are run with pytest, whatever the project's own check is: is it here?
let pytestHere = null;
export function pytestAvailable() {
  if (pytestHere === null) {
    const r = spawnSync("python", ["-m", "pytest", "--version"], { encoding: "utf8", timeout: 60000, windowsHide: true });
    pytestHere = r.status === 0;
  }
  return pytestHere;
}

// -> {file, cost_usd} (a pytest file outside the repo, run from the worktree) or null. One lean, tool-less call to
// the session's model: a short system prompt, no tools, no project settings -- ~$0.02 with Opus, measured.
export function writeSpecTests({ prompt, root, mine, check = "", env = process.env, log = () => {}, runClaude }) {
  // the writer: the session's model, or STEALTHLAB_DISPATCH_TESTFIRST_MODEL (a cheaper Claude model, say)
  const model = String(env.STEALTHLAB_DISPATCH_TESTFIRST_MODEL || "").trim() || String(mine || "").split("|")[0];
  if (!model.includes("claude")) return null;
  const ask = `Task:
${String(prompt || "").trim()}

Project files:
${specContext(root)}

` +
    (check ? `The project's own check command: ${check}

` : "") +
    "Write the acceptance tests for this task. Reply with only the Python test file.";
  // the prompt goes on stdin: it has many lines, and on Windows `claude` is a .cmd shim that would cut an argument at
  // the first newline -- launchOrThrow runs Claude Code's own entry point, never through a shell
  const run = runClaude || ((args, input) => {
    const launch = launchOrThrow("claude", { env });
    return spawnSync(launch.cmd, [...launch.prefix, ...args], { encoding: "utf8", timeout: 180000, windowsHide: true,
      input, env: scrubEnv(env) });
  });
  const args = ["-p", "--model", model, "--system-prompt", SPEC_SYSTEM,
    "--tools", "", "--setting-sources", "user", "--strict-mcp-config", "--output-format", "json"];
  // one retry: a failed call (a transient error, an empty reply) would leave every cheap answer unchecked by tests
  let j = null;
  let code = "";
  for (let attempt = 1; attempt <= 2 && !code; attempt++) {
    const r = run(args, ask);
    try {
      j = JSON.parse(String(r.stdout || "").trim().split(/\r?\n/).pop());
    } catch {
      log(`test-first: no JSON reply (attempt ${attempt}, exit ${r.status ?? r.error?.code ?? "?"}: ` +
          `${String(r.stderr || r.error?.message || "").trim().slice(-200)})`);
      continue;
    }
    if (j.is_error || !j.result) { log(`test-first: ${String(j.result || "error").slice(0, 160)} (attempt ${attempt})`); continue; }
    const c = rootRelative(codeBlock(j.result));
    if (/def test_/.test(c)) code = c;
    else log(`test-first: the reply has no test (attempt ${attempt})`);
  }
  if (!code) return null;
  const dir = path.join(env.STEALTHLAB_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".stealthlab"),
    "specs", `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "test_spec.py");
  // run from the worktree: the project's modules import from the current directory
  fs.writeFileSync(file, "import os, sys\nsys.path.insert(0, os.getcwd())\n\n" + code + "\n");
  const cost = typeof j.total_cost_usd === "number" ? j.total_cost_usd : null;
  const bad = pythonSyntaxError(file);
  if (bad) log(`test-first: the tests do not parse (${bad}); dispatch goes on without them`);
  try {
    fs.appendFileSync(path.join(path.dirname(path.dirname(dir)), "dispatch_costs.jsonl"),
      JSON.stringify({ at: new Date().toISOString(), what: "test-first", model, cost_usd: cost }) + "\n");
  } catch { /* the ledger is best-effort */ }
  return bad ? null : { file, cost_usd: cost };   // the call is paid (ledger) either way
}

// The plan's rungs, in order, that a local executor can run, up to the session's own model. Claude models count
// too when an executor runs them (headless Claude Code on Sonnet, say -- the `claude` executor): nothing cheaper
// than the session's model ever runs inside the session.
export function dispatchableRungs(plan, mine, env = process.env, { claude = true } = {}) {
  const out = [];
  for (const unit of plan?.ladder || []) {
    const model = String(unit).split("|")[0];
    if (mine && model === String(mine).split("|")[0]) break;
    const local = executorFor(unit, env);
    if (!local) continue;                    // e.g. Sonnet as an in-session subagent: not dispatched, skipped
    if (local.executor === "claude" && !claude) continue;
    if (!out.some((r) => r.unit === unit)) out.push({ unit, ...local });
  }
  return out;
}

// What dispatch tries: the plan's dispatchable rungs in its order, then the other open models this machine can run
// (exec.json), up to `max`. Trying them all costs cents; one often solves what another gets wrong.
export function cheapCandidates(plan, mine, env = process.env, { max = 3, claude = false } = {}) {
  // the plan puts the session's own model first: it expects the cheap models to fail here, so none is tried
  const first = String((plan?.ladder || [])[0] || "").split("|")[0];
  if (!first || (mine && first === String(mine).split("|")[0])) return [];
  const out = dispatchableRungs(plan, mine, env, { claude });
  try {
    const cfg = readExecConfig(env);
    for (const [executor, spec] of Object.entries(cfg.executors || {})) {
      if (executor === "claude") continue;
      for (const m of (spec?.models || []).map(String)) {
        if (!out.some((r) => r.model === m)) out.push({ unit: `${m}|${executor}`, executor, model: m });
      }
    }
  } catch { /* no config: the plan's rungs only */ }
  return out.slice(0, max);
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
export async function dispatch({ payload, reply, root, mine, env = process.env, fetchImpl, log = () => {}, runtime, runClaude }) {
  const policy = dispatchPolicy(env);
  const plan = reply?.model_plan;
  if (!policy.enabled || !plan || plan.status !== "ok" || !root) return null;
  const rungs = cheapCandidates(plan, mine, env, { max: policy.maxRungs, claude: policy.claude });
  const check = dispatchCheck(policy, reply, root);
  if (!rungs.length || !check) return null;
  const rt = runtime || new (await import("./exec/runtime.mjs")).ExecRuntime({ env, fetchImpl });
  const tried = [];
  // test-first: the session model's tests join the project's check for every cheap rung
  let spec = null;
  // whatever the project's check is (pytest, vitest, a SQL or page check), the tests are a pytest file run after it
  if (policy.testFirst && (runClaude || pytestAvailable())) {
    try { spec = writeSpecTests({ prompt: payload.prompt, root: payload.cwd || root, mine, check, env, log, runClaude }); } catch (err) {
      log(`test-first: ${err.message}`);
    }
  }
  const fullCheck = spec ? `${check} && python -m pytest -q -p no:cacheprovider "${spec.file}"` : check;

  const running = new Map();               // unit -> run_id of a started run, for cancelling the rest once one passes
  // one rung: start it and wait for its result -> {rung, run_id, result} | {rung, error}
  const runRung = async (rung) => {
    let runId = null;
    try {
      const started = await rt.achieve({ repo_path: payload.cwd || root, task: taskText(payload.prompt), checks: [fullCheck],
        scope: policy.scope, executor: rung.executor, model: rung.model, base: "working-tree",
        ...(plan.instance_key ? { instance_key: plan.instance_key } : {}) });
      runId = started.run_id;
      running.set(rung.unit, runId);
      let result;
      do {
        result = await rt.runResult(runId, 55);
      } while (!TERMINAL.has(result?.state));
      return { rung, run_id: runId, result };
    } catch (err) {
      return { rung, run_id: runId, error: err };
    }
  };
  // a finished rung -> true when its answer is accepted
  const judge = async ({ rung, result, error }) => {
    if (error) {
      log(`dispatch ${rung.unit}: ${error.message}`);
      tried.push({ unit: rung.unit, state: "error", error: String(error.message).slice(0, 200) });
      return false;
    }
    let ok = result.state === "verified";
    if (result.usage_limited) {            // the executor's account hit its limit: no verdict on the model
      tried.push({ unit: rung.unit, state: "usage_limited", run_id: result.run_id });
      return false;
    }
    let cross = null;
    // The session model's own tests (test-first) are the independent judge when they exist: a cheap verifier's tests
    // are weaker and, on code that is not a standalone module, can fail a correct answer. The cross-check covers only
    // a task test-first could not write tests for.
    if (ok && policy.crosscheck !== "off" && !spec) {
      cross = await crossCheck({ rt, rung, result, rungs, env, payload, root, check, scope: policy.scope, log });
      if (cross.verdict === "disagree") ok = false;
      if (cross.verdict === "inconclusive" && policy.crosscheck === "strict") {
        // nothing could confirm the answer: not delivered, and not counted against the model either
        tried.push({ unit: rung.unit, state: "unconfirmed", run_id: result.run_id, cost_usd: result.cost_usd, cross });
        try { await rt.cancelRun(result.run_id); } catch { /* best effort */ }
        return false;
      }
    }
    try {
      if (plan.goal_id) recordObs(root, ensureRoute(root, plan.goal_id), rung.model, rung.executor, ok);
    } catch { /* the record is best-effort */ }
    tried.push({ unit: rung.unit, state: cross?.verdict === "disagree" ? "rejected by cross-check" : result.state,
                 run_id: result.run_id, cost_usd: result.cost_usd, ...(cross ? { cross } : {}),
                 tail: ok ? "" : (cross?.verdict === "disagree" ? cross.tail
                   : String((result.checks || []).map((c) => c.tail || "").join("\n")).slice(-600)) });
    if (!ok && cross?.verdict === "disagree") try { await rt.cancelRun(result.run_id); } catch { /* best effort */ }
    return ok;
  };

  const queue = [...rungs];
  const pending = new Map();               // unit -> promise of its finished run
  const width = policy.parallel ? rungs.length : 1;
  let winner = null;
  while (!winner && (queue.length || pending.size)) {
    while (queue.length && pending.size < width) {
      const rung = queue.shift();
      pending.set(rung.unit, runRung(rung));
    }
    const done = await Promise.race([...pending.values()]);
    pending.delete(done.rung.unit);
    running.delete(done.rung.unit);
    if (await judge(done)) winner = done;
  }
  if (winner) {
    // the others still running: cancelled (not counted against their models), then awaited so their cost is known
    for (const unit of pending.keys()) {
      const runId = running.get(unit);
      if (runId) try { await rt.cancelRun(runId); } catch { /* best effort */ }
    }
    for (const p of pending.values()) {
      const r = await p;
      tried.push({ unit: r.rung.unit, state: "cancelled", run_id: r.run_id, cost_usd: r.result?.cost_usd ?? null });
    }
    const { rung, result } = winner;
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
  return "StealthLab first tried this on " + d.tried.map((t) => `${t.unit.split("|")[0]} (${t.state})`).join(", ") +
    "; none passed the check and nothing was applied. Do the task yourself; there is nothing to report.";
}

