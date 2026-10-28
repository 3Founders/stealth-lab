// Accepting a cheap model's answer on evidence, not on one veto (STEALTHLAB_DISPATCH_ACCEPT=<rule>; "check", the default,
// keeps the old behaviour: the check, our written tests included, decides alone).
//
// Why: our written tests guess the interface or the behaviour about half the time, so as the only judge they accepted
// 11 cheap answers of which 2 were right and rejected 7 right ones (pipeline survey stage 3, 2026-10-16). The rules and
// their signals were fixed before they were measured: docs/acceptance_rules_preregistered_2026-10-16.md.
//
// A rule: signals that must all hold, separated by spaces; "A|R" is either; "!Rno" is not. Signals:
//   E      the answer passes the project's own check, judged on NEW failures (every candidate here has passed it)
//   T      our written tests pass on it              T*  T, or no candidate passes them (then they are no evidence)
//   S      it changes source code, not only tests/docs/config
//   F      focused: at most 3 source files and 60 changed lines
//   X      it adds no catch-all exception swallow
//   A      another candidate answer changed the same source files
//   C      it passes another candidate's VALIDATED tests (tests that answer added, failing without its source change and
//          passing with it) -- the two behave alike (crossTest; the strongest oracle-free signal in the literature)
//   R/Rno  the cheap reviewer says it fixes / does not fix the request     (STEALTHLAB_REVIEW_MODEL, gemma-4-31b-it)
//   Rs/Rs_no  the strong reviewer says so                                  (STEALTHLAB_REVIEW_STRONG_MODEL, claude-sonnet-5-5)
// In this mode the cheap agents work against the project's check only, never our written tests: refining code until
// generated tests pass raises false accepts to ~60% (Ahmed et al., FSE 2026). Our tests and the reviewers judge the
// finished answer. Only the signals the rule names are computed (no tests are written for a rule without T).
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { changesSource, reviewPatch } from "./review.mjs";
import { depsEnv } from "./exec/deps.mjs";

export const SIGNALS = ["E", "T", "T*", "S", "F", "X", "A", "C", "R", "Rno", "Rs", "Rs_no"];

// -> [[{sig, neg}]] (an AND of OR-groups) | null when the text is not a rule (unknown signal) or is "check"
export function parseRule(text) {
  const t = String(text || "").trim();
  if (!t || t.toLowerCase() === "check") return null;
  const groups = t.split(/\s+/).map((g) => g.split("|").map((x) => {
    const neg = x.startsWith("!");
    return { sig: neg ? x.slice(1) : x, neg };
  }));
  return groups.every((g) => g.every((x) => SIGNALS.includes(x.sig))) ? groups : null;
}
export const ruleUses = (rule, ...sigs) => !!rule && rule.some((g) => g.some((x) => sigs.includes(x.sig)));
export const evalRule = (rule, s) => rule.every((g) => g.some((x) => (x.neg ? !s[x.sig] : !!s[x.sig])));

const filesOf = (patch) => [...String(patch || "").matchAll(/^diff --git a\/\S+ b\/(\S+)/gm)].map((m) => m[1]);
const SWALLOW = /^\+\s*except(\s+(Exception|BaseException)(\s+as\s+\w+)?)?\s*:\s*(pass|continue|return\b.*)?\s*$/;
const BODY = /^\+\s*(pass|continue|return\b.*)\s*$/;

// S, F, X from the patch alone (the same definitions as experiments/abtest/acceptance_eval.py)
export function patchSignals(patch) {
  const files = filesOf(patch);
  const src = files.filter((f) => changesSource([f]));
  const lines = String(patch || "").split(/\r?\n/);
  const changed = lines.filter((l) => /^[+-]/.test(l) && !/^(\+\+\+|---)/.test(l)).length;
  const added = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++"));
  const swallow = added.some((l, i) => { const m = SWALLOW.exec(l); return m && (m[4] || (i + 1 < added.length && BODY.test(added[i + 1]))); });
  return { S: changesSource(files), F: src.length <= 3 && changed <= 60, X: !swallow, src: [...new Set(src)].sort() };
}

