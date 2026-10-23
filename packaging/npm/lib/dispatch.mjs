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
// It needs a CHECK -- an unverified result never goes in: STEALTHLAB_DISPATCH_CHECK (the project's test command), the
// check of the library entry the lookup matched, or the test command the project itself defines (lib/detect_check.mjs).
// No check, no dispatch. STEALTHLAB_DISPATCH=off turns it off.
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { readExecConfig } from "./exec/store.mjs";
import { claudeChildEnv, launchOrThrow, scrubEnv } from "./executors/common.mjs";
import { runClaudeAsync } from "./claude_call.mjs";
import { answerEvidence, crossTest, decide, evidenceLine, parseRule, ruleUses } from "./accept.mjs";
import { contextPack, contextPackEnabled } from "./context_pack.mjs";
import { locate, locateEnabled, renderForWriter, renderWhere, testFileFor } from "./locate.mjs";
import { readArea, recordArea } from "./areas.mjs";
import { writeDagRun, writeDispatchRun } from "./dispatch_run.mjs";
import { runDag, sessionStartedAt } from "./dag_run.mjs";
import { wayStepsForEntry } from "./way_extract.mjs";
import { describeEntry } from "./way_describe.mjs";
import { reproAgentEnabled, reproduceTests } from "./repro_agent.mjs";
import { addEntry, diffFromGit, ensureRoute, readEntry, recordObs } from "./library.mjs";
import { executorFor } from "./model_guard.mjs";
import { autoTidy } from "./library_tidy.mjs";

// The check that accepted an answer, and the strong reviewer's "fixes" when it gave one, become evidence on the library entry the
// answer created (.stealth/evidence.md; queued for upload only when STEALTHLAB_EVIDENCE_UPLOAD=on). Bookkeeping: it never throws and
// never waits on the network (lib/evidence_record.mjs).
async function recordEvidenceFor({ root, entry, check, winner, env, log }) {
  try {
    const { recordAcceptedEvidence } = await import("./evidence_record.mjs");
    await recordAcceptedEvidence({ root, entryId: entry, check, reviewerFixes: Boolean(winner?.evidence?.Rs),
      reviewerModel: winner?.evidence?.Rs_model || null, executorModel: winner?.rung?.model || null, env, log });
  } catch { /* evidence is bookkeeping: never a reason to fail an answer */ }
}
import { profileKey } from "./executors/profile.mjs";
import { capFloorS, capMult, fastestFirstEnabled, modelCapEnabled, modelCapS, modelSpeeds, modelWins, orderFastestFirst, recordModelWin } from "./model_speed.mjs";
import { detectCheck, pythonBin } from "./detect_check.mjs";
import { repoContext } from "./task_context.mjs";
import { repoTopLevel } from "./exec/worktree.mjs";
import { createUndoPoint, discardUndoPoint, finishUndoPoint, undoEnabled } from "./undo.mjs";
import { cheapWritten } from "./continuity.mjs";

const TERMINAL = new Set(["verified", "failed", "timed_out", "cancelled"]);
// the executor's task text: the issue whole plus the pointers; at 2,000 it cut 8 of 25 real issues (survey 2026-10-16)
const TASK_MAX = 12000;

export function dispatchPolicy(env = process.env) {
  return {
    enabled: (env.STEALTHLAB_DISPATCH || "on").toLowerCase() !== "off",
    check: (env.STEALTHLAB_DISPATCH_CHECK || "").trim() || null,
    // find the project's own test command (lib/detect_check.mjs) when none is configured and no library entry has one;
    // "off" restores "no configured check, no dispatch"
    detect: (env.STEALTHLAB_DISPATCH_DETECT || "on").toLowerCase() !== "off",
    // how many configured cheap models are tried: all of them unless set (each is a chance at the task; there is no
    // measured reason to try fewer)
    maxRungs: Number(env.STEALTHLAB_DISPATCH_MAX_RUNGS) > 0 ? Number(env.STEALTHLAB_DISPATCH_MAX_RUNGS) : Infinity,
    scope: String(env.STEALTHLAB_DISPATCH_SCOPE || "*,**/*").split(",").map((s) => s.trim()).filter(Boolean),
    // "strict" (default): a cross-check that cannot decide counts as NOT verified -- the task goes up the ladder;
    // "on": inconclusive is accepted as before; "off": no cross-check
    crosscheck: (env.STEALTHLAB_DISPATCH_CROSSCHECK || "strict").toLowerCase(),
    // how a passing answer is accepted (lib/accept.mjs). Default: E (no new failure in the project's own tests) and Rs
    // (the strong reviewer says the diff fixes the request; "does not fix" or unsure is not an accept). Nothing else
    // gates by default: a reproduction test written before any answer guesses the behaviour wrong about half the time
    // (pipeline survey, 2026-10-16), and as a gate it rejected 4 of 4 right answers (ct-series-v5, jsonargparse) -- so
    // no test is written for this rule. "check": the check alone, as before 2026-10-17.
    // agreeWaitS: after a first passing answer, how long the others may still finish -- only for a rule that uses A or C
    accept: (env.STEALTHLAB_DISPATCH_ACCEPT || "E Rs").trim(),
    agreeWaitS: Math.max(0, Number(env.STEALTHLAB_DISPATCH_AGREE_WAIT_S || 90)),
    // test-first: the session's own model writes a few tests from the task before any cheap rung runs; every cheap
    // answer must pass them too. "off" turns it off.
    testFirst: (env.STEALTHLAB_DISPATCH_TESTFIRST || "on").toLowerCase() !== "off",
    // the cheap models run at once, the first accepted answer wins and the rest are cancelled: the user waits for the
    // fastest good answer, not for each model in turn. "off": one after another
    parallel: (env.STEALTHLAB_DISPATCH_PARALLEL || "on").toLowerCase() !== "off",
    // Claude models run by the claude executor (headless Claude Code): off by default -- on a small task their cost is
    // Claude Code's own overhead, about what the session model costs to do the task, so a failed try only adds cost
    claude: (env.STEALTHLAB_DISPATCH_CLAUDE || "off").toLowerCase() === "on",
    // the time the cheap tries get, together, from their start: past it the session's model takes over. 600 s: 12 of
    // the 14 cheap solves of real repository issues finished within it (pipeline survey R1, 2026-10-16: solve times
    // 46-594 s, then 1053 and 1201). One run gets the same limit (a run cannot outlast the tries it is part of).
    timeoutS: Math.max(10, Number(env.STEALTHLAB_DISPATCH_TIMEOUT_S || 600)),
    // at most this many cheap runs at once on one provider: unset, no limit (a provider's rate limit answers 429, which
    // the bridge retries)
    maxParallel: Number(env.STEALTHLAB_DISPATCH_MAX_PARALLEL) > 0 ? Number(env.STEALTHLAB_DISPATCH_MAX_PARALLEL) : Infinity,
    // when the plan puts the session's model first, still try the cheap models this often (a skipped kind recovers)
    explore: Math.min(1, Math.max(0, Number(env.STEALTHLAB_DISPATCH_EXPLORE ?? 0.2))),
    // a provider with fewer tokens left today than this is not tried: a big task's cheap tries take ~100k-500k
    minQuotaTokens: Math.max(0, Number(env.STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS ?? 300000)),
    // "on": a verified cheap answer is NOT applied: the prompt is stopped with a summary of the change and the command that
    // applies it (`stealthlab-mcp exec apply <run>`). Default off: it is applied, with an undo point (lib/undo.mjs)
    ask: (env.STEALTHLAB_DISPATCH_ASK || "off").toLowerCase() === "on",
    // per-MODEL timing learned from this repo's own cheap wins (lib/model_speed.mjs): a run is capped at 3 x its model's p90 win time (not
    // below 60 s, not above the limit above; fewer than 5 wins: no cap), a capped run hands in its change to be judged; and the rungs start
    // fastest model first. The two switches are independent.
    modelCap: modelCapEnabled(env),
    fastestFirst: fastestFirstEnabled(env),
    capMult: capMult(env),
    capFloorS: capFloorS(env),
    // two cheap answers that fail the same advance tests end the cheap tries at once (STEALTHLAB_DISPATCH_SHARED_FAIL_STOP=off)
    sharedFailStop: (env.STEALTHLAB_DISPATCH_SHARED_FAIL_STOP || "on").toLowerCase() !== "off",
    // the test writer flags a task that reads two ways: no cheap answer is delivered for it (STEALTHLAB_DISPATCH_AMBIGUITY=off)
    ambiguityStop: (env.STEALTHLAB_DISPATCH_AMBIGUITY || "on").toLowerCase() !== "off",
    // an image task's passing page is compared with the screenshot by a model that can see (lib/visual_check.mjs)
    visualCheck: (env.STEALTHLAB_DISPATCH_VISUAL_CHECK || "on").toLowerCase() !== "off",
    // extra seconds for the cheap tries when a strong cheap model (exec.json profile strong: true) is among them: none
    // unless set
    strongExtraS: Math.max(0, Number(env.STEALTHLAB_DISPATCH_STRONG_EXTRA_S ?? 0)),
    // a middle model (an exec.json profile with tier: 2, say GLM-5.3) starts only once every cheaper one has finished
    // without an accepted answer, with this much time of its own
    tierS: Math.max(60, Number(env.STEALTHLAB_DISPATCH_TIER_S ?? 420)),
    // one cheap run's own time limit in the executor (lib/exec/runtime.mjs, at most 3600 s): unset, the tries' limit
    runTimeoutS: Number(env.STEALTHLAB_DISPATCH_RUN_TIMEOUT_S) > 0 ? Number(env.STEALTHLAB_DISPATCH_RUN_TIMEOUT_S) : null,
    strongFirst: (env.STEALTHLAB_DISPATCH_STRONG_FIRST || "off").toLowerCase() === "on",
    // a repo area (lib/areas.mjs) where the cheap models lost this many times and never won is not dispatched to: off
    // unless set -- the model plan already learns from this repo's outcomes (routing.md OBS), with no fixed threshold
    areaSkip: (env.STEALTHLAB_DISPATCH_AREA_SKIP || "off").toLowerCase() === "on",
    areaSkipAfter: Math.max(1, Number(env.STEALTHLAB_DISPATCH_AREA_SKIP_AFTER || 3)),
    // a follow-up about code this session's own model is already changing is not dispatched (STEALTHLAB_DISPATCH_FOLLOWUPS=on
    // dispatches it anyway): the cheap models would redo the session's work from a one-line follow-up
    followups: (env.STEALTHLAB_DISPATCH_FOLLOWUPS || "off").toLowerCase() === "on",
    // the located module's existing test file joins the check when it passes on the unchanged code (a cheap answer
    // must not break what already works there)
    regression: (env.STEALTHLAB_DISPATCH_REGRESSION || "on").toLowerCase() !== "off",
    // tests written for the task that all pass on the unchanged code do not test the change: asked again, then dropped
    specBaseline: (env.STEALTHLAB_DISPATCH_SPEC_BASELINE || "on").toLowerCase() !== "off",
    // the project's pytest check: tests that already fail on the unchanged code do not fail an answer
    baselineCheck: (env.STEALTHLAB_DISPATCH_BASELINE_CHECK || "on").toLowerCase() !== "off",
    // a task in parts (lib/dag_run.mjs): planned from the closest fix already made here; the accepted parts of an
    // unfinished plan are applied for the session's model to continue from (STEALTHLAB_DISPATCH_DAG_PARTIAL=off: none).
    // Off unless "on": a task is tried whole. Split by file from a past fix, every part of both plans it made failed
    // (sqlglot-4051, Modal 2026-10-17: 4 and 5 parts, part 1 failed on every model), and a part's check is weaker
    // than the whole task's.
    dag: (env.STEALTHLAB_DISPATCH_DAG || "off").toLowerCase() === "on",
    dagPartialApply: (env.STEALTHLAB_DISPATCH_DAG_PARTIAL || "on").toLowerCase() !== "off",
    // no test command in the project: the tests written for the task are the check (needs the test writer). Off unless
    // "on": with no project tests nothing but a guessed test would judge an answer (tests written before any answer
    // guess the behaviour wrong about half the time, pipeline survey 2026-10-16), so the session's model does it.
    specOnly: (env.STEALTHLAB_DISPATCH_SPEC_ONLY || "off").toLowerCase() === "on",
    // "on": a cheap model can read images, so a task built on a screenshot may be dispatched (default off: text-only models)
    vision: (env.STEALTHLAB_DISPATCH_VISION || "off").toLowerCase() === "on",
    // the task brief (lib/brief.mjs): a cheap call restates the prompt with the earlier turns, and only a clear, small or
    // medium code task is dispatched; the cheap models and the test writer get the brief, then the user's original request. "off": the raw prompt, as before
    brief: (env.STEALTHLAB_BRIEF || "on").toLowerCase() !== "off",
    // only a request the server's judge (JEV first, POST /triage, asked by the hook) says asks for a change now goes
    // to the cheap models; "off": no gate (a mechanism test, or a client with no server judge)
    gate: (env.STEALTHLAB_DISPATCH_GATE || "on").toLowerCase() !== "off",
    // after the time is up, how long the handed-in changes may take to be checked and judged: a hang guard over the
    // runtime's check limit (600 s) and the reviewer's (120 s), never reached by a check that ends
    handInS: Math.max(1, Number(env.STEALTHLAB_DISPATCH_HANDIN_S || 780)),
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
  "set); test the behaviour through what exists now -- import only names the files shown define or the task itself names,",
  "never a helper you expect the fix to add. The test file is not inside the project: it runs with the project root as the current directory, so reach",
  "project files through os.getcwd() or plain relative paths, never through __file__. (6) The tests are pytest",
  "files whatever the project's language: for a project that is not Python, drive it through its own runtime from",
  "the test the way its check does (subprocess: node for JavaScript, the database for SQL, a headless browser for a",
  "web page when the project uses one), and assert on what it outputs. (7) When the project's own check already",
  "verifies a contract or schema exhaustively (property-based fuzzing, a schema validator, a type checker), do not",
  "re-test what it covers: test only behaviour the task asks for beyond it, and never assume what the contract",
  "leaves open (which route serves an ambiguous path, field values the schema allows freely). (8) If the task, read",
  "with its files, can reasonably be read two ways that give DIFFERENT correct results, and nothing in the task or the",
  "files settles which, make the first line of the file `# AMBIGUOUS: <the two readings, one short sentence>` and still",
  "write the tests for the likelier reading. Do not flag details the tests can leave open, or readings only a careless",
  "reader would take; most tasks are not ambiguous. (9) The tests must fail on the code as it is now and pass once the",
  "task is done: for a bug, reproduce it (the input from the report, the result it should give); import the code from",
  "the module paths the files shown use. Output only code.",
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

// Where test-first's files go: <STEALTHLAB_HOME>/specs/<id>/ -- test_spec.py when written, SKIP when none could be.
export function specDir(env = process.env) {
  const dir = path.join(env.STEALTHLAB_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".stealthlab"),
    "specs", `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// The check step that runs test-first's tests: waits for the tests (they are written while the cheap model works)
// or for SKIP, then runs them with pytest from the worktree. No tests could be written: it passes, and dispatch
// falls back on the cross-check for that answer.
const RUN_SPEC = `import os, subprocess, sys, time
d = os.path.dirname(os.path.abspath(__file__))
f, skip = os.path.join(d, "test_spec.py"), os.path.join(d, "SKIP")
end = time.time() + 300
while not os.path.exists(f) and not os.path.exists(skip) and time.time() < end:
    time.sleep(0.5)
if not os.path.exists(f):
    sys.exit(0)
sys.exit(run_inside(f, os.getcwd()))
`;

// The tests written for a task run from INSIDE the project -- copied into its own test folder (else its root), run there,
// removed -- so the project's pytest config, plugins and conftest fixtures apply. Run from the specs folder they were
// collected without them, and 22 of 27 sets of tests failed even with the real fix applied, many of them on that alone
// ("invalid fixture loop scope", errors during collection -- pipeline survey, 2026-10-16). Coverage is off for these runs
// (--no-cov, when pytest-cov is installed): a project's fail-under applies to its whole suite, and on one file of tests it
// turns every pass into a failure.
const RUN_INSIDE = `def run_inside(spec, cwd, name="test_zz_stealthlab_spec.py"):
    import os, shutil, subprocess, sys
    d = next((os.path.join(cwd, t) for t in ("tests", "test", "testing") if os.path.isdir(os.path.join(cwd, t))), cwd)
    dest = os.path.join(d, name)
    shutil.copyfile(spec, dest)
    try:
        return subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *(["--no-cov"] if __import__("importlib.util").util.find_spec("pytest_cov") else []), dest], cwd=cwd)
    finally:
        try:
            os.remove(dest)
        except OSError:
            pass
`;

// strict: the tests written for the task are the ONLY check (the project has no test command), so no tests = fail
export function writeSpecRunner(dir, { strict = false } = {}) {
  const file = path.join(dir, "run_spec.py");
  const body = `${RUN_INSIDE}\n${RUN_SPEC}`;
  fs.writeFileSync(file, strict ? body.replace("if not os.path.exists(f):\n    sys.exit(0)", "if not os.path.exists(f):\n    sys.exit(1)") : body);
  return file;
}

// The located module's existing tests as part of the check (lib/locate.mjs testFile): waits for dispatch's run of them on
// the unchanged code -- REGRESS_OK (they pass: the answer must keep them passing) or REGRESS_SKIP (they fail or could
// not run there already: they say nothing about the answer) -- then runs them from the worktree.
const RUN_REGRESS = `import os, subprocess, sys, time
d = os.path.dirname(os.path.abspath(__file__))
ok, skip = os.path.join(d, "REGRESS_OK"), os.path.join(d, "REGRESS_SKIP")
end = time.time() + 300
while not os.path.exists(ok) and not os.path.exists(skip) and time.time() < end:
    time.sleep(0.5)
if not os.path.exists(ok):
    sys.exit(0)
t = open(ok, encoding="utf-8").read().strip()
sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *(["--no-cov"] if __import__("importlib.util").util.find_spec("pytest_cov") else []), t]))
`;

// The project's check, judged against the unchanged code: run it; on failure, wait for dispatch's run of it on the
// unchanged code (BASE_FAILS: the tests that failed there; BASE_NONE: none failed, or that run said nothing usable)
// and pass when every failing test also failed there. Anything else -- a new failure, an error before the tests ran
// (exit 2+), no failing test names in the output -- keeps the check's own exit code.
const RUN_BASELINE_CHECK = `import os, re, subprocess, sys, time
d = os.path.dirname(os.path.abspath(__file__))
cmd = open(os.path.join(d, "CHECK"), encoding="utf-8").read()
p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
sys.stdout.write(p.stdout[-20000:])
if p.returncode != 1:
    sys.exit(p.returncode)
