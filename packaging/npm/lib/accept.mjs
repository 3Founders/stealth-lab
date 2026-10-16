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
//   R/Rno  the cheap reviewer says it fixes / does not fix the request     (STEALTHLAB_REVIEW_MODEL, gpt-oss-120b)
//   Rs/Rs_no  the strong reviewer says so                                  (STEALTHLAB_REVIEW_STRONG_MODEL, claude-sonnet-5-5)
// In this mode the cheap agents work against the project's check only, never our written tests: refining code until
// generated tests pass raises false accepts to ~60% (Ahmed et al., FSE 2026). Our tests and the reviewers judge the
// finished answer. Only the signals the rule names are computed (no tests are written for a rule without T).
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { changesSource, reviewPatch } from "./review.mjs";

export const SIGNALS = ["E", "T", "T*", "S", "F", "X", "A", "R", "Rno", "Rs", "Rs_no"];

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

function runSpecIn(python, runner, cwd, signal) {
  return new Promise((resolve) => {
    const c = spawn(python, [runner], { cwd, windowsHide: true });
    const t = setTimeout(() => c.kill(), 300000);
    signal?.addEventListener?.("abort", () => c.kill());
    c.on("error", () => { clearTimeout(t); resolve(null); });
    c.on("close", (code) => { clearTimeout(t); resolve(code); });
  });
}

// the evidence on one answer that passed the project's check -> signals (only those the rule needs) + its patch
export async function answerEvidence({ rule, result, spec, specRunner, python = "python", prompt, env = process.env, log = () => {},
                                       signal = null, fetchImpl, review = reviewPatch }) {
  let patch = "";
  try { patch = result?.diff?.patch_path ? fs.readFileSync(result.diff.patch_path, "utf8") : ""; } catch { /* no patch read */ }
  const ev = { E: true, ...patchSignals(patch), T: false, R: false, Rno: false, Rs: false, Rs_no: false, specKnown: false };
  const jobs = [];
  if (ruleUses(rule, "T", "T*") && spec?.file && specRunner && result?.diff?.worktree) {
    jobs.push(runSpecIn(python, specRunner, result.diff.worktree, signal).then((code) => { ev.T = code === 0; ev.specKnown = code !== null; }));
  }
  const ask = (model, yes, no) => review({ prompt, diff: patch, env, log, model, fetchImpl, signal })
    .then((v) => { ev[yes] = v?.verdict === "fixes"; ev[no] = v?.verdict === "does_not_fix"; ev[`${yes}_why`] = v?.why || null; ev.review_cost = (ev.review_cost || 0) + (v?.cost_usd || 0); });
  if (ruleUses(rule, "R", "Rno") && patch) jobs.push(ask(env.STEALTHLAB_REVIEW_MODEL || "gpt-oss-120b", "R", "Rno"));
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

// what a rule decided, for the log and run.md
export function evidenceLine(e) {
  const on = SIGNALS.filter((s) => e?.[s]).join(" ");
  return `${on || "none"}${e?.Rs_why ? ` -- strong review: ${String(e.Rs_why).slice(0, 120)}` : e?.R_why ? ` -- review: ${String(e.R_why).slice(0, 120)}` : ""}`;
}

export const ACCEPT_DOC = path.join("docs", "acceptance_rules_preregistered_2026-10-16.md");