// Our tests run in the answer's worktree with the environment its check had (lib/exec/runtime.mjs: depsEnv) -- the
// project's venv first on PATH, the worktree's own code first on PYTHONPATH. Without it the installed copy of the project
// (an editable install points at the user's checkout) can be what the tests import, and they test the unchanged code:
// ct-series-v5, jsonargparse: 4 answers the hidden tests grade correct, our tests failed on all 4. No bytecode is
// written (testEnv): a file changed and run again within the same second can otherwise load the stale .pyc of the
// version before (crossTest takes a change out and puts it back). -> {code, out}
const testEnv = (cwd, env) => ({ ...depsEnv(cwd, { ...process.env, ...env }), PYTHONDONTWRITEBYTECODE: "1" });

function runSpecIn(python, runner, cwd, env, signal) {
  return new Promise((resolve) => {
    let out = "";
    const c = spawn(python, [runner], { cwd, env: testEnv(cwd, env), windowsHide: true });
    const t = setTimeout(() => c.kill(), 300000);
    signal?.addEventListener?.("abort", () => c.kill());
    c.stdout.on("data", (d) => { out = (out + d).slice(-20000); });
    c.stderr.on("data", (d) => { out = (out + d).slice(-20000); });
    c.on("error", (err) => { clearTimeout(t); resolve({ code: null, out: err.message }); });
    c.on("close", (code) => { clearTimeout(t); resolve({ code, out }); });
  });
}

// the lines of a pytest run that say what happened: its failures and errors, and the summary line
export function pytestGist(out) {
  const lines = String(out || "").split(/\r?\n/).filter((l) => l.trim());
  const keep = lines.filter((l) => /^(FAILED|ERROR) |^E\s|Error:|^=+ .*(passed|failed|error)/.test(l));
  return (keep.length ? keep : lines).slice(-8).join(" | ").slice(0, 600);
}

// the evidence on one answer that passed the project's check -> signals (only those the rule needs) + its patch
export async function answerEvidence({ rule, result, spec, specRunner, python = "python", prompt, env = process.env, log = () => {},
                                       signal = null, fetchImpl, review = reviewPatch, unit = "" }) {
  let patch = "";
  try { patch = result?.diff?.patch_path ? fs.readFileSync(result.diff.patch_path, "utf8") : ""; } catch { /* no patch read */ }
  const ev = { E: true, ...patchSignals(patch), T: false, R: false, Rno: false, Rs: false, Rs_no: false, specKnown: false };
  const jobs = [];
  if (ruleUses(rule, "T", "T*") && spec?.file && specRunner && result?.diff?.worktree) {
    jobs.push(runSpecIn(python, specRunner, result.diff.worktree, env, signal).then(({ code, out }) => {
      ev.T = code === 0;
      ev.specKnown = code !== null;
      log(`dispatch${unit ? ` ${unit}` : ""}: reproduction test ${code === 0 ? "passes" : code === null ? "did not run" : `fails (exit ${code})`}` +
          `${code === 0 ? "" : `: ${pytestGist(out)}`}`);
    }));
  }
  const ask = (model, yes, no) => review({ prompt, diff: patch, env, log, model, fetchImpl, signal })
    .then((v) => { ev[yes] = v?.verdict === "fixes"; ev[no] = v?.verdict === "does_not_fix"; ev[`${yes}_why`] = v?.why || null; ev[`${yes}_model`] = v?.model || null; ev.review_cost = (ev.review_cost || 0) + (v?.cost_usd || 0); });
  if (ruleUses(rule, "R", "Rno") && patch) jobs.push(ask(env.STEALTHLAB_REVIEW_MODEL || "gemma-4-31b-it", "R", "Rno"));
  if (ruleUses(rule, "Rs", "Rs_no") && patch) jobs.push(ask(env.STEALTHLAB_REVIEW_STRONG_MODEL || "claude-sonnet-5-5", "Rs", "Rs_no"));
  await Promise.all(jobs);
  return { ...ev, patch };
}

// the candidates (each with .evidence) -> the first one the rule accepts (one an agreeing candidate backs, first), or null.
// A and T* depend on the other candidates, so they are set here.
export function decide(cands, rule) {
  const anyT = cands.some((c) => c.evidence?.T);
  for (const c of cands) {
    const e = c.evidence || {};
    e.A = e.src?.length > 0 && cands.some((o) => o !== c && JSON.stringify(o.evidence?.src || []) === JSON.stringify(e.src));
    e["T*"] = e.T || !anyT;
  }
  const ok = cands.filter((c) => evalRule(rule, c.evidence || {}));
  return ok.find((c) => c.evidence.A) || ok[0] || null;
}

