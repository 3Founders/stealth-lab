// Tests written by an agent that can RUN them: a cheap model, in a worktree of the project, writes a pytest file that
// reproduces the reported problem and must see it fail on the current code -- by assertion, not by an import or setup
// error -- before it is accepted (the executor's check enforces exactly that). It may not change any other file.
//
// Why: one tool-less call wrote tests that were valid (fail before, pass with the real fix) for 41% of 32 real issues;
// the rest asserted behaviour the real fix does not have, imported helpers that do not exist, or did not run at all
// (pipeline survey stage 2). Published reproduction agents that run their tests reach ~50-65% (Agentless, e-Otter++).
// The one-call writer stays as the fallback. STEALTHLAB_DISPATCH_TESTFIRST_AGENT=on (measured before it is the default).
// On 22 issues the agent got a failing test 4 times (10 times its test never failed by assertion, 8 times it ran out of
// time). Showing the writer the agent's rejected test, with 360 s for the agent, did not help: 1 more valid set of tests
// in 21, 2 lost in 3, and ~400 s per issue (survey stage 2, 2026-10-16) -- so that is opt-in,
// STEALTHLAB_DISPATCH_TESTFIRST_AGENT_HANDOFF=on.
import fs from "node:fs";
import path from "node:path";

export const REPRO_FILE = "test_stealthlab_repro.py";
export const reproAgentEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_TESTFIRST_AGENT || "off").toLowerCase() === "on";

// passes (exit 0) only when the test file exists, is collected, and fails by assertion (pytest exit 1, no errors)
const CHECK_PY = `import glob, os, re, subprocess, sys
fs = [f for f in glob.glob("**/${REPRO_FILE}", recursive=True) if "/." not in f.replace(os.sep, "/")]
if not fs:
    print("no ${REPRO_FILE} was written"); sys.exit(1)
nocov = ["--no-cov"] if __import__("importlib.util").util.find_spec("pytest_cov") else []
p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nocov, fs[0]], capture_output=True, text=True)
out = p.stdout + p.stderr
print(out[-3000:])
if p.returncode == 1 and not re.search(r"errors? during collection|^ERROR ", out, re.M):
    print("OK: the test fails on the current code, by assertion"); sys.exit(0)
if p.returncode == 0:
    print("NOT OK: the test PASSES on the current code, so it does not reproduce the problem"); sys.exit(1)
print("NOT OK: the test could not run (an import or setup error) -- it must run and fail by assertion"); sys.exit(1)
`;

export function reproTask(prompt, testDir) {
  return [
    `Write ONE pytest file, ${testDir ? `${testDir}/` : ""}${REPRO_FILE}, that reproduces the problem below. Do NOT fix the`,
    "problem and do not change any other file. The test must fail on the code as it is now, with an assertion that shows",
    "the reported behaviour, and pass once the problem is fixed. Use the report's own example when it gives one; import only",
    "names that exist now. Run it (python -m pytest -q <file>) and make sure it fails by assertion, not by an error.",
    "",
    "The report:",
    String(prompt || "").slice(0, 1400),
  ].join("\n");
}

// a failed or late run: the test it wrote (the new file in its patch) and the check's verdict, for the writer to do better
export function lastTry(r) {
  let patch = "";
  try { patch = r?.diff?.patch_path ? fs.readFileSync(r.diff.patch_path, "utf8") : ""; } catch { /* */ }
  const lines = patch.split(/\r?\n/);
  const at = lines.findIndex((l) => l.startsWith("+++ ") && l.endsWith(REPRO_FILE));
  if (at < 0) return null;
  const body = [];
  for (const l of lines.slice(at + 1)) {
    if (l.startsWith("diff --git ")) break;
    if (l.startsWith("+")) body.push(l.slice(1));
  }
  const tried = body.join("\n").trim();
  if (!/def test_/.test(tried)) return null;
  const tail = String(r?.checks?.[0]?.tail || "");
  const errs = tail.split(/\r?\n/).filter((l) => /Error/.test(l)).slice(-2).join(" | ").slice(0, 300);
  const why = /PASSES on the current code/.test(tail) ? "it PASSED on the current code, so it did not show the problem"
    : /could not run/.test(tail) ? `it could not run (an import or setup error)${errs ? `: ${errs}` : ""}`
    : r?.state === "timed_out" ? "the agent ran out of time before it was checked" : "it was not shown to fail by assertion";
  return { tried: tried.slice(0, 2500), why };
}

// -> {code} (a test that fails on the current code by assertion), {tried, why} (its last test and the check's verdict on
// it), or null. rt: the exec runtime; root: the project; dir: where the check script is written.
export async function reproduceTests({ rt, root, prompt, where = "", env = process.env, log = () => {}, signal = null, dir }) {
  const model = String(env.STEALTHLAB_DISPATCH_TESTFIRST_AGENT_MODEL || "gpt-oss-120b").trim();
  const executor = String(env.STEALTHLAB_DISPATCH_TESTFIRST_AGENT_EXECUTOR || "stealth").trim();
  const testDir = ["tests", "test", "testing"].find((t) => { try { return fs.statSync(path.join(root, t)).isDirectory(); } catch { return false; } }) || "";
  const check = path.join(dir, "repro_check.py");
  fs.writeFileSync(check, CHECK_PY);
  let runId = null;
  const abort = () => { if (runId) rt.cancelRun(runId).catch(() => {}); };
  signal?.addEventListener?.("abort", abort);
  try {
    const task = `${reproTask(prompt, testDir)}${where ? `\n\n${where}` : ""}`.slice(0, 2000);
    const st = await rt.achieve({ repo_path: root, task, checks: [`python "${check}"`], scope: [`**/${REPRO_FILE}`, REPRO_FILE],
      executor, model, base: "working-tree", timeout_s: Number(env.STEALTHLAB_DISPATCH_TESTFIRST_AGENT_TIMEOUT_S || 240) });
    runId = st.run_id;
    let r;
    do { r = await rt.runResult(runId, 55); } while (!["verified", "failed", "timed_out", "cancelled", "error"].includes(r?.state));
    if (r.state !== "verified") { log(`test-first: the reproduction agent did not get a failing test (${r.state})`); return (env.STEALTHLAB_DISPATCH_TESTFIRST_AGENT_HANDOFF || "off").toLowerCase() === "on" ? lastTry(r) : null; }
    const wt = r.diff?.worktree;
    const f = (r.diff?.files || []).find((x) => x.endsWith(REPRO_FILE));
    const code = wt && f ? fs.readFileSync(path.join(wt, f), "utf8") : null;
    if (code) log(`test-first: ${model} reproduced the problem in a test that fails on the current code`);
    return code ? { code } : null;
  } catch (err) {
    log(`test-first: reproduction agent: ${err.message}`);
    return null;
  } finally {
    signal?.removeEventListener?.("abort", abort);
    if (runId) rt.cancelRun(runId).catch(() => {});
  }
}