fails = set(re.findall(r"^(?:FAILED|ERROR|SUBFAIL(?:ED)?\\[.*?\\]) (\\S+)", p.stdout, re.M))
bf, bn = os.path.join(d, "BASE_FAILS"), os.path.join(d, "BASE_NONE")
end = time.time() + 300
while not os.path.exists(bf) and not os.path.exists(bn) and time.time() < end:
    time.sleep(0.5)
if fails and os.path.exists(bf):
    base = set(open(bf, encoding="utf-8").read().split())
    new = sorted(fails - base)
    if not new:
        print("[stealthlab] %d failing test(s), all failing on the unchanged code too: not caused by this change" % len(fails))
        sys.exit(0)
    print("[stealthlab] new failures: " + ", ".join(new[:10]))
sys.exit(1)
`;

export function writeBaselineCheckRunner(dir, check) {
  fs.writeFileSync(path.join(dir, "CHECK"), check);
  const file = path.join(dir, "run_check.py");
  fs.writeFileSync(file, RUN_BASELINE_CHECK);
  return file;
}

// pytest's short summary -> the failing test ids ("FAILED tests/test_x.py::test_y - msg" -> tests/test_x.py::test_y). A
// failing subtest ("SUBFAILED[case] tests/test_x.py::T::test_y") fails its test: pytest still lists the test PASSED
// (sqlglot's dialect tests are all subtests).
export function failingTests(out) {
  return [...new Set([...String(out || "").matchAll(/^(?:FAILED|ERROR|SUBFAIL(?:ED)?\[.*?\]) (\S+)/gm)].map((m) => m[1]))];
}

// a shell command -> {code, out} (code null: could not start, timed out or aborted); never blocks the event loop
export function runShellCapture(cmd, { cwd, timeoutMs = 300000, signal = null } = {}) {
  return new Promise((resolve) => {
    let child;
    try { child = spawn(cmd, { cwd, shell: true, windowsHide: true }); } catch { resolve({ code: null, out: "" }); return; }
    let out = "", done = false;
    const finish = (code) => { if (!done) { done = true; clearTimeout(timer); signal?.removeEventListener?.("abort", abort); resolve({ code, out }); } };
    const abort = () => { try { child.kill(); } catch { /* */ } finish(null); };
    const timer = setTimeout(abort, timeoutMs);
    signal?.addEventListener?.("abort", abort);
    child.stdout.on("data", (b) => { out = (out + b).slice(-200000); });
    child.stderr.on("data", (b) => { out = (out + b).slice(-200000); });
    child.on("error", () => finish(null));
    child.on("close", (code) => finish(code));
  });
}

export function writeRegressRunner(dir) {
  const file = path.join(dir, "run_regress.py");
  fs.writeFileSync(file, RUN_REGRESS);
  return file;
}

// a command without a shell -> its exit code, or null (could not start, timed out, aborted); never blocks the event loop
export function runAsync(cmd, args, { cwd, timeoutMs = 120000, signal = null } = {}) {
  return new Promise((resolve) => {
    let child;
    try { child = spawn(cmd, args, { cwd, windowsHide: true, stdio: "ignore" }); } catch { resolve(null); return; }
    let done = false;
    const finish = (v) => { if (!done) { done = true; clearTimeout(timer); signal?.removeEventListener?.("abort", abort); resolve(v); } };
    const abort = () => { try { child.kill(); } catch { /* */ } finish(null); };
    const timer = setTimeout(abort, timeoutMs);
    signal?.addEventListener?.("abort", abort);
    child.on("error", () => finish(null));
    child.on("close", (code) => finish(code));
  });
}

// ["--no-cov"] when this python has pytest-cov (the flag is an error without it): our runs cover a file or two of
// tests, so a project's coverage fail-under would fail every one of them
const noCov = new Map();
export function noCovArgs(python, cwd) {
  const k = `${python}|${cwd || ""}`;
  if (!noCov.has(k)) {
    const r = spawnSync(String(python).replace(/^"|"$/g, ""), ["-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest_cov') else 1)"],
      { cwd: cwd || undefined, encoding: "utf8", timeout: 30000, windowsHide: true });
    noCov.set(k, r.status === 0 ? ["--no-cov"] : []);
  }
  return noCov.get(k);
}

// tests for the task, run on the code as it is now (cwd: the project) -> pytest's exit code (0: all pass) or null
// the test file as run: the project root first on the import path -- placed after any `from __future__` imports, which
// Python requires first in a file (above them it broke 2 of 30 writers' tests: pipeline survey, 2026-10-16)
export function withHeader(code) {
  const lines = String(code).split(/\r?\n/);
  let last = -1;
  for (let i = 0; i < lines.length; i++) if (/^from __future__ import /.test(lines[i])) last = i;
  const head = "import os, sys\nsys.path.insert(0, os.getcwd())";
  if (last < 0) return `${head}\n\n${code}\n`;
  return `${lines.slice(0, last + 1).join("\n")}\n${head}\n${lines.slice(last + 1).join("\n")}\n`;
}

// run inside the project, as the check does (RUN_INSIDE): a uniquely named copy in its test folder, removed after
// -> {code, out} or null
async function runPytestAsync(python, code, dir, cwd, signal) {
  const testDir = ["tests", "test", "testing"].map((t) => path.join(cwd, t)).find((d) => { try { return fs.statSync(d).isDirectory(); } catch { return false; } }) || cwd;
  const f = path.join(testDir, `test_zz_stealthlab_baseline_${process.pid}_${Date.now() % 100000}.py`);
  try { fs.writeFileSync(f, code); } catch { return null; }
  try {
    const r = await runShellCapture(`"${python}" -m pytest -q -p no:cacheprovider ${noCovArgs(python, cwd).join(" ")} "${f}"`, { cwd, timeoutMs: 120000, signal });
    return r.code === null ? null : r;
  } finally {
    try { fs.unlinkSync(f); } catch { /* */ }
  }
}


// -> {file, cost_usd} (a pytest file outside the repo, run from the worktree) or null. One lean, tool-less call to
// a Claude model: a short system prompt, no tools, no project settings. Async: dispatch starts the cheap models at
// the same time, and their check waits for the file (run_spec.py). `dir`: where to write (specDir() when omitted);
// the file appears only once it is complete and parses, and SKIP appears instead when no tests could be written.
export async function writeSpecTests({ prompt, root, mine, check = "", env = process.env, log = () => {}, runClaude,
                                       dir = null, signal = null, located = "", baseline = false, python = "python", agentWriter = null }) {
  const out = dir || specDir(env);
  const skip = () => { try { fs.writeFileSync(path.join(out, "SKIP"), ""); } catch { /* best effort */ } return null; };
  // the writer: the session's model, or STEALTHLAB_DISPATCH_TESTFIRST_MODEL -- a Claude model (through Claude Code), or
  // an open model with an exec.json profile (GLM, say: called directly on its OpenAI-compatible endpoint)
  const model = String(env.STEALTHLAB_DISPATCH_TESTFIRST_MODEL || "").trim() || String(mine || "").split("|")[0];
  const openProfile = model.includes("claude") ? null : openWriterProfile(env, model);
  if (!model.includes("claude") && !openProfile) return skip();
  // located: the code where the task is (lib/locate.mjs) and the module's own tests; in a large repository the
  // top-level files alone left the writer guessing the module paths and the interface
  let ask = `Task:\n${String(prompt || "").trim()}\n\n` +
    (located ? `Where the task is in this repository (found by searching it for the task's names; the code at those lines):\n${located}\n\n` : "") +
    `Project files:\n${specContext(root)}\n\n` +
    (check ? `The project's own check command: ${check}\n\n` : "") +
    "Write the acceptance tests for this task. Reply with only the Python test file.";
  // the prompt goes on stdin: it has many lines, and on Windows `claude` is a .cmd shim that would cut an argument at
  // the first newline -- launchOrThrow runs Claude Code's own entry point, never through a shell
  // an open writer gets STEALTHLAB_DISPATCH_TESTFIRST_OPEN_TIMEOUT_S (90 s); when it fails or is late, the second attempt
  // goes to STEALTHLAB_DISPATCH_TESTFIRST_FALLBACK (Sonnet; "off": the open model again). GLM-5.3 at high reasoning took
  // over 180 s twice on a task with a large schema, and a task without tests is checked far less well (2026-10-15).
  const openTimeoutS = Math.max(10, Number(env.STEALTHLAB_DISPATCH_TESTFIRST_OPEN_TIMEOUT_S || 90));
  const fbRaw = String(env.STEALTHLAB_DISPATCH_TESTFIRST_FALLBACK ?? "claude-sonnet-5-5").trim();
  const fallback = openProfile && fbRaw && fbRaw.toLowerCase() !== "off" && fbRaw.includes("claude") ? fbRaw : null;
  // each writer call's own time, in the log: the hedge (Sonnet after 30 s, GLM cut at 90 s) is to be set from these
  const timed = (m, f) => async (a, input) => {
    const t0 = Date.now();
    const r = await f(a, input);
    const ok = r?.status === 0 && /def test_/.test(String(r?.stdout || ""));
    log(`test-first: ${m} replied in ${Math.round((Date.now() - t0) / 1000)} s${ok ? "" : " (no usable tests)"}`);
    return r;
  };
  const claudeRun = timed(fallback || model, runClaude || ((a, input) => runClaudeAsync(env, a, input, signal)));
  const run = openProfile && !runClaude ? timed(model, (_args, input) => runOpenWriter(openProfile, env, input, signal, openTimeoutS)) : claudeRun;
  const argsFor = (m) => ["-p", "--model", m, "--system-prompt", SPEC_SYSTEM,
    "--tools", "", "--setting-sources", "user", "--strict-mcp-config", "--output-format", "json"];
  const args = argsFor(model);
  let usedModel = model;
  // hedged: an open writer not done after STEALTHLAB_DISPATCH_TESTFIRST_HEDGE_S (30 s) gets the fallback writing beside
  // it, and the first usable tests win -- instead of waiting out its whole timeout first. On real repository issues
  // GLM-5.3 timed out on every turn (90 s each, Modal 2026-10-15) while its quick replies on small tasks are kept.
  const hedgeS = Number(env.STEALTHLAB_DISPATCH_TESTFIRST_HEDGE_S ?? 30);
  const ledger = path.join(path.dirname(path.dirname(out)), "dispatch_costs.jsonl");
  const usable = (r) => {
    try { const x = JSON.parse(String(r?.stdout || "").trim().split(/\r?\n/).pop()); return !x.is_error && /def test_/.test(String(x.result || "")); } catch { return false; }
  };
  const bookLoser = (p) => p.then(({ r, m }) => {             // the slower call is paid too: on the ledger, as unused
    try {
      const x = JSON.parse(String(r?.stdout || "").trim().split(/\r?\n/).pop());
      fs.appendFileSync(ledger, JSON.stringify({ at: new Date().toISOString(), what: "test-first-unused", model: m, cost_usd: x.total_cost_usd ?? null }) + "\n");
    } catch { /* no reply, nothing paid that we can see */ }
  });
  const hedged = async () => {
    if (!fallback || !(hedgeS > 0)) return run(args, ask);
    const p1 = Promise.resolve(run(args, ask)).then((r) => ({ r, m: model }));
    let t;
    const wait = new Promise((res) => { t = setTimeout(() => res(null), hedgeS * 1000); });   // awaited, cleared below
    const early = await Promise.race([p1, wait]);
    clearTimeout(t);
    if (early) return early.r;
    log(`test-first: ${model} not done in ${hedgeS} s; ${fallback} writes too, the first usable tests win`);
    const p2 = Promise.resolve(claudeRun(argsFor(fallback), ask)).then((r) => ({ r, m: fallback }));
    const first = await Promise.race([p1, p2]);
    const other = first.m === model ? p2 : p1;
    if (usable(first.r)) { usedModel = first.m; bookLoser(other); return first.r; }
    const second = await other;
    usedModel = second.m;
    return second.r;
  };
  // one retry: a failed call (a transient error, an empty reply) would leave every cheap answer unchecked by tests
  let j = null;
  let code = "";
  // a reproduction agent that ran its test (lib/repro_agent.mjs) first; its test already fails on the current code by
  // assertion -- it still goes through the parse check below. None: the one-call writer, as before.
  if (agentWriter && !signal?.aborted) {
    const got = await agentWriter();
    const c = typeof got === "string" ? got : got?.code;
    // its test that was not accepted, and why: the one-call writer is told, so it does not write the same test again
    if (!c && got?.tried) {
      ask += `\n\nAn agent that could run the tests tried this test first, and it was not accepted because ${got.why}. ` +
        `Do not repeat its mistake:\n\`\`\`python\n${got.tried}\n\`\`\``;
    }
    if (c && /def test_/.test(c)) {
      const probe = path.join(out, "test_spec.py.probe");
      fs.writeFileSync(probe, withHeader(c));
      const bad = pythonSyntaxError(probe);
      try { fs.unlinkSync(probe); } catch { /* */ }
      if (!bad) { code = c; usedModel = "reproduction-agent"; j = { total_cost_usd: null }; }
    }
  }
  for (let attempt = 1; attempt <= 2 && !code && !signal?.aborted; attempt++) {
    if (attempt === 2 && fallback) { usedModel = fallback; log(`test-first: ${model} gave no usable tests; ${fallback} writes them`); }
    const r = attempt === 2 && fallback ? await claudeRun(argsFor(fallback), ask) : await hedged();
    try {
      j = JSON.parse(String(r.stdout || "").trim().split(/\r?\n/).pop());
    } catch {
      log(`test-first: no JSON reply (attempt ${attempt}, exit ${r.status ?? r.error?.code ?? "?"}: ` +
          `${String(r.stderr || r.error?.message || "").trim().slice(-200)})`);
      continue;
    }
    if (j.is_error || !j.result) { log(`test-first: ${String(j.result || "error").slice(0, 160)} (attempt ${attempt})`); continue; }
    const c = rootRelative(codeBlock(j.result));
    if (!/def test_/.test(c)) { log(`test-first: the reply has no test (attempt ${attempt})`); continue; }
    // a reply that does not parse gets the second attempt too: when these tests are the only check, no tests = no win
    const probe = path.join(out, "test_spec.py.probe");
    fs.writeFileSync(probe, withHeader(c));
    const bad = pythonSyntaxError(probe);
    try { fs.unlinkSync(probe); } catch { /* */ }
    if (bad && attempt < 2) { log(`test-first: the tests do not parse (${bad}); asking again`); continue; }
    // tests that all pass on the code as it is now do not test the change: every answer, even a wrong one, would pass
    // them. Asked again with that said; still so on the second attempt -> no tests (the session's model does the task)
    if (baseline && !bad) {
      const run = await runPytestAsync(python, withHeader(c), out, root, signal);
      const base = run?.code ?? null;
      // a real failure is pytest's exit 1 with tests run; a crash at collection or setup (2-5: an import of a name that
      // does not exist, a plugin error) is not "failing on the current code" -- such tests reject every answer
      if (base !== null && base !== 0 && (base !== 1 || /errors? during collection|^ERROR /m.test(run.out))) {
        log(`test-first: the tests do not run on the unchanged code (pytest exit ${base}, attempt ${attempt})${attempt < 2 ? "; asking again" : "; dropped"}`);
        ask += "\n\nYour previous tests could not even run on the current code (an error before any test ran):\n" +
          String(run.out || "").split(/\r?\n/).filter((l) => /Error|error|ERROR/.test(l)).slice(-6).join("\n").slice(0, 800) +
          "\nWrite tests that import only names that exist now (or that the task itself names) and fail by assertion.";
        continue;
      }
      if (base === 0) {
        log(`test-first: the tests all pass on the unchanged code (attempt ${attempt})${attempt < 2 ? "; asking again" : "; dropped"}`);
        ask += "\n\nYour previous tests ALL PASS on the code as it is now, so they do not check the change this task asks " +
          "for. Write tests that fail on the current code and pass once the task is done.";
        continue;
      }
    }
    code = c;
  }
  if (!code) return skip();
  const file = path.join(out, "test_spec.py");
  const tmp = path.join(out, "test_spec.py.part");
  // run from the worktree: the project's modules import from the current directory
  fs.writeFileSync(tmp, withHeader(code));
  const cost = typeof j.total_cost_usd === "number" ? j.total_cost_usd : null;
  try {
    fs.appendFileSync(path.join(path.dirname(path.dirname(out)), "dispatch_costs.jsonl"),
      JSON.stringify({ at: new Date().toISOString(), what: "test-first", model: usedModel, cost_usd: cost }) + "\n");
  } catch { /* the ledger is best-effort */ }
  const bad = pythonSyntaxError(tmp);
  if (bad) {
    log(`test-first: the tests do not parse (${bad}); dispatch goes on without them`);
    return skip();                                 // the call is paid (ledger) either way
  }
  fs.renameSync(tmp, file);                        // whole and parsing, or not there at all
  const names = [...code.matchAll(/^\s*(?:async\s+)?def (test_\w+)/gm)].map((m) => m[1]);
  log(`test-first: ${names.length} test(s) by ${usedModel || "the writer"} (${names.slice(0, 6).join(", ")}${names.length > 6 ? ", ..." : ""}) -> ${file}`);
  const amb = /^#\s*AMBIGUOUS:\s*(.+)$/m.exec(code);
  // "# AMBIGUOUS: none" (a writer that fills in the line anyway) is not a flag: it stopped the cheap tries on a clear task
  const ambiguous = amb && !/^(none|no|n\/?a|not ambiguous|nothing)[\s.)]*$/i.test(amb[1].trim().replace(/^[(\[]/, "")) ? amb[1].trim().slice(0, 300) : null;
  return { file, cost_usd: cost, ...(ambiguous ? { ambiguous } : {}) };
}

// An open model's exec.json profile with an OpenAI-compatible endpoint, or null.
export function openWriterProfile(env, model) {
  try {
    const p = readExecConfig(env).profiles?.[model];
    return p?.openai_base_url ? p : null;
  } catch {
    return null;
  }
}

// The test writer on an open model: one chat call, the same instructions and request; -> what runClaudeAsync returns
// ({status, stdout: Claude Code's JSON line}), so the rest of writeSpecTests is the same for both.
async function runOpenWriter(profile, env, input, signal, timeoutS = 180) {
  try {
    const messages = profile.no_system_role
      ? [{ role: "user", content: `${SPEC_SYSTEM}\n\n${input}` }]
      : [{ role: "system", content: SPEC_SYSTEM }, { role: "user", content: input }];
    const res = await fetch(`${profile.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error",
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeoutS * 1000)]) : AbortSignal.timeout(timeoutS * 1000),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id, messages, max_tokens: 12000, temperature: 0, ...(profile.request_extras || {}) }),
    });
    const text = await res.text();
    if (!res.ok) return { status: 1, stdout: "", stderr: `HTTP ${res.status}: ${text.slice(0, 200)}` };
    const body = JSON.parse(text);
    const u = body?.usage || {};
    const cost = (profile.input_per_mtok || profile.output_per_mtok)
      ? ((u.prompt_tokens || 0) * (profile.input_per_mtok || 0) + (u.completion_tokens || 0) * (profile.output_per_mtok || 0)) / 1e6 : null;
    const result = body?.choices?.[0]?.message?.content || "";
    return { status: 0, stdout: JSON.stringify({ result, total_cost_usd: cost, is_error: !result }) };
  } catch (err) {
    return { status: 1, stdout: "", stderr: err.message };
  }
}

// The test writer can run: a Claude model and Claude Code to run it, or an open model with a profile.
export function testWriterAvailable(env = process.env, mine = "") {
  const model = String(env.STEALTHLAB_DISPATCH_TESTFIRST_MODEL || "").trim() || String(mine || "").split("|")[0];
  if (!model.includes("claude")) return Boolean(openWriterProfile(env, model));
  try { launchOrThrow("claude", { env }); return true; } catch { return false; }
}

// ---- provider quota: do not send work to a provider that has (almost) nothing left today -------------------------
// A provider out of daily tokens answers every request with 429: the cheap tries then burn the whole time limit and
// the session's model does the task anyway, later and at a higher total cost. One tiny request (a few tokens) reads
// the provider's rate-limit headers; the answer is cached for 5 minutes per provider. Unknown -> assume it is fine.
const QUOTA_TTL_MS = 5 * 60 * 1000;
const QUOTA_PROBE_MS = 5000;

function quotaCachePath(env) {
  return path.join(env.STEALTHLAB_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".stealthlab"),
    "provider_quota.json");
}

function headerNum(h, names) {
  for (const n of names) {
    const v = Number(h.get ? h.get(n) : h[n]);
    if (Number.isFinite(v)) return v;
  }
  return null;
}

// -> {day, minute} remaining tokens (null where the provider does not say), or null when it could not be read.
export async function providerQuota(profile, env = process.env, fetchImpl = globalThis.fetch, now = Date.now()) {
  const base = profile?.openai_base_url;
  if (!base) return null;
  let cache = {};
  try { cache = JSON.parse(fs.readFileSync(quotaCachePath(env), "utf8")) || {}; } catch { /* none yet */ }
  const hit = cache[base];
  if (hit && now - hit.at < QUOTA_TTL_MS) return hit.quota;
  let quota = null;
  try {
    // 5 s at most: a provider slow to answer this probe held a prompt 5-7 minutes (live check, 2026-10-14); no answer =
    // unknown, which is treated as fine
    const res = await fetchImpl(`${base.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(QUOTA_PROBE_MS),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(profile, env)}` },
      body: JSON.stringify({ model: profile.model_id || profile.name, messages: [{ role: "user", content: "hi" }], max_tokens: 1 }),
    });
    await res.text().catch(() => "");
    const day = headerNum(res.headers, ["x-ratelimit-remaining-tokens-day"]);
    const minute = headerNum(res.headers, ["x-ratelimit-remaining-tokens-minute", "x-ratelimit-remaining-tokens"]);
    quota = day === null && minute === null ? null : { day, minute };
    // a provider that just ANSWERED the probe is not out of tokens: "0 left" from a 200 is a header it does not keep
    // (z.ai returned day/minute 0 on a successful call, 2026-10-14) -- unknown, which counts as fine
    if (res.ok && quota && !quota.day && !quota.minute) quota = null;
  } catch { return null; }
  cache[base] = { at: now, quota };
  try { fs.mkdirSync(path.dirname(quotaCachePath(env)), { recursive: true }); fs.writeFileSync(quotaCachePath(env), JSON.stringify(cache)); } catch { /* best effort */ }
  return quota;
}

// The rungs whose provider has enough left today; the others are dropped with a log line.
export async function withQuota(rungs, env, { min, fetchImpl, log = () => {} } = {}) {
  if (!min) return rungs;
  let profiles = {};
  try { profiles = readExecConfig(env).profiles || {}; } catch { return rungs; }
  const byBase = new Map();
  const out = [];
  for (const r of rungs) {
    const prof = profiles[r.model];
    if (!prof?.openai_base_url || r.executor === "claude") { out.push(r); continue; }
    if (!byBase.has(prof.openai_base_url)) byBase.set(prof.openai_base_url, await providerQuota(prof, env, fetchImpl));
    const q = byBase.get(prof.openai_base_url);
    if (q && q.day !== null && q.day < min) { log(`dispatch: ${r.model} skipped -- its provider has ${q.day} tokens left today (< ${min})`); continue; }
    out.push(r);
  }
  return out;
}

// Extra seconds for the cheap tries, from the size of the project's top-level text files (the task's own material:
// a spec, a contract, a data sample): 4 s per KB, at most 210 s. A small task gets ~nothing; a 22 KB API contract
// ~90 s more.
export function taskAllowanceS(root) {
  let bytes = 0;
  try {
    for (const f of fs.readdirSync(root)) {
      if (f.startsWith(".") || !/\.(py|md|txt|json|ya?ml|js|mjs|ts|sql|html|css|csv|toml)$/i.test(f)) continue;
      try { const st = fs.statSync(path.join(root, f)); if (st.isFile()) bytes += Math.min(st.size, 100_000); } catch { /* skip */ }
    }
  } catch { return 0; }
  return Math.min(210, Math.round((bytes / 1024) * 4));
}

// The plan's rungs, in order, that a local executor can run, up to the session's own model. Claude models count
// too when an executor runs them (headless Claude Code on Sonnet, say -- the `claude` executor): nothing cheaper
// than the session's model ever runs inside the session.
// an exec.json profile for this model (an open model), or null
function openModelProfile(model, env) {
  try { return readExecConfig(env).profiles?.[model] || null; } catch { return null; }
}

export function dispatchableRungs(plan, mine, env = process.env, { claude = true } = {}) {
  const out = [];
  for (const unit of plan?.ladder || []) {
    const model = String(unit).split("|")[0];
    if (mine && model === String(mine).split("|")[0]) break;
    const local = executorFor(unit, env);
    if (!local) continue;                    // e.g. Sonnet as an in-session subagent: not dispatched, skipped
    if (local.executor === "claude" && !claude && !openModelProfile(model, env)) continue;
    if (!out.some((r) => r.unit === unit)) out.push({ unit, ...local });
  }
  return out;
}

// What dispatch tries: the plan's dispatchable rungs in its order, then the other open models this machine can run
// (exec.json), up to `max`. Trying them all costs cents; one often solves what another gets wrong.
export function cheapCandidates(plan, mine, env = process.env, { max = 3, claude = false, explore = 0, rand = Math.random } = {}) {
  // the plan puts the session's own model first: it expects the cheap models to fail on this kind of task, so none is
  // tried -- except, with probability `explore`, a check that they still do (models and providers change; a kind of
  // task the router gave up on must be able to come back)
  const first = String((plan?.ladder || [])[0] || "").split("|")[0];
  if (!first) return [];
  if (mine && first === String(mine).split("|")[0] && !(explore > 0 && rand() < explore)) return [];
  const out = dispatchableRungs(plan, mine, env, { claude });
  try {
    const cfg = readExecConfig(env);
    for (const [executor, spec] of Object.entries(cfg.executors || {})) {
      for (const m of (spec?.models || []).map(String)) {
        // Claude Code driving an OPEN model (an exec.json profile) is a cheap rung like any other; a Claude model
        // run by it is not (its cost is about the session model's own)
        if (executor === "claude" && !(cfg.profiles || {})[m]) continue;
        if (!out.some((r) => r.model === m)) out.push({ unit: `${m}|${executor}`, executor, model: m });
      }
    }
  } catch { /* no config: the plan's rungs only */ }
  return out.slice(0, max);
}

// The check to verify a dispatched run with: the configured one, else the matched library entry's own check, else the
// one the project itself defines (detectCheck).
export function dispatchCheck(policy, reply, root) {
  if (policy.check) return policy.check;
  const match = (reply?.library_matches || []).find((m) => m?.relation === "matches" && m.id);
  if (match && root) {
    try {
      const e = readEntry(root, match.id);
      const steps = (e?.procs || []).flatMap((p) => p.steps || []);
      const own = steps.map((s) => s.check).filter(Boolean).pop();
      if (own) return own;
    } catch { /* fall through to detection */ }
  }
  return policy.detect && root ? detectCheck(root) : null;
}

// The files this session's own model has edited so far (its transcript's Edit / Write / MultiEdit / NotebookEdit calls),
// relative to root. Reads at most the transcript's last 4 MB; no transcript -> none.
export function sessionEdits(transcriptPath, root) {
  const out = new Set();
  if (!transcriptPath) return out;
  try {
    const st = fs.statSync(transcriptPath);
    const len = Math.min(st.size, 4 * 1024 * 1024);
    const fd = fs.openSync(transcriptPath, "r");
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, st.size - len);
    fs.closeSync(fd);
    for (const line of buf.toString("utf8").split(/\r?\n/)) {
      if (!line.includes("tool_use")) continue;
      let o;
      try { o = JSON.parse(line); } catch { continue; }
      if (o?.type !== "assistant") continue;
      for (const b of o?.message?.content || []) {
        if (b?.type !== "tool_use" || !/^(Edit|Write|MultiEdit|NotebookEdit)$/.test(String(b.name))) continue;
        const f = b.input?.file_path || b.input?.notebook_path;
        if (!f) continue;
        const rel = path.isAbsolute(f) ? path.relative(root, f) : f;
        if (rel && !rel.startsWith("..")) out.add(rel.split(path.sep).join("/"));
      }
    }
  } catch { /* unreadable: none */ }
  return out;
}

// The files in the working tree changed since `since` (an ISO time, the session's start): modified, added or new files
// whose last write is at or after it. What the session changed, however it changed them (an Edit, or a script run by
// Bash, which the transcript's Edit calls do not show -- sqlglot-4051 turn 2 was dispatched while the session's own
// fix of the same files was there, ct-series-v5). None when there is no start or no git.
export function changedSince(root, since) {
  const out = new Set();
  const t = since ? Date.parse(since) : NaN;
  if (!root || Number.isNaN(t)) return out;
  try {
    const r = spawnSync("git", ["-C", root, "status", "--porcelain", "-z", "--untracked-files=all"], { encoding: "utf8", timeout: 5000, windowsHide: true });
    if (r.status !== 0) return out;
    const parts = r.stdout.split("\0");
    for (let i = 0; i < parts.length; i++) {
      const e = parts[i];
      if (e.length < 4) continue;
      if (e[0] === "R" || e[0] === "C") i++;                     // a rename/copy: the next field is the old name
      const f = e.slice(3);
      if (f.startsWith(".stealth/")) continue;
      try { if (fs.statSync(path.join(root, f)).mtimeMs >= t - 1000) out.add(f); } catch { /* deleted: not a file to compare */ }
    }
  } catch { /* none */ }
  return out;
}

const firstLine = (text) => String(text || "").split("\n").map((l) => l.trim()).find(Boolean)?.slice(0, 200) || "";

// The executor takes at most TASK_MAX characters in all (lib/exec/runtime.mjs). The request comes first and is never cut
// for the context; the repo context fills only what is left, and is dropped when less than CONTEXT_MIN would fit.
const CONTEXT_MIN = 300;
export function fitTask(task, ctx) {
  if (!ctx) return task;
  const room = TASK_MAX - task.length - 2;
  if (room < CONTEXT_MIN) return task;
  return `${task}\n\n${ctx.length <= room ? ctx : `${ctx.slice(0, room - 20)}\n[...context cut]`}`;
}

// the "likely place" line (lib/locate.mjs) after the request, inside TASK_MAX: the request is cut, never the line
export function withWhere(task, where) {
  if (!where) return task;
  if (task.length + where.length + 2 <= TASK_MAX) return `${task}\n\n${where}`;
  return `${task.slice(0, Math.max(0, TASK_MAX - where.length - 30))}\n[...cut]\n\n${where}`;
}

function taskText(prompt) {
  const t = String(prompt || "").trim();
  return t.length <= TASK_MAX ? t : `${t.slice(0, TASK_MAX - 40)}\n[...prompt truncated for the executor]`;
}

// -> {handled: true, text} when a cheap rung's verified change was applied; else {handled: false, tried, remaining}
//    (remaining: the plan's ladder after the rungs tried here), or null when dispatch does not apply.
// force: the prompt began with `!cheap` -- try the cheap models even where routing would skip, and skip the brief's gate
// briefImpl: for tests, a stand-in for brief.mjs makeBrief()
export async function dispatch({ payload: rawPayload, reply, root, mine, env = process.env, fetchImpl, log = () => {}, runtime, runClaude,
                                 force = false, briefImpl, triage = null, timing = null }) {
  // lib/routing_timing.mjs: marks the steps before the first cheap model starts (a stand-in that does nothing when off)
  const mark = (name) => { try { timing?.mark(name); } catch { /* timing never affects dispatch */ } };
  let payload = rawPayload;
  const dispatchStart = Date.now();
  const policy = dispatchPolicy(env);
  const plan = reply?.model_plan;
  if (!policy.enabled) return null;
  if (!plan || plan.status !== "ok") { log(`dispatch: not dispatched (no model plan${plan?.status ? `: ${plan.status}` : ""})`); return null; }
  if (!root) { log("dispatch: not dispatched (no .stealth/ in this project)"); return null; }
  const check = dispatchCheck(policy, reply, root);
  // no test command in the project: the tests written for this task (test-first) can be the whole check -- a weaker one,
  // so it needs the test writer, and an answer is accepted only if those tests exist and pass (STEALTHLAB_DISPATCH_SPEC_ONLY)
  const specOnly = !check && policy.specOnly && policy.testFirst && (runClaude || (pytestAvailable() && testWriterAvailable(env, mine)));
  if (!check && !specOnly) { log("dispatch: not dispatched (no check: no test command found or configured)"); return null; }
  const checkLabel = specOnly ? "the tests written for this task (the project has no test command)" : `\`${check}\``;
  // a task built on an image goes only to the cheap models that can see it (exec.json profile `vision: true`, e.g. a
  // GLM-V); none, and it is not dispatched (STEALTHLAB_DISPATCH_VISION=on treats every cheap model as able to see)
  const images = force ? [] : namedImages(payload.prompt, payload.cwd || root);
  // an image task keeps every candidate until the ones that can see are picked (a max-3 cut first dropped GLM-V)
  const seeOnly = images.length > 0 && !policy.vision;
  const strongSet = (() => { try { const ps = readExecConfig(env).profiles || {}; return new Set(Object.keys(ps).filter((k) => ps[k]?.strong)); } catch { return new Set(); } })();
  // a strong profile with "first": true starts in the first wave -- a fast strong model (glm-5.3-flash) loses nothing there
  // a profile's tier (1: the cheap models, the default; 2+: a middle model, tried only after them, before the session's)
  const tierOf = (() => { let ps = {}; try { ps = readExecConfig(env).profiles || {}; } catch { /* */ } return (m) => Math.max(1, Number(ps[m]?.tier) || 1); })();
  const strongFirstSet = (() => { try { const ps = readExecConfig(env).profiles || {}; return new Set([...strongSet].filter((k) => ps[k]?.first === true)); } catch { return new Set(); } })();
  const tierCount = (() => { try { const ps = readExecConfig(env).profiles || {}; return Object.keys(ps).filter((k) => tierOf(k) > 1).length; } catch { return 0; } })();
  let rungs = await withQuota(cheapCandidates(plan, mine, env, { max: seeOnly ? 16 : policy.maxRungs + strongSet.size + tierCount, claude: policy.claude,
                                                                    explore: force ? 1 : policy.explore }), env,
    { min: policy.minQuotaTokens, fetchImpl: fetchImpl || globalThis.fetch, log });
  if (!rungs.length) {
    log(`dispatch: not dispatched (${String(plan.ladder?.[0] || "").split("|")[0] === String(mine || "").split("|")[0]
      ? "the plan puts the session's model first for this kind of task" : "no cheap model available (quota or exec.json)"})`);
    return null;
  }
  if (policy.fastestFirst) {
    // the fast models get the parallel slots (lib/model_speed.mjs); a model without enough wins is placed as the median of the known ones
    const ordered = orderFastestFirst(rungs, modelSpeeds(root));
    if (ordered.some((r, i) => r !== rungs[i])) log(`dispatch: rungs fastest first: ${ordered.map((r) => r.model).join(", ")}`);
    rungs = ordered;
  }
  // the strong cheap model (profile strong: true) is never cut by the max-rungs limit. By default it is the LAST cheap
  // try: the small models keep their slots and their cheap wins, and the strong one starts when one of them is done
  // without a pass (first, it took the slot a faster small model used to win in: 17 -> 15 cheap wins on 20 tasks).
  // STEALTHLAB_DISPATCH_STRONG_FIRST=on, or "first": true on its profile, starts it in the first wave instead.
  {
    // an image task is cut to max-rungs only after the models that can see are picked (below)
    // a middle model (tier 2+) is not cut either: it was cut as one of the "weak" ones, so GLM-5.3 never ran when three
    // cheap models came before it (ct-smoke-final4, 2026-10-17)
    const weak = rungs.filter((r) => !strongSet.has(r.model) && tierOf(r.model) === 1).slice(0, seeOnly ? 16 : policy.maxRungs);
    const strong = rungs.filter((r) => strongSet.has(r.model));
    const middle = rungs.filter((r) => !strongSet.has(r.model) && tierOf(r.model) > 1);
    const first = policy.strongFirst ? strong : strong.filter((r) => strongFirstSet.has(r.model));
    rungs = [...first, ...weak, ...strong.filter((r) => !first.includes(r)), ...middle];
  }
  if (images.length && !policy.vision) {
    const profiles = (() => { try { return readExecConfig(env).profiles || {}; } catch { return {}; } })();
    rungs = rungs.filter((r) => profiles[r.model]?.vision === true).slice(0, policy.maxRungs);
    if (!rungs.length) {
      log(`dispatch: not dispatched (the task needs an image no cheap model here can see: ${images.join(", ")})`);
      return null;
    }
  }
  // does it ask for a change now? The server's judge decided (the hook's POST /triage, with the earlier turns): only
  // "change" goes to the cheap models; a question, a confirmation, conversation, an unclear change -- or no verdict --
  // stay with the session's model. No word rules here.
  if (policy.gate && !force) {
    const work = triage?.work;
    if (work !== "change") {
      log(`dispatch: not dispatched (${work ? `the judge says work=${work}` : `no verdict from the server's judge${triage?.reason ? `: ${triage.reason}` : ""}`})`);
      return null;
    }
    log(`dispatch: the judge${triage.provider ? ` (${triage.provider})` : ""} says work=change`);
  }
  mark("gate_end");
  // what the cheap models are told: the conversation and the request as the user said it (lib/brief.mjs)
  const { conversationPrompt, makeBrief, sessionTurns } = await import("./brief.mjs");
  const turns = sessionTurns({ transcriptPath: payload.transcript_path, sessionId: payload.session_id, current: payload.prompt, env });
  payload = { ...payload, prompt: conversationPrompt(turns, payload.prompt, TASK_MAX) };
  // the brief: names to search the code for, and the parts of a request that asks for several things (no gate)
  let brief = null;
  if (policy.brief) {
    brief = await (briefImpl || makeBrief)({ prompt: rawPayload.prompt, transcriptPath: payload.transcript_path,
                                            cwd: payload.cwd || root, env, fetchImpl, log });
    if (brief) log(`dispatch: brief: ${String(brief.task || "").replace(/\s+/g, " ").slice(0, 240)}`);
  }
  mark("brief_end");
  const briefed = { brief, turns, conversationPrompt };
  // where in this repository the task is (lib/locate.mjs): the test writer gets that code, the cheap models a
  // path:start-end line their agent preloads, and the session's model the same pointer if the cheap tries fail
  // the brief's search terms join the request's own words: a plain-language issue ("double pipe ... ms sql server")
  // names nothing the code spells, and the brief has seen the file names (tsql.py, ||)
  const searchTerms = (brief?.search_terms || []).map((t) => `\`${String(t).replace(/`/g, "")}\``).join(" ");
  const locPrompt = `${rawPayload.prompt || ""}\n${payload.prompt || ""}\n${searchTerms}`;
  const searched = locate({ prompt: locPrompt, root: payload.cwd || root, env });
  // a cheap model narrows the search to the files and functions the fix is in (lib/locate_model.mjs); the test file is
  // the search's for the first file picked
  let loc = searched;
  if (locateEnabled(env)) {
    const { narrowLocation } = await import("./locate_model.mjs");
    loc = await narrowLocation({ prompt: rawPayload.prompt || payload.prompt, root: payload.cwd || root, loc: searched, env, fetchImpl, log });
    if (loc !== searched && loc.files[0]) loc = { ...loc, testFile: testFileFor(payload.cwd || root, loc.files[0]) || searched.testFile };
  }
  mark("locate_end");
  const where = renderWhere(loc);
  if (loc.spots.length) log(`dispatch: located ${loc.spots.map((s) => `${s.file}:${s.start}-${s.end}`).join(", ")}${loc.testFile ? ` (tests: ${loc.testFile})` : ""}`);
  if (where) {
    // the earlier turns are cut to leave the location line its room inside the executor's TASK_MAX
    payload = { ...payload, prompt: briefed.conversationPrompt(briefed.turns, rawPayload.prompt, TASK_MAX - where.length - 2) };
  }
  // a follow-up in a session whose own model is already changing the located code: the session finishes its own work
  // (sqlglot-4051 turn 2: the follow-up was dispatched, split from the entry recorded 12 s earlier from turn 1, every
  // cheap model failed part 1 after 19 min, and the session then reworked the fix, ct-series-v4)
  // A change a cheap model made earlier in this session, with the file still as it left it, is not the session's own
  // work: a follow-up on it may go to the cheap models again (they are told the conversation, that turn included).
  if (!force && !policy.followups && payload.transcript_path) {
    const theirs = cheapWritten(payload.session_id, payload.cwd || root, { env });
    const edited = new Set([...sessionEdits(payload.transcript_path, payload.cwd || root),
                            ...changedSince(payload.cwd || root, sessionStartedAt(payload.transcript_path))].filter((f) => !theirs.has(f)));
    const overlap = (loc.files || []).filter((f) => edited.has(f));
    if (overlap.length) {
      log(`dispatch: not dispatched (a follow-up: this session already changed ${overlap.join(", ")})`);
      return null;
    }
  }
  // a repo area where the cheap models keep losing (lib/areas.mjs): straight to the session's model, which saves the
  // wait and the cheap models' spend there; now and then (explore) they still try, so an area can recover
  const area = loc.files[0] || null;
  if (area && policy.areaSkip && !force) {
    const rec = readArea(root, area);
    if (rec && rec.losses >= policy.areaSkipAfter && rec.wins === 0 && Math.random() >= policy.explore) {
      log(`dispatch: not dispatched (cheap models lost ${rec.losses} of ${rec.losses} tries in ${area} here)`);
      return null;
    }
  }
  const rt = runtime || new (await import("./exec/runtime.mjs")).ExecRuntime({ env, fetchImpl });
  const tried = [];
  // test-first: tests from the task join the project's check for every cheap rung. They are written WHILE the cheap
  // models work (a cheap model takes ~50 s to reach its first check, the tests ~20 s): the check's last step waits
  // for them. Whatever the project's check is (pytest, vitest, a SQL or page check), the tests are a pytest file.
  let spec = null;
  let specDone = Promise.resolve(null);
  const specAbort = new AbortController();
  // the project's pytest check judged against the unchanged code: tests that already fail there (an environment-bound
  // test, a flaky one) do not fail an answer, only NEW failures do (STEALTHLAB_DISPATCH_BASELINE_CHECK). In the Modal
  // containers 5 permission tests fail as root on any code, and they failed all 13 cheap answers of a real issue.
  // Never for a test the task names (a failing test for this very bug must still fail the answer). It does not wait
  // for, or need, tests written for the task: it once did, and with no test writer (the default rule) every check
  // waited 300 s for a file that never came and then failed every answer on those 5 tests (ct-series-v6).
  // a rule on evidence (lib/accept.mjs) when the project has its own check: the agents work against that check alone, and
  // tests are written only when the rule uses them
  const acceptRule = check && !specOnly ? parseRule(policy.accept) : null;
  const evidence = !!acceptRule;
  const testFirstOn = policy.testFirst && (runClaude || pytestAvailable()) && (!evidence || ruleUses(acceptRule, "T", "T*"));
  if (evidence && ruleUses(acceptRule, "T") && !testFirstOn) {
    log("dispatch: not dispatched (the acceptance rule needs a reproduction test and no test writer can run here)");
    return null;
  }
  const workDir = testFirstOn || (check && policy.baselineCheck) ? specDir(env) : null;
  let checkRun = check;
  if (check && !specOnly && (testFirstOn || evidence) && policy.baselineCheck && /\bpytest\b/.test(check)) {
    const bdir = workDir;
    checkRun = `${pythonBin({ env })} "${writeBaselineCheckRunner(bdir, check)}"`;
    const named = String(`${rawPayload.prompt || ""}\n${payload.prompt || ""}`);
    runShellCapture(check, { cwd: payload.cwd || root, timeoutMs: 300000, signal: specAbort.signal }).then(({ code, out }) => {
      const fails = (code === 1 ? failingTests(out) : []).filter((id) => {
        const name = id.split("::").pop().replace(/\[.*$/, "");
        return !named.includes(name) && !named.includes(id.split("::")[0]);
      });
      try {
        if (fails.length) fs.writeFileSync(path.join(bdir, "BASE_FAILS"), fails.join("\n") + "\n");
        else fs.writeFileSync(path.join(bdir, "BASE_NONE"), String(code ?? "none"));
      } catch { /* */ }
      if (fails.length) log(`dispatch: ${fails.length} test(s) already fail on the unchanged code: answers are judged on new failures only`);
    });
  }
  let fullCheck = checkRun;
  let specDirPath = null;
  let specRunnerPath = null;               // evidence mode: our tests are run on a passing answer, not in its check
  // the module's own tests, when they joined the check (for run.md and the note to the session's model)
  const regressIn = () => (specDirPath && fs.existsSync(path.join(specDirPath, "REGRESS_OK")) ? loc.testFile : null);
  if (testFirstOn) {
    const dir = workDir;
    specDirPath = dir;
    const py = pythonBin({ env });
    if (evidence) specRunnerPath = writeSpecRunner(dir);
    else fullCheck = specOnly ? `${py} "${writeSpecRunner(dir, { strict: true })}"`
      : `${checkRun} && ${py} "${writeSpecRunner(dir)}"`;
    // the located module's existing tests: in the check when they pass on the unchanged code (run now, alongside)
    if (policy.regression && loc.testFile && /\.py$/.test(loc.testFile) && specOnly) {
      fullCheck += ` && ${py} "${writeRegressRunner(dir)}"`;
      runAsync(py, ["-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", ...noCovArgs(py, payload.cwd || root), loc.testFile], { cwd: payload.cwd || root, timeoutMs: 180000, signal: specAbort.signal })
        .then((code) => {
          const ok = code === 0;
          log(`dispatch: ${loc.testFile} ${ok ? "passes on the unchanged code: it joins the check" : "does not pass on the unchanged code: not in the check"}`);
          try { fs.writeFileSync(path.join(dir, ok ? "REGRESS_OK" : "REGRESS_SKIP"), ok ? loc.testFile : ""); } catch { /* */ }
        });
    }
    specDone = writeSpecTests({ prompt: payload.prompt, root: payload.cwd || root, mine, check, env, log, runClaude, dir,
                                signal: specAbort.signal, located: renderForWriter(loc, payload.cwd || root),
                                baseline: policy.specBaseline && !runClaude, python: py,
                                // the reproduction agent (lib/repro_agent.mjs; STEALTHLAB_DISPATCH_TESTFIRST_AGENT=on)
                                agentWriter: reproAgentEnabled(env) && !runClaude
                                  ? () => reproduceTests({ rt, root: payload.cwd || root, prompt: payload.prompt, where, env, log, signal: specAbort.signal, dir })
                                  : null })
      .then((s) => { spec = s; return s; })
      .catch((err) => { log(`test-first: ${err.message}`); try { fs.writeFileSync(path.join(dir, "SKIP"), ""); } catch { /* */ } return null; });
  }

  // the repo's own facts and the matched library entries, after the task (lib/task_context.mjs; STEALTHLAB_DISPATCH_CONTEXT=off)
  const ctx = repoContext({ reply, root, cwd: payload.cwd || root, prompt: payload.prompt, env });
  // the context pack (lib/context_pack.mjs; STEALTHLAB_DISPATCH_CONTEXT_PACK=on): what to read and run first, and the
  // project's conventions, before the "likely place" line
  const pack = contextPackEnabled(env) ? contextPack({ files: loc.files, testFile: loc.testFile }) : "";
  const taskWithContext = fitTask(withWhere(taskText(payload.prompt), [pack, where].filter(Boolean).join("\n\n")), ctx);
  // the limit on all the cheap tries together, and the ceiling of any one model's own cap: 150 s plus an allowance for the task's material
  const baseLimitS = policy.timeoutS;
  const running = new Map();               // unit -> run_id of a started run, for cancelling the rest once one passes
  // one rung: start it and wait for its result -> {rung, run_id, result} | {rung, error}
  const runRung = async (rung) => {
    let runId = null;
    let startedAt = Date.now();
    let capS = null;
    let capped = false;
    let capTimer = null;
    try {
      const started = await rt.achieve({ repo_path: payload.cwd || root, task: taskWithContext, checks: [fullCheck],
        scope: policy.scope, executor: rung.executor, model: rung.model, base: "working-tree",
        // one run's limits are the tries' own: it cannot usefully outlast them, and a run that prints nothing for that
        // long has stopped working
        timeout_s: Math.min(3600, Math.ceil(policy.runTimeoutS || baseLimitS)), hang_s: Math.min(3600, Math.ceil(policy.runTimeoutS || baseLimitS)),
        ...(plan.instance_key ? { instance_key: plan.instance_key } : {}) });
      runId = started.run_id;
      running.set(rung.unit, runId);
      startedAt = Date.now();
      // this MODEL's own cap (from its wins in this repo): past it the run hands in its change, which is checked and judged
      // like any other (the time limit's rule); a runtime without hand-in cancels it, which is no verdict on the model
      const cap = policy.modelCap ? modelCapS(modelWins(root, rung.model, rung.executor)?.wins, { baseS: baseLimitS, mult: policy.capMult, floorS: policy.capFloorS }) : null;
      if (cap !== null) {
        capS = cap;
        capTimer = setTimeout(() => {
          capped = true;
          log(`dispatch: ${rung.model} capped at ${Math.round(cap * 10) / 10} s: it hands in its change`);
          (typeof rt.finishRun === "function" ? rt.finishRun(runId) : rt.cancelRun(runId)).catch(() => {});
        }, cap * 1000);
        capTimer.unref?.();
      }
      let result;
      do {
        result = await rt.runResult(runId, 55);
      } while (!TERMINAL.has(result?.state));
      return { rung, run_id: runId, result, startedAt, capped, capS };
    } catch (err) {
      return { rung, run_id: runId, error: err, startedAt, capped, capS };
    } finally {
      clearTimeout(capTimer);
    }
  };
  // a finished rung -> true when its answer is accepted
  const judge = async (d) => {
    const { rung, result, error, startedAt, capped, capS } = d;
    if (result?.state === "verified") await specDone;     // settled by then: the check waited for the tests
    if (error) {
      log(`dispatch ${rung.unit}: ${error.message}`);
      tried.push({ unit: rung.unit, state: "error", error: String(error.message).slice(0, 200) });
      return false;
    }
    let ok = result.state === "verified";
    // the test writer said the task can be read two ways with different results: a passing cheap answer may have taken
    // the other reading, which no test of ours can tell -- the session's model does the task (STEALTHLAB_DISPATCH_AMBIGUITY)
    // a task built on a screenshot: the page must also LOOK like it (lib/visual_check.mjs); no verdict = not accepted
    if (ok && images.length && policy.visualCheck) {
      const { visualCheck } = await import("./visual_check.mjs");
      const v = await visualCheck({ worktree: result.diff?.worktree, files: result.diff?.files || [], images,
                                    repo: payload.cwd || root, env, fetchImpl, log, python: pythonBin({ env }) });
      if (!v || !v.match) {
        log(`dispatch ${rung.unit}: ${v ? `the page does not match the screenshot (${v.why})` : "no visual verdict"}`);
        tried.push({ unit: rung.unit, state: v ? `passed the check, rejected by the visual check (${v.why})` : "passed the check, no visual verdict",
                     run_id: result.run_id, cost_usd: result.cost_usd });
        return false;
      }
    }
    if (ok && spec?.ambiguous && policy.ambiguityStop) {
      ambiguous = spec.ambiguous;
      tried.push({ unit: rung.unit, state: "passed, not delivered: the task can be read two ways", run_id: result.run_id,
                   cost_usd: result.cost_usd });
      return false;
    }
    if (result.usage_limited) {            // the executor's account or the provider hit a limit: no verdict on the model
      tried.push({ unit: rung.unit, state: "usage_limited", run_id: result.run_id, cost_usd: result.cost_usd });
      log(`dispatch ${rung.unit}: no pass (the executor or provider hit a usage limit: no verdict on the model)`);
      return false;
    }
    // the executor failed before changing anything (its endpoint kept refusing, say): the model was not reached, so this
    // is no verdict on it either -- not a loss in the route's counts (lib/exec/runtime.mjs "the executor failed")
    if (result.state === "failed" && /^the executor failed/.test(String(result.error || ""))) {
      log(`${rung.unit}: ${String(result.error).slice(0, 200)}`);
      tried.push({ unit: rung.unit, state: "executor error", error: String(result.error).slice(0, 200), run_id: result.run_id, cost_usd: result.cost_usd });
      return false;
    }
    if (result.state === "cancelled") {    // stopped from outside: no verdict on the model either
      // by its own model's cap (slower than its wins here usually are): not a failure of the model either
      tried.push({ unit: rung.unit, state: capped ? `capped at ${Math.round(capS * 10) / 10} s` : "cancelled", run_id: result.run_id, cost_usd: result.cost_usd });
      log(`dispatch ${rung.unit}: no pass (${tried[tried.length - 1].state})`);
      return false;
    }
    if (ok && evidence) {
      // passed the project's check: the rule decides with the evidence (and the other answers), in the loop below
      await specDone;
      d.evidence = await answerEvidence({ rule: acceptRule, result, spec, specRunner: specRunnerPath, python: pythonBin({ env }),
        prompt: rawPayload.prompt || payload.prompt, env, log, signal: specAbort.signal, fetchImpl, unit: rung.unit });
      log(`dispatch ${rung.unit}: passes the project's check; evidence: ${evidenceLine(d.evidence)}`);
      return true;
    }
    let cross = null;
    // The session model's own tests (test-first) are the independent judge when they exist: a cheap verifier's tests
    // are weaker and, on code that is not a standalone module, can fail a correct answer. The cross-check covers only
    // a task test-first could not write tests for.
    if (ok && policy.crosscheck !== "off" && !spec) {
      cross = await crossCheck({ rt, rung, result, rungs, env, payload, root, check, scope: policy.scope, log,
                                 onStart: (id) => extraRuns.add(id) });
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
    if (ok) recordModelWin(root, rung.model, rung.executor, (Date.now() - startedAt) / 1000);       // this model's own time to a pass
    tried.push({ unit: rung.unit, state: cross?.verdict === "disagree" ? "rejected by cross-check"
                   : result.stalled ? `failed, stalled (rule ${result.stalled.rule})` : result.state,
                 run_id: result.run_id, cost_usd: result.cost_usd, ...(cross ? { cross } : {}),
                 tail: ok ? "" : (cross?.verdict === "disagree" ? cross.tail
                   : String((result.checks || []).map((c) => c.tail || "").join("\n")).slice(-600)) });
    if (!ok && cross?.verdict === "disagree") try { await rt.cancelRun(result.run_id); } catch { /* best effort */ }
    // a failed answer is logged too: the hooks log showed nothing for gemma and flash failing (ct-smoke-final4)
    if (!ok) log(`dispatch ${rung.unit}: no pass (${tried[tried.length - 1].state})`);
    return ok;
  };

  // a task in parts, planned from the closest fix already made here (lib/dag_run.mjs); no such fix -> whole, as before
  if (policy.dag && !images.length) {
    const py = pythonBin({ env });
    // a part is checked by "no new failures": the project's check judged against the unchanged code, or with no check
    // here, that the Python still compiles (the tests written for the whole task run on the parts together, at the end)
    const interCheck = checkRun || `${py} -c "import compileall, sys; sys.exit(0 if compileall.compile_dir('.', quiet=1) else 1)"`;
    let dagRes = null;
    try {
      dagRes = await runDag({ rt, root, payload, rawPayload, reply, loc, where, rungs, strongSet, policy, interCheck, fullCheck,
        specDone, env, log, instanceKey: plan.instance_key, runShell: (cmd, o) => runShellCapture(cmd, { ...o, signal: specAbort.signal }),
        // each part's outcome counts for its model on this task's route, as a whole-task try does (routing.md OBS)
        obs: (model, executor, ok) => { try { if (plan.goal_id) recordObs(root, ensureRoute(root, plan.goal_id), model, executor, ok); } catch { /* best effort */ } },
        goalId: plan.goal_id || null,
        // no similar fix here: the request's own parts, when the brief found several (lib/brief.mjs)
        parts: briefed?.brief?.parts || [],
        undo: { enabled: undoEnabled(env) && !policy.ask,
                create: (o) => createUndoPoint({ ...o, env }), finish: (p, o) => finishUndoPoint(p, { ...o, env }), discard: (p, o) => discardUndoPoint(p, { ...o, env }) } });
    } catch (err) {
      log(`dag: ${err.message}; the task goes whole`);
    }
    if (dagRes) {
      specAbort.abort();
      const runMd = writeDagRun(root, { task: rawPayload.prompt, nodes: dagRes.handled ? dagRes.dag.nodes : dagRes.partial.nodes,
        entry: dagRes.handled ? dagRes.dag.entry : dagRes.partial.entry, interCheck, fullCheck });
      if (!dagRes.handled) {
        log("dispatch: the parts were not all done by cheap models; the session's model takes over");
        return { ...dagRes, spec, loc, runMd, remaining: plan.ladder || [] };
      }
      let entry = null, diff = "";
      try {
        const { promptTitle } = await import("./capture_hook.mjs");
        diff = diffFromGit(root);
        if (diff.trim()) entry = addEntry(root, { title: promptTitle(rawPayload.prompt), check: check || "test-first tests", observed: true, diff,
                                                  steps: wayStepsForEntry(diff) }).id;
      } catch (err) { log(`dispatch: library entry not added (${err.message})`); }
      if (entry) await recordEvidenceFor({ root, entry, check: check || "test-first tests", winner: null, env, log });
      const { explainChange } = await import("./explain.mjs");
      const [explanation] = await Promise.all([
        explainChange({ request: rawPayload.prompt, diff, check: check || "tests written for this task", summary: "", env, fetchImpl, log }),
        entry ? describeEntry({ root, id: entry, task: rawPayload.prompt, diff, env, fetchImpl, log }) : null]);
      const secs = Math.round((Date.now() - dispatchStart) / 1000);
      const parts = dagRes.dag.nodes.length;
      return { handled: true, dag: dagRes.dag, files: dagRes.files, tried: dagRes.tried, spec, loc, runMd, library_entry: entry, explanation,
        model: dagRes.models, unit: `${dagRes.models}|dag`,
        text: `Done in ${parts} parts by ${dagRes.models} in ${secs} s (StealthLab ${dagRes.dag.entry === "request" ? "split the request into the things it asks for"
          : "planned it from a similar fix made here"} and dispatched each part ` +
          `before this session's model ran, $${(dagRes.cost_usd || 0).toFixed(4)}). All parts together passed ${checkLabel} in an isolated worktree ` +
          `and the change is applied: ${dagRes.files.join(", ") || "no files"}.` + (entry ? ` Recorded in the library as ${entry}.` : "") +
          (dagRes.undone ? " To put it back as it was: stealthlab-mcp undo." : "") + (explanation ? `\n\n${explanation}` : "") };
    }
  }

  const queue = [...rungs].map((r, i) => [r, i]).sort((a, b) => tierOf(a[0].model) - tierOf(b[0].model) || a[1] - b[1]).map(([r]) => r);
  const pending = new Map();               // unit -> promise of its finished run
  let tierStarted = false;                 // a middle model has started: it has its own time (policy.tierS)
  const width = policy.parallel ? Math.min(rungs.length, policy.maxParallel) : 1;
  const profilesNow = (() => { try { return readExecConfig(env).profiles || {}; } catch { return {}; } })();
  // the executor's own limit on runs at once (exec.json max_concurrent_runs): a rung past it waits for a slot instead of
  // failing at start -- minimax lost its only try to "2 runs already active" (ct-smoke-final4, 2026-10-17)
  const runCap = (() => { try { return readExecConfig(env).max_concurrent_runs || Infinity; } catch { return Infinity; } })();
  // a rung's provider: its profile's endpoint host, else its executor
  const providerOf = (r) => { try { return new URL(profilesNow[r.model]?.openai_base_url).host; } catch { return r.executor || "?"; } };
  let winner = null;
  // the time limit on the cheap tries: past it the session's model takes over (a pass takes ~50 s, p90 ~60 s)
  // ... plus an allowance for the material the task comes with: an agent reads a 20 KB contract many times over
  // the strong model works more slowly (more context, more steps): its run gets extra time
  let limitS = baseLimitS + (rungs.some((r) => strongSet.has(r.model)) ? policy.strongExtraS : 0);
  let timer;
  let fireDeadline;
  const triesStart = Date.now();
  // the deadline timers are NOT unref'd: dispatch awaits them, so they must keep the hook's process alive while it waits
  // (unref'd, a dispatch whose runs were stuck in pure waits let the process exit with nothing delivered); every one is
  // cleared when the tries end
  let deadline = new Promise((resolve) => { fireDeadline = resolve; timer = setTimeout(() => resolve({ timeout: true }), limitS * 1000); });
  // the extra time is the strong model's: once it has finished without a pass (failed, stalled, capped), the others get
  // the plain limit again -- on a real issue they waited 240 s more after flash had already stopped (Modal, 2026-10-15)
  const dropStrongExtra = () => {
    if (limitS === baseLimitS || tierStarted) return;
    const strongLeft = queue.some((r) => strongSet.has(r.model)) || rungs.some((r) => strongSet.has(r.model) && pending.has(r.unit));
    if (strongLeft) return;
    limitS = baseLimitS;
    clearTimeout(timer);
    timer = setTimeout(() => fireDeadline({ timeout: true }), Math.max(0, baseLimitS * 1000 - (Date.now() - triesStart)));
  };
  let timedOut = false;
  let handedIn = false;                    // the time was up and the runs still going were told to hand in their change
  const cands = [];                        // evidence mode: answers that passed the project's check
  const extraRuns = new Set();             // cross-check runs: cancelled with the rest when the time is up
  const seenFails = [];                    // the failing advance tests of each failed rung, as a sorted list
  let sharedFail = null;
  let ambiguous = null;                    // the test writer's note that the task reads two ways (stops the cheap tries)
  const ambiguityKnown = specDone.then((s) => (s?.ambiguous && policy.ambiguityStop ? { ambiguityFlag: s.ambiguous }
    // no valid reproduction test (the writer could not write one that fails on the unchanged code): an answer the rule
    // needs T for can never be accepted, so the tries stop now instead of at the limit
    : !s && testFirstOn && (specOnly || ruleUses(acceptRule, "T")) ? { noSpec: true } : new Promise(() => {})));
  let noSpec = false;
  while (!winner && (queue.length || pending.size)) {
    // the limit is per provider: it is there because 3 agents on one provider exceed its tokens-per-minute; a model on
    // another provider (glm-5.3-flash on z.ai) does not take a General Compute slot (Modal, sqlglot-4051: minimax got
    // the last 2 of 10 minutes because flash held one of the two slots)
    for (let i = 0; i < queue.length;) {
      const p = providerOf(queue[i]);
      // a strong model meant to go last (no first: true) still waits until the small ones have all started
      const holdLast = strongSet.has(queue[i].model) && !policy.strongFirst && !strongFirstSet.has(queue[i].model)
        && queue.some((r) => !strongSet.has(r.model));
      // a middle model waits for every cheaper one to finish (queued or running)
      const t = tierOf(queue[i].model);
      const holdTier = t > 1 && [...queue, ...rungs.filter((r) => pending.has(r.unit))].some((r) => tierOf(r.model) < t);
      if (!holdLast && !holdTier && pending.size < runCap && [...pending.keys()].filter((u) => providerOf(rungs.find((r) => r.unit === u) || {}) === p).length < width) {
        const rung = queue.splice(i, 1)[0];
        if (t > 1 && !tierStarted) {
          // the middle model's own time from now: the cheap models' limit has been used up by them
          tierStarted = true;
          limitS = Math.round((Date.now() - triesStart) / 1000) + policy.tierS;
          clearTimeout(timer);
          deadline = new Promise((resolve) => { fireDeadline = resolve; timer = setTimeout(() => resolve({ timeout: true }), policy.tierS * 1000); });
          log(`dispatch: the cheap models did not do it; ${rung.model} (tier ${t}) tries, ${policy.tierS} s`);
        }
        mark("first_rung");
        pending.set(rung.unit, runRung(rung));
      } else i++;
    }
    // the test writer's ambiguity flag (~20 s in) ends the cheap tries at once, not when the first one finishes
    const done = await Promise.race([...pending.values(), deadline, ambiguityKnown]);
    if (done.ambiguityFlag) { ambiguous = done.ambiguityFlag; break; }
    if (done.noSpec) { noSpec = true; break; }
    if (done.timeout && !tierStarted && queue.some((r) => tierOf(r.model) > 1)) {
      // the cheap models' time is up: the ones still running stop, the cheap ones queued are skipped, the middle model tries
      for (const unit of pending.keys()) {
        const runId = running.get(unit);
        if (runId) try { await settleWithin(rt.cancelRun(runId), SETTLE_MS); } catch { /* best effort */ }
      }
      for (const [unit, p] of pending) {
        const r = await settleWithin(p, SETTLE_MS);
        tried.push({ unit, state: "timed out", run_id: r?.run_id ?? running.get(unit) ?? null, cost_usd: r?.result?.cost_usd ?? null });
        log(`dispatch ${unit}: no pass (timed out at the cheap models' limit)`);
        running.delete(unit);
      }
      pending.clear();
      for (const r of queue.filter((x) => tierOf(x.model) === 1)) tried.push({ unit: r.unit, state: "skipped (time limit)" });
      queue.splice(0, queue.length, ...queue.filter((x) => tierOf(x.model) > 1));
      continue;
    }
    if (done.timeout && !handedIn && pending.size && typeof rt.finishRun === "function") {
      // the time is up: the cheap models still working stop and hand in their change, which is checked and judged like
      // any other -- the limit bounds the work, not the judging (ct-series-v7 sqlglot: a right answer was cut at 600 s
      // unchecked). The judging is bounded by its own limits (the runtime's check limit, the reviewer's); this one is
      // only the hang guard over both.
      handedIn = true;
      log(`dispatch: the ${limitS} s are up: ${[...pending.keys()].join(", ")} stop and hand in their change`);
      for (const unit of pending.keys()) {
        const id = running.get(unit);
        if (id) try { await rt.finishRun(id); } catch { /* best effort */ }
      }
      clearTimeout(timer);
      deadline = new Promise((resolve) => { fireDeadline = resolve; timer = setTimeout(() => resolve({ timeout: true }), policy.handInS * 1000); });
      continue;
    }
    if (done.timeout) { timedOut = true; break; }
    pending.delete(done.rung.unit);
    running.delete(done.rung.unit);
    // the judging (waiting for the advance tests, a cross-check run) counts against the same time limit: a cross-check
    // stuck in a model call held a task for 18 minutes past a 394 s limit (2026-10-15)
    const verdict = await Promise.race([judge(done), deadline]);
    if (verdict?.timeout) {
      timedOut = true;
      tried.push({ unit: done.rung.unit, state: "timed out while being checked", run_id: done.run_id ?? null, cost_usd: done.result?.cost_usd ?? null });
      break;
    }
    if (verdict && evidence) {
      cands.push(done);
      // the other models still running may finish a little later: answers that agree are stronger evidence
      // waiting for a second answer only helps a rule that compares answers (A, C)
      const until = Date.now() + (ruleUses(acceptRule, "A", "C") ? policy.agreeWaitS : 0) * 1000;
      while (pending.size && Date.now() < until && cands.length < 3) {
        let tick;
        const over = new Promise((res) => { tick = setTimeout(() => res({ waitOver: true }), Math.max(0, until - Date.now())); });
        const more = await Promise.race([...pending.values(), deadline, over]);
        clearTimeout(tick);
        if (more.waitOver || more.timeout) break;
        pending.delete(more.rung.unit);
        running.delete(more.rung.unit);
        const v2 = await Promise.race([judge(more), deadline]);
        if (v2?.timeout) break;
        if (v2) cands.push(more);
      }
      // C: the answers run each other's validated tests (only when the rule uses it: it runs their tests)
      if (ruleUses(acceptRule, "C") && cands.length > 1) await crossTest(cands, { python: pythonBin({ env }), log, env });
      const pick = decide(cands, acceptRule);
      if (pick) {
        winner = pick;
        try { if (plan.goal_id) recordObs(root, ensureRoute(root, plan.goal_id), pick.rung.model, pick.rung.executor, true); } catch { /* best effort */ }
        recordModelWin(root, pick.rung.model, pick.rung.executor, (Date.now() - pick.startedAt) / 1000);
        log(`dispatch: ${pick.rung.unit} accepted by the rule (${policy.accept}): ${evidenceLine(pick.evidence)}`);
        tried.push({ unit: pick.rung.unit, state: "verified", run_id: pick.result.run_id, cost_usd: pick.result.cost_usd, evidence: evidenceLine(pick.evidence) });
        for (const c of cands) if (c !== pick && !c.recorded) {
          tried.push({ unit: c.rung.unit, state: "passed the check, another answer was taken", run_id: c.result.run_id, cost_usd: c.result.cost_usd });
          try { await rt.cancelRun(c.result.run_id); } catch { /* best effort */ }
        }
        break;
      }
      // not accepted: no verdict on the model (the answer may be right); a later answer may still agree with it
      for (const c of cands) if (!c.recorded) {
        c.recorded = true;
        tried.push({ unit: c.rung.unit, state: `passed the check, not accepted by the rule (${evidenceLine(c.evidence)})`,
                     run_id: c.result.run_id, cost_usd: c.result.cost_usd, patch: c.result.diff?.patch_path || null,
                     review: c.evidence?.Rs_why || c.evidence?.R_why || null });
      }
      if (timedOut) break;
      continue;
    }
    if (verdict) { winner = done; break; }
    if (ambiguous) break;
    dropStrongExtra();
    // two models failing the SAME advance tests: the test is as likely wrong as both answers, and a third model rarely
    // rescues it (2 of 116 tasks in the A-vs-C runs) -- the session's model takes over now instead of at the time limit
    const fails = specFailures(done.result);
    if (fails && policy.sharedFailStop && seenFails.includes(fails)) {
      // the strong cheap model still gets its try: the small ones queued are skipped, and the stop waits for the strong one
      const strongLeft = queue.some((r) => strongSet.has(r.model)) || rungs.some((r) => strongSet.has(r.model) && pending.has(r.unit));
      if (!strongLeft) { sharedFail = fails; break; }
      for (const r of queue.filter((x) => !strongSet.has(x.model))) tried.push({ unit: r.unit, state: "skipped (two answers failed the same advance tests)" });
      queue.splice(0, queue.length, ...queue.filter((x) => strongSet.has(x.model)));
      log(`dispatch: two cheap answers failed the same advance tests (${fails}); the strong cheap model still tries`);
    }
    if (fails) seenFails.push(fails);
  }
  clearTimeout(timer);
  if (sharedFail || ambiguous || noSpec) {
    log(noSpec ? "dispatch: no reproduction test could be written for this task (none that fails on the unchanged code); the session's model takes over"
      : ambiguous ? `dispatch: the task reads two ways (${ambiguous}); the session's model takes over`
      : `dispatch: two cheap answers failed the same advance tests (${sharedFail}); the session's model takes over`);
    for (const unit of pending.keys()) {
      const runId = running.get(unit);
      if (runId) try { await settleWithin(rt.cancelRun(runId), SETTLE_MS); } catch { /* best effort */ }
    }
    for (const [unit, p] of pending) {
      const r = await settleWithin(p, SETTLE_MS);         // a cancelled run stuck in a model call is not waited for
      tried.push({ unit, state: "cancelled", run_id: r?.run_id ?? running.get(unit) ?? null, cost_usd: r?.result?.cost_usd ?? null });
    }
    pending.clear();
    for (const rung of queue) tried.push({ unit: rung.unit, state: noSpec ? "skipped (no reproduction test)" : ambiguous ? "skipped (the task reads two ways)"
                                                                   : "skipped (two answers failed the same advance tests)" });
    queue.length = 0;
  }
  // every cheap try finished and none was accepted: this ending had no line of its own (ct-smoke-final4)
  if (!winner && !timedOut && !sharedFail && !ambiguous && !noSpec) {
    log(`dispatch: no cheap answer accepted (${tried.length} tried); the session's model takes over`);
  }
  if (timedOut) {
    // no pass in time: the runs still going are cancelled -- not counted against their models -- and the rest skipped
    log(cands.length
      ? `dispatch: no cheap answer accepted within ${limitS} s (${cands.length} passed the check, not accepted by the rule); the session's model takes over`
      : `dispatch: no cheap pass within ${limitS} s; the session's model takes over`);
    for (const id of extraRuns) try { await settleWithin(rt.cancelRun(id), SETTLE_MS); } catch { /* best effort */ }
    for (const unit of pending.keys()) {
      const runId = running.get(unit);
      if (runId) try { await settleWithin(rt.cancelRun(runId), SETTLE_MS); } catch { /* best effort */ }
    }
    for (const [unit, p] of pending) {
      const r = await settleWithin(p, SETTLE_MS);         // a cancelled run stuck in a model call is not waited for
      tried.push({ unit, state: "timed out", run_id: r?.run_id ?? running.get(unit) ?? null, cost_usd: r?.result?.cost_usd ?? null });
      log(`dispatch ${unit}: no pass (timed out at the limit)`);
    }
    pending.clear();
    for (const rung of queue) tried.push({ unit: rung.unit, state: "skipped (time limit)" });
  }
  specAbort.abort();                       // a test writer still going (no pass, or a time-out) is not needed now
  if (winner) {
    // the others still running: cancelled (not counted against their models), then awaited so their cost is known
    for (const unit of pending.keys()) {
      const runId = running.get(unit);
      if (runId) try { await settleWithin(rt.cancelRun(runId), SETTLE_MS); } catch { /* best effort */ }
    }
    for (const [unit, p] of pending) {
      const r = await settleWithin(p, SETTLE_MS);         // a cancelled run stuck in a model call is not waited for
      tried.push({ unit, state: "cancelled", run_id: r?.run_id ?? running.get(unit) ?? null, cost_usd: r?.result?.cost_usd ?? null });
    }
    const { rung, result } = winner;
    const runId = winner.run_id || result.run_id;
    const files = result.diff?.files || [];
    const costNote = typeof result.cost_usd === "number" ? `, $${result.cost_usd.toFixed(4)}` : "";
    if (policy.ask) {
      // ask mode: show what would change and stop; nothing is written to the user's checkout
      const stat = result.diff?.stat ? `\n${result.diff.stat}\n` : " ";
      return { handled: true, ask: true, run_id: runId, unit: rung.unit, model: rung.model, files, library_entry: null, tried, spec,
        text: `Ready, NOT applied: ${rung.model} solved this and ${checkLabel} passed in an isolated worktree${costNote}. ` +
          `It would change: ${files.join(", ") || "no files"}.${stat}Apply it with: stealthlab-mcp exec apply ${runId}  ` +
          "(dispatch asks first because STEALTHLAB_DISPATCH_ASK=on). That command makes an undo point first: stealthlab-mcp undo puts it back." };
    }
    // the way back first: an undo point for the files the answer will touch (lib/undo.mjs), then the apply, which itself
    // refuses when one of those files was edited in the checkout since the run started
    let repoDir = null;
    let undoPoint = null;
    let undoNote = "";
    if (undoEnabled(env) && files.length) {
      try {
        repoDir = await repoTopLevel(payload.cwd || root, env);
        undoPoint = createUndoPoint({ repo: repoDir, files, model: rung.model, task: firstLine(rawPayload.prompt), env });
      } catch (err) {
        log(`dispatch: no undo point (${err.message})`);
        undoNote = ` (no undo point could be made: ${String(err.message).split("\n")[0].slice(0, 120)})`;
      }
    }
    let applied;
    try {
      applied = await rt.applyRun(runId);
    } catch (err) {
      if (undoPoint) discardUndoPoint(undoPoint, { repo: repoDir, env });
      if (Array.isArray(err.changed) && err.changed.length) {
        log(`dispatch: not applied, ${err.changed.join(", ")} changed during the run`);
        tried.push({ unit: rung.unit, state: `verified, not applied (you edited ${err.changed.join(", ")})`, run_id: runId });
        return { handled: false, tried, spec, remaining: (plan.ladder || []).filter((u) => !tried.some((t) => t.unit === u)),
                 notApplied: { files: err.changed, model: rung.model, run_id: runId } };
      }
      throw err;
    }
    if (undoPoint) finishUndoPoint(undoPoint, { repo: repoDir, env });
    let entry = null;
    let diff = "";
    try {
      const { promptTitle } = await import("./capture_hook.mjs");
      diff = diffFromGit(root);
      if (diff.trim()) {
        entry = addEntry(root, { title: promptTitle(rawPayload.prompt), check: check || "test-first tests", observed: true, diff, steps: wayStepsForEntry(diff) }).id;
        await recordEvidenceFor({ root, entry, check: check || "test-first tests", winner, env, log });
        autoTidy(root, env);                  // an exact duplicate of an earlier entry is dropped, the newer one kept
      }
    } catch (err) {
      log(`dispatch: library entry not added (${err.message})`);
    }
    // the explanation the user reads (lib/explain.mjs) is written WHILE the library bookkeeping below runs, not after it
    const { explainChange } = await import("./explain.mjs");
    const explaining = explainChange({ request: rawPayload.prompt, diff, check: check || "tests written for this task", summary: result.summary, env, fetchImpl, log });
    // a share of the delivered answers is reviewed afterwards, detached, so the router learns how often this kind's
    // checks let a wrong answer through (lib/audit.mjs)
    try {
      const { startAudit } = await import("./audit.mjs");
      startAudit({ instanceKey: plan.instance_key, prompt: rawPayload.prompt, diff, env, log,
                   model: String(env.STEALTHLAB_DISPATCH_TESTFIRST_MODEL || "").trim() || String(mine || "").split("|")[0] });
    } catch (err) {
      log(`dispatch: audit not started (${err.message})`);
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
    // what the user reads: a short explanation by a cheap model (started above), never the session's model
    // the entry in the global ingestion's shape: a general Goal and Way with parameters (lib/way_describe.mjs)
    const [explanation] = await Promise.all([explaining,
      entry ? describeEntry({ root, id: entry, task: rawPayload.prompt, diff, env, fetchImpl, log }) : null]);
    const secs = Math.round((Date.now() - dispatchStart) / 1000);
    recordArea(root, area, true);
    writeDispatchRun(root, { task: rawPayload.prompt, loc, specFile: spec?.file, regressFile: regressIn(), python: pythonBin({ env }), winner: rung.model, tried });
    return { handled: true, run_id: runId, unit: rung.unit, model: rung.model, files: applied.files || [], library_entry: entry, tried, spec, loc,
      explanation,
      text: `Done by ${rung.model} in ${secs} s (StealthLab dispatched it before this session's model ran${cost}). ` +
        `It passed ${checkLabel} in an isolated worktree and the change is applied: ` +
        `${(applied.files || []).join(", ") || "no files"}.` + (entry ? ` Recorded in the library as ${entry}.` : "") +
        (undoPoint ? " To put it back as it was: stealthlab-mcp undo." : undoNote) +
        (explanation ? `\n\n${explanation}` : "") };
  }
  const triedUnits = new Set(tried.map((t) => t.unit));
  // a loss for this area only when the models were really tried: not a task that reads two ways, not usage limits
  if (!ambiguous && tried.some((t) => !/usage_limited|^skipped|^cancelled$/.test(String(t.state)))) recordArea(root, area, false);
  const runMd = writeDispatchRun(root, { task: rawPayload.prompt, loc, specFile: spec?.file, regressFile: regressIn(), python: pythonBin({ env }), tried });
  return { handled: false, tried, spec, loc, runMd, remaining: (plan.ladder || []).filter((u) => !triedUnits.has(u)), ...(sharedFail ? { sharedFail } : {}), ...(ambiguous ? { ambiguous } : {}) };
}

// The image files the request names that exist in the project ("recreate the page in screenshot.png"). The cheap models
// read text only, so such a task is not dispatched -- unless STEALTHLAB_DISPATCH_VISION=on says one of them can see.
const IMAGE_FILE = /(?:^|[\s`'"(])((?:[\w.-]+\/)*[\w.-]+\.(?:png|jpe?g|gif|webp|bmp))(?=$|[\s`'".,;:)!?])/gi;
export function namedImages(prompt, cwd) {
  if (!cwd) return [];
  const root = path.resolve(cwd);
  return [...String(prompt || "").matchAll(IMAGE_FILE)].map((m) => m[1]).filter((name) => {
    const abs = path.resolve(root, name);
    try { return abs.startsWith(root + path.sep) && fs.statSync(abs).isFile(); } catch { return false; }
  });
}

// After a pass (or the time limit) the other runs are cancelled; their final cost is waited for at most SETTLE_MS --
// a run stuck in a slow model call (up to 6 x 60 s of retries) once held a finished answer back for 5 minutes.
const SETTLE_MS = 5000;
function settleWithin(promise, ms) {
  let timer;
  return Promise.race([Promise.resolve(promise).catch(() => null),
                       new Promise((resolve) => { timer = setTimeout(() => resolve(null), ms); })])   // awaited: keeps the process alive, cleared below
    .finally(() => clearTimeout(timer));
}

// The advance (test-first) tests a failed run failed, as one sorted, comma-separated string -- or null when the run did
// not fail on them (it failed the project's own check, stalled, or errored).
export function specFailures(result) {
  if (!result || result.state !== "failed") return null;
  const text = (result.checks || []).map((c) => c.tail || "").join("\n");
  const names = [...text.matchAll(/FAILED [^\s]*test_spec\.py::(\w+)/g)].map((m) => m[1]);
  return names.length ? [...new Set(names)].sort().join(",") : null;
}

// Cross-check (differential testing): a passing cheap answer is accepted only if a DIFFERENT model, solving the same
// task on its own, writes edge-case tests that its own solution passes -- and the first answer passes them too.
// Visible tests cover part of a task; two models that read it differently (n vs frac, a dropped sign, an error the
// task implies) disagree on the cases those tests leave out, and the task goes up the ladder instead of shipping a
// wrong answer. No other model, no usable tests, or a verifier that fails the visible check: "inconclusive", and
// the answer is accepted as before. pytest projects only (the check names pytest).
export const EXTRA_TESTS = "tests_extra/test_extra.py";

function pytestRun(cwd, args, timeoutMs = 300000) {
  const r = spawnSync("python", ["-m", "pytest", "-q", "-p", "no:cacheprovider", ...noCovArgs("python", cwd), ...args],
    { cwd, encoding: "utf8", timeout: timeoutMs, windowsHide: true });
  return { status: r.status, out: `${r.stdout || ""}${r.stderr || ""}` };
}

export async function crossCheck({ rt, rung, result, rungs, env, payload, root, check, scope, log = () => {}, onStart = () => {} }) {
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
    onStart(started.run_id);
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
  if (d?.notApplied) {
    return `StealthLab: ${d.notApplied.model} solved this and its check passed, but the change was NOT applied: you edited ` +
      `${d.notApplied.files.join(", ")} while it ran. Do the task yourself, starting from your current files; there is nothing to report.`;
  }
  if (!d || d.handled) return "";
  if (d.partial) {
    // the task was planned in parts from a similar fix made here; some were done by cheap models
    const done = d.partial.nodes.filter((n) => n.status === "done");
    const yours = d.partial.nodes.filter((n) => n.status === "ready");
    const waiting = d.partial.nodes.filter((n) => n.status === "waiting");
    const list = (ns) => ns.map((n) => `${n.id} (${n.at.join(", ")})`).join(", ");
    return `StealthLab split this task into ${d.partial.nodes.length} parts, ` +
      (d.partial.entry === "request" ? "one per thing the request asks for. " : `planned from a similar fix already made here (${d.partial.entry}). `) +
      (done.length && d.partial.applied.length
        ? `Done by cheap models, checked (no new test failures) and applied: ${list(done)}${d.partial.undone ? "; stealthlab-mcp undo removes them" : ""}. `
        : "No part was applied. ") +
      `Yours: ${list(yours)}.` +
      (waiting.length && d.partial.resumable
        ? ` Do only your part; then run \`${selfCommand()} dag continue\`: it checks your part, has cheap models do ${list(waiting)}, ` +
          "and runs the full check. Finish whatever it reports as still yours."
        : " Finish the task from the code as it is now and check the whole of it.") +
      (d.runMd ? " The plan is in .stealth/run.md." : "") + handoffNote({ ...d, runMd: false });
  }
  if (!d.tried?.length) return "";
  // an answer that passed the check but was not accepted is not "nothing": the session gets it to judge, instead of
  // being told none passed and redoing the work (sqlglot-4051: glm-5.3-flash passed, a review said it fixes, and the
  // session was told "none passed", ct-series-v4)
  const passed = d.tried.filter((t) => /^passed the check/.test(String(t.state)) && t.patch);
  if (passed.length) {
    const p = passed[0];
    return `StealthLab first tried this on ${d.tried.map((t) => t.unit.split("|")[0]).join(", ")}. ${p.unit.split("|")[0]}'s answer ` +
      `passed the project's check but was not applied: the acceptance rule did not accept it ` +
      `(evidence: ${String(p.state).replace(/^passed the check, not accepted by the rule \(|\)$/g, "").slice(0, 300)}). Its change is in ${p.patch}. Read it first: ` +
      "if it does what the task asks, apply it (`git apply` that file), run the tests and finish from there; if not, do the " +
      "task yourself. There is nothing to report." + handoffNote(d);
  }
  return "StealthLab first tried this on " + d.tried.map((t) => `${t.unit.split("|")[0]} (${t.state})`).join(", ") +
    "; none passed the check and nothing was applied. Do the task yourself; there is nothing to report." +
    (d.sharedFail ? ` Two of those answers failed the same tests written in advance (${d.sharedFail}); those tests may ` +
                    "themselves be wrong, so judge the task from its own words." : "") +
    (d.ambiguous ? ` The task may be read two ways (${d.ambiguous}); settle which one is meant before answering.` : "") +
    handoffNote(d);
}

// this StealthLab's own command line, runnable from the session's shell whether or not stealthlab-mcp is on its PATH
export function selfCommand() {
  const bin = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");
  return `node "${bin}"`;
}

// What the cheap tries found that saves the session's model its own search: where the task likely is, and tests written
// for it that fail on the unchanged code (not when two answers failed them alike: then they may be wrong).
function handoffNote(d) {
  const spots = (d.loc?.spots || []).slice(0, 3).map((s) => `${s.file}:${s.start}-${s.end}${s.symbol ? ` (${s.symbol})` : ""}`);
  const parts = [];
  if (spots.length) parts.push(`Searching this repository for the task's names points at ${spots.join(", ")}; confirm it before relying on it.`);
  if (d.spec?.file && !d.sharedFail && !d.ambiguous) parts.push(`Tests written for this task, which fail on the unchanged code, are at ${d.spec.file}: ` +
    "read them and run them to check your change, but judge the task from its own words where they disagree.");
  if (parts.length && d.runMd) parts.push("The plan is in .stealth/run.md (node N-3 is yours).");
  return parts.length ? ` ${parts.join(" ")}` : "";
}