const TEST_PATH = /(^|\/)(tests?|testing)\/|(^|\/)test_[^/]*\.py$|_test\.py$/;

// an answer's patch -> {src, tst}: its source change and its test change, by file
export function splitPatch(patch) {
  const blocks = String(patch || "").split(/(?=^diff --git )/m).filter((b) => b.startsWith("diff --git "));
  const isTest = (b) => TEST_PATH.test((/ b\/(\S+)/.exec(b.split("\n", 1)[0]) || [])[1] || "");
  return { src: blocks.filter((b) => !isTest(b)).join(""), tst: blocks.filter(isTest).join("") };
}

function gitApply(cwd, patch, reverse = false) {
  return new Promise((resolve) => {
    if (!String(patch || "").trim()) { resolve(false); return; }
    const c = spawn("git", ["-C", cwd, "apply", ...(reverse ? ["-R"] : []), "--whitespace=nowarn", "-"], { windowsHide: true });
    c.on("error", () => resolve(false));
    c.on("close", (code) => resolve(code === 0));
    c.stdin.end(patch);
  });
}

function pytestIds(python, cwd, files, env = process.env) {
  return new Promise((resolve) => {
    if (!files.length) { resolve({}); return; }
    const c = spawn(python, ["-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", ...files], { cwd, env: testEnv(cwd, env), windowsHide: true });
    let out = "";
    const t = setTimeout(() => c.kill(), 300000);
    c.stdout.on("data", (d) => { out += d; });
    c.stderr.on("data", (d) => { out += d; });
    c.on("error", () => { clearTimeout(t); resolve({}); });
    c.on("close", () => {
      clearTimeout(t);
      const ids = {};
      for (const m of out.matchAll(/^(PASSED|FAILED|ERROR) (.+?)(?: - .*)?$/gm)) ids[m[2].trim()] = m[1];
      resolve(ids);
    });
  });
}

// signal C on every candidate (in place): each candidate's validated tests run on the others, in their worktrees. A
// candidate's own source change is taken out of its worktree for a moment to see its tests fail without it, then put back.
export async function crossTest(cands, { python = "python", log = () => {}, env = process.env } = {}) {
  const parts = cands.map((c) => ({ c, wt: c.result?.diff?.worktree, ...splitPatch(c.evidence?.patch) }));
  for (const p of parts) if (p.c.evidence) p.c.evidence.C = false;
  for (const y of parts) {
    if (!y.wt || !y.tst.trim() || !y.src.trim()) continue;
    const files = [...new Set([...y.tst.matchAll(/^diff --git a\/\S+ b\/(\S+\.py)$/gm)].map((m) => m[1]))];
    const withY = await pytestIds(python, y.wt, files, env);
    if (!(await gitApply(y.wt, y.src, true))) continue;
    let before;
    try { before = await pytestIds(python, y.wt, files, env); } finally { await gitApply(y.wt, y.src); }
    const validated = Object.keys(withY).filter((t) => withY[t] === "PASSED" && ["FAILED", "ERROR"].includes(before[t]));
    if (!validated.length) continue;
    for (const x of parts) {
      if (x === y || !x.wt || !x.src.trim() || !(await gitApply(x.wt, y.tst))) continue;
      let got;
      try { got = await pytestIds(python, x.wt, files, env); } finally { await gitApply(x.wt, y.tst, true); }
      if (validated.every((t) => got[t] === "PASSED")) x.c.evidence.C = true;
      log(`accept: ${x.c.rung?.unit || "?"} ${validated.every((t) => got[t] === "PASSED") ? "passes" : "fails"} the ${validated.length} validated test(s) of ${y.c.rung?.unit || "?"}`);
    }
  }
}

// what a rule decided, for the log and run.md
export function evidenceLine(e) {
  const on = SIGNALS.filter((s) => e?.[s]).join(" ");
  return `${on || "none"}${e?.Rs_why ? ` -- strong review: ${String(e.Rs_why).slice(0, 120)}` : e?.R_why ? ` -- review: ${String(e.R_why).slice(0, 120)}` : ""}`;
}

export const ACCEPT_DOC = path.join("docs", "acceptance_rules_preregistered_2026-10-16.md");
