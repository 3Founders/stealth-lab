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
import { readExecConfig } from "./exec/store.mjs";
import { claudeChildEnv, launchOrThrow, scrubEnv } from "./executors/common.mjs";
import { addEntry, diffFromGit, ensureRoute, readEntry, recordObs } from "./library.mjs";
import { executorFor } from "./model_guard.mjs";
import { autoTidy } from "./library_tidy.mjs";
import { profileKey } from "./executors/profile.mjs";
import { capFloorS, capMult, fastestFirstEnabled, modelCapEnabled, modelCapS, modelSpeeds, modelWins, orderFastestFirst, recordModelWin } from "./model_speed.mjs";
import { detectCheck, pythonBin } from "./detect_check.mjs";
import { repoContext } from "./task_context.mjs";
import { repoTopLevel } from "./exec/worktree.mjs";
import { createUndoPoint, discardUndoPoint, finishUndoPoint, undoEnabled } from "./undo.mjs";

const TERMINAL = new Set(["verified", "failed", "timed_out", "cancelled"]);
const TASK_MAX = 2000;

export function dispatchPolicy(env = process.env) {
  return {
    enabled: (env.STEALTHLAB_DISPATCH || "on").toLowerCase() !== "off",
    check: (env.STEALTHLAB_DISPATCH_CHECK || "").trim() || null,
    // find the project's own test command (lib/detect_check.mjs) when none is configured and no library entry has one;
    // "off" restores "no configured check, no dispatch"
    detect: (env.STEALTHLAB_DISPATCH_DETECT || "on").toLowerCase() !== "off",
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
    // the time limit on all the cheap tries together, from their start: past it the session's model takes over
    timeoutS: Math.max(10, Number(env.STEALTHLAB_DISPATCH_TIMEOUT_S || 150)),
    // how many cheap runs at once: three agents on one provider can exceed its tokens-per-minute limit
    maxParallel: Math.max(1, Number(env.STEALTHLAB_DISPATCH_MAX_PARALLEL || 2)),
    // when the plan puts the session's model first, still try the cheap models this often (a skipped kind recovers)
    explore: Math.min(1, Math.max(0, Number(env.STEALTHLAB_DISPATCH_EXPLORE ?? 0.2))),
    // a provider with fewer tokens left today than this is not tried: a big task's cheap tries take ~100k-500k
    minQuotaTokens: Math.max(0, Number(env.STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS ?? 300000)),
    // "on": a verified cheap answer is NOT applied: the prompt is stopped with a summary of the change and the command that
    // applies it (`stealthlab-mcp exec apply <run>`). Default off: it is applied, with an undo point (lib/undo.mjs)
    ask: (env.STEALTHLAB_DISPATCH_ASK || "off").toLowerCase() === "on",
    // per-MODEL timing learned from this repo's own cheap wins (lib/model_speed.mjs): a run is capped at 3 x its model's p90 win time (not
    // below 60 s, not above the limit above; fewer than 5 wins: no cap), a capped run is cancelled and is not a failure; and the rungs start
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
    // extra seconds for the cheap tries when a strong cheap model (exec.json profile strong: true) is among them
    strongExtraS: Math.max(0, Number(env.STEALTHLAB_DISPATCH_STRONG_EXTRA_S ?? 240)),
    strongFirst: (env.STEALTHLAB_DISPATCH_STRONG_FIRST || "off").toLowerCase() === "on",
    // no test command in the project: the tests written for the task are the check (needs the test writer); "off": no dispatch
    specOnly: (env.STEALTHLAB_DISPATCH_SPEC_ONLY || "on").toLowerCase() !== "off",
    // "on": a cheap model can read images, so a task built on a screenshot may be dispatched (default off: text-only models)
    vision: (env.STEALTHLAB_DISPATCH_VISION || "off").toLowerCase() === "on",
    // the task brief (lib/brief.mjs): a cheap call restates the prompt with the earlier turns, and only a clear, small or
    // medium code task is dispatched; the cheap models and the test writer get the brief, then the user's original request. "off": the raw prompt, as before
    brief: (env.STEALTHLAB_BRIEF || "on").toLowerCase() !== "off",
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
  "web page when the project uses one), and assert on what it outputs. (7) When the project's own check already",
  "verifies a contract or schema exhaustively (property-based fuzzing, a schema validator, a type checker), do not",
  "re-test what it covers: test only behaviour the task asks for beyond it, and never assume what the contract",
  "leaves open (which route serves an ambiguous path, field values the schema allows freely). (8) If the task, read",
  "with its files, can reasonably be read two ways that give DIFFERENT correct results, and nothing in the task or the",
  "files settles which, make the first line of the file `# AMBIGUOUS: <the two readings, one short sentence>` and still",
  "write the tests for the likelier reading. Do not flag details the tests can leave open, or readings only a careless",
  "reader would take; most tasks are not ambiguous. Output only code.",
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
sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f]))
`;

// strict: the tests written for the task are the ONLY check (the project has no test command), so no tests = fail
export function writeSpecRunner(dir, { strict = false } = {}) {
  const file = path.join(dir, "run_spec.py");
  fs.writeFileSync(file, strict ? RUN_SPEC.replace("if not os.path.exists(f):\n    sys.exit(0)", "if not os.path.exists(f):\n    sys.exit(1)") : RUN_SPEC);
  return file;
}

function runClaudeAsync(env, args, input, signal) {
  return new Promise((resolve) => {
    let launch;
    try { launch = launchOrThrow("claude", { env }); } catch (err) { resolve({ status: null, stdout: "", stderr: err.message }); return; }
    const child = spawn(launch.cmd, [...launch.prefix, ...args], { windowsHide: true, env: claudeChildEnv(env) });
    let stdout = "", stderr = "";
    const timer = setTimeout(() => child.kill(), 180000);
    const abort = () => child.kill();
    signal?.addEventListener?.("abort", abort);
    child.stdout.on("data", (d) => { stdout += d; });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("error", (err) => { stderr += err.message; });
    child.on("close", (status) => { clearTimeout(timer); signal?.removeEventListener?.("abort", abort); resolve({ status, stdout, stderr }); });
    child.stdin.end(input);
  });
}

// -> {file, cost_usd} (a pytest file outside the repo, run from the worktree) or null. One lean, tool-less call to
// a Claude model: a short system prompt, no tools, no project settings. Async: dispatch starts the cheap models at
// the same time, and their check waits for the file (run_spec.py). `dir`: where to write (specDir() when omitted);
// the file appears only once it is complete and parses, and SKIP appears instead when no tests could be written.
export async function writeSpecTests({ prompt, root, mine, check = "", env = process.env, log = () => {}, runClaude,
                                       dir = null, signal = null }) {
  const out = dir || specDir(env);
  const skip = () => { try { fs.writeFileSync(path.join(out, "SKIP"), ""); } catch { /* best effort */ } return null; };
  // the writer: the session's model, or STEALTHLAB_DISPATCH_TESTFIRST_MODEL -- a Claude model (through Claude Code), or
  // an open model with an exec.json profile (GLM, say: called directly on its OpenAI-compatible endpoint)
  const model = String(env.STEALTHLAB_DISPATCH_TESTFIRST_MODEL || "").trim() || String(mine || "").split("|")[0];
  const openProfile = model.includes("claude") ? null : openWriterProfile(env, model);
  if (!model.includes("claude") && !openProfile) return skip();
  const ask = `Task:\n${String(prompt || "").trim()}\n\nProject files:\n${specContext(root)}\n\n` +
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
  const claudeRun = runClaude || ((a, input) => runClaudeAsync(env, a, input, signal));
  const run = openProfile && !runClaude ? (_args, input) => runOpenWriter(openProfile, env, input, signal, openTimeoutS) : claudeRun;
  const argsFor = (m) => ["-p", "--model", m, "--system-prompt", SPEC_SYSTEM,
    "--tools", "", "--setting-sources", "user", "--strict-mcp-config", "--output-format", "json"];
  const args = argsFor(model);
  let usedModel = model;
  // one retry: a failed call (a transient error, an empty reply) would leave every cheap answer unchecked by tests
  let j = null;
  let code = "";
  for (let attempt = 1; attempt <= 2 && !code && !signal?.aborted; attempt++) {
    if (attempt === 2 && fallback) { usedModel = fallback; log(`test-first: ${model} gave no usable tests; ${fallback} writes them`); }
    const r = attempt === 2 && fallback ? await claudeRun(argsFor(fallback), ask) : await run(args, ask);
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
    fs.writeFileSync(probe, "import os, sys\nsys.path.insert(0, os.getcwd())\n\n" + c + "\n");
    const bad = pythonSyntaxError(probe);
    try { fs.unlinkSync(probe); } catch { /* */ }
    if (bad && attempt < 2) { log(`test-first: the tests do not parse (${bad}); asking again`); continue; }
    code = c;
  }
  if (!code) return skip();
  const file = path.join(out, "test_spec.py");
  const tmp = path.join(out, "test_spec.py.part");
  // run from the worktree: the project's modules import from the current directory
  fs.writeFileSync(tmp, "import os, sys\nsys.path.insert(0, os.getcwd())\n\n" + code + "\n");
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
      if (executor === "claude") continue;
      for (const m of (spec?.models || []).map(String)) {
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

function taskText(prompt) {
  const t = String(prompt || "").trim();
  return t.length <= TASK_MAX ? t : `${t.slice(0, TASK_MAX - 40)}\n[...prompt truncated for the executor]`;
}

// -> {handled: true, text} when a cheap rung's verified change was applied; else {handled: false, tried, remaining}
//    (remaining: the plan's ladder after the rungs tried here), or null when dispatch does not apply.
// force: the prompt began with `!cheap` -- try the cheap models even where routing would skip, and skip the brief's gate
// briefImpl: for tests, a stand-in for brief.mjs makeBrief()
export async function dispatch({ payload: rawPayload, reply, root, mine, env = process.env, fetchImpl, log = () => {}, runtime, runClaude,
                                 force = false, briefImpl }) {
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
  const strongFirstSet = (() => { try { const ps = readExecConfig(env).profiles || {}; return new Set([...strongSet].filter((k) => ps[k]?.first === true)); } catch { return new Set(); } })();
  let rungs = await withQuota(cheapCandidates(plan, mine, env, { max: seeOnly ? 16 : policy.maxRungs + strongSet.size, claude: policy.claude,
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
    const weak = rungs.filter((r) => !strongSet.has(r.model)).slice(0, seeOnly ? 16 : policy.maxRungs);
    const strong = rungs.filter((r) => strongSet.has(r.model));
    const first = policy.strongFirst ? strong : strong.filter((r) => strongFirstSet.has(r.model));
    rungs = [...first, ...weak, ...strong.filter((r) => !first.includes(r))];
  }
  if (images.length && !policy.vision) {
    const profiles = (() => { try { return readExecConfig(env).profiles || {}; } catch { return {}; } })();
    rungs = rungs.filter((r) => profiles[r.model]?.vision === true).slice(0, policy.maxRungs);
    if (!rungs.length) {
      log(`dispatch: not dispatched (the task needs an image no cheap model here can see: ${images.join(", ")})`);
      return null;
    }
  }
  // the brief: what the cheap models and the test writer are told, and whether this prompt is a task for them at all
  if (policy.brief) {
    const { briefGate, briefedPrompt, makeBrief } = await import("./brief.mjs");
    const brief = await (briefImpl || makeBrief)({ prompt: payload.prompt, transcriptPath: payload.transcript_path,
                                                  cwd: payload.cwd || root, env, fetchImpl, log });
    const gate = briefGate(brief, payload.prompt);
    // a large task: only the strong cheap model tries it (the small ones rarely manage); none -> the session's model
    if (!gate.ok && !force && gate.reason === "size=large" && rungs.some((r) => strongSet.has(r.model))) {
      rungs = rungs.filter((r) => strongSet.has(r.model));
      log(`dispatch: a large task -- only ${rungs.map((r) => r.model).join(", ")} tries it`);
    } else if (!gate.ok && !force) { log(`dispatch: not dispatched (${gate.reason})`); return null; }
    // the brief first, then the user's own words ("Original request:"): a detail the brief dropped is still there
    if (brief) payload = { ...payload, prompt: briefedPrompt(brief, payload.prompt, TASK_MAX) };
  }
  const rt = runtime || new (await import("./exec/runtime.mjs")).ExecRuntime({ env, fetchImpl });
  const tried = [];
  // test-first: tests from the task join the project's check for every cheap rung. They are written WHILE the cheap
  // models work (a cheap model takes ~50 s to reach its first check, the tests ~20 s): the check's last step waits
  // for them. Whatever the project's check is (pytest, vitest, a SQL or page check), the tests are a pytest file.
  let spec = null;
  let specDone = Promise.resolve(null);
  let fullCheck = check;
  const specAbort = new AbortController();
  if (policy.testFirst && (runClaude || pytestAvailable())) {
    const dir = specDir(env);
    fullCheck = specOnly ? `${pythonBin({ env })} "${writeSpecRunner(dir, { strict: true })}"`
      : `${check} && ${pythonBin({ env })} "${writeSpecRunner(dir)}"`;
    specDone = writeSpecTests({ prompt: payload.prompt, root: payload.cwd || root, mine, check, env, log, runClaude, dir,
                                signal: specAbort.signal })
      .then((s) => { spec = s; return s; })
      .catch((err) => { log(`test-first: ${err.message}`); try { fs.writeFileSync(path.join(dir, "SKIP"), ""); } catch { /* */ } return null; });
  }

  // the repo's own facts and the matched library entries, after the task (lib/task_context.mjs; STEALTHLAB_DISPATCH_CONTEXT=off)
  const ctx = repoContext({ reply, root, cwd: payload.cwd || root, prompt: payload.prompt, env });
  const taskWithContext = fitTask(taskText(payload.prompt), ctx);
  // the limit on all the cheap tries together, and the ceiling of any one model's own cap: 150 s plus an allowance for the task's material
  const baseLimitS = policy.timeoutS + taskAllowanceS(payload.cwd || root);
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
        ...(plan.instance_key ? { instance_key: plan.instance_key } : {}) });
      runId = started.run_id;
      running.set(rung.unit, runId);
      startedAt = Date.now();
      // this MODEL's own cap (from its wins in this repo): past it the run is cancelled, which is not a verdict on the model
      const cap = policy.modelCap ? modelCapS(modelWins(root, rung.model, rung.executor)?.wins, { baseS: baseLimitS, mult: policy.capMult, floorS: policy.capFloorS }) : null;
      if (cap !== null) {
        capS = cap;
        capTimer = setTimeout(() => { capped = true; log(`dispatch: ${rung.model} capped at ${Math.round(cap * 10) / 10} s`); rt.cancelRun(runId).catch(() => {}); }, cap * 1000);
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
  const judge = async ({ rung, result, error, startedAt, capped, capS }) => {
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
      return false;
    }
    if (result.state === "cancelled") {    // stopped from outside: no verdict on the model either
      // by its own model's cap (slower than its wins here usually are): not a failure of the model either
      tried.push({ unit: rung.unit, state: capped ? `capped at ${Math.round(capS * 10) / 10} s` : "cancelled", run_id: result.run_id, cost_usd: result.cost_usd });
      return false;
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
    return ok;
  };

  const queue = [...rungs];
  const pending = new Map();               // unit -> promise of its finished run
  const width = policy.parallel ? Math.min(rungs.length, policy.maxParallel) : 1;
  let winner = null;
  // the time limit on the cheap tries: past it the session's model takes over (a pass takes ~50 s, p90 ~60 s)
  // ... plus an allowance for the material the task comes with: an agent reads a 20 KB contract many times over
  // the strong model works more slowly (more context, more steps): its run gets extra time
  const limitS = baseLimitS + (rungs.some((r) => strongSet.has(r.model)) ? policy.strongExtraS : 0);
  let timer;
  const deadline = new Promise((resolve) => { timer = setTimeout(() => resolve({ timeout: true }), limitS * 1000); });
  timer.unref?.();
  let timedOut = false;
  const extraRuns = new Set();             // cross-check runs: cancelled with the rest when the time is up
  const seenFails = [];                    // the failing advance tests of each failed rung, as a sorted list
  let sharedFail = null;
  let ambiguous = null;                    // the test writer's note that the task reads two ways (stops the cheap tries)
  const ambiguityKnown = specDone.then((s) => (s?.ambiguous && policy.ambiguityStop ? { ambiguityFlag: s.ambiguous } : new Promise(() => {})));
  while (!winner && (queue.length || pending.size)) {
    while (queue.length && pending.size < width) {
      const rung = queue.shift();
      pending.set(rung.unit, runRung(rung));
    }
    // the test writer's ambiguity flag (~20 s in) ends the cheap tries at once, not when the first one finishes
    const done = await Promise.race([...pending.values(), deadline, ambiguityKnown]);
    if (done.ambiguityFlag) { ambiguous = done.ambiguityFlag; break; }
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
    if (verdict) { winner = done; break; }
    if (ambiguous) break;
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
  if (sharedFail || ambiguous) {
    log(ambiguous ? `dispatch: the task reads two ways (${ambiguous}); the session's model takes over`
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
    for (const rung of queue) tried.push({ unit: rung.unit, state: ambiguous ? "skipped (the task reads two ways)"
                                                                   : "skipped (two answers failed the same advance tests)" });
    queue.length = 0;
  }
  if (timedOut) {
    // no pass in time: the runs still going are cancelled -- not counted against their models -- and the rest skipped
    log(`dispatch: no cheap pass within ${limitS} s; the session's model takes over`);
    for (const id of extraRuns) try { await settleWithin(rt.cancelRun(id), SETTLE_MS); } catch { /* best effort */ }
    for (const unit of pending.keys()) {
      const runId = running.get(unit);
      if (runId) try { await settleWithin(rt.cancelRun(runId), SETTLE_MS); } catch { /* best effort */ }
    }
    for (const [unit, p] of pending) {
      const r = await settleWithin(p, SETTLE_MS);         // a cancelled run stuck in a model call is not waited for
      tried.push({ unit, state: "timed out", run_id: r?.run_id ?? running.get(unit) ?? null, cost_usd: r?.result?.cost_usd ?? null });
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
        entry = addEntry(root, { title: promptTitle(rawPayload.prompt), check: check || "test-first tests", observed: true, diff }).id;
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
    const explanation = await explaining;
    const secs = Math.round((Date.now() - dispatchStart) / 1000);
    return { handled: true, run_id: runId, unit: rung.unit, model: rung.model, files: applied.files || [], library_entry: entry, tried, spec,
      explanation,
      text: `Done by ${rung.model} in ${secs} s (StealthLab dispatched it before this session's model ran${cost}). ` +
        `It passed ${checkLabel} in an isolated worktree and the change is applied: ` +
        `${(applied.files || []).join(", ") || "no files"}.` + (entry ? ` Recorded in the library as ${entry}.` : "") +
        (undoPoint ? " To put it back as it was: stealthlab-mcp undo." : undoNote) +
        (explanation ? `\n\n${explanation}` : "") };
  }
  const triedUnits = new Set(tried.map((t) => t.unit));
  return { handled: false, tried, spec, remaining: (plan.ladder || []).filter((u) => !triedUnits.has(u)), ...(sharedFail ? { sharedFail } : {}), ...(ambiguous ? { ambiguous } : {}) };
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
                       new Promise((resolve) => { timer = setTimeout(() => resolve(null), ms); timer.unref?.(); })])
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
  const r = spawnSync("python", ["-m", "pytest", "-q", "-p", "no:cacheprovider", ...args],
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
  if (!d || d.handled || !d.tried?.length) return "";
  return "StealthLab first tried this on " + d.tried.map((t) => `${t.unit.split("|")[0]} (${t.state})`).join(", ") +
    "; none passed the check and nothing was applied. Do the task yourself; there is nothing to report." +
    (d.sharedFail ? ` Two of those answers failed the same tests written in advance (${d.sharedFail}); those tests may ` +
                    "themselves be wrong, so judge the task from its own words." : "") +
    (d.ambiguous ? ` The task may be read two ways (${d.ambiguous}); settle which one is meant before answering.` : "");
}

