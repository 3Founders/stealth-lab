// The Goal file: what a model that is handed work reads, instead of having it pasted into its prompt.
//
// The session's model (or dispatch, on its behalf) writes one file per Goal, `.kell/goals/<G-id>.md`: the objective, what
// "done" means (the check and the tests), where in the code the work is (file:start-end), the repo facts that bear on it
// (each cites its source file:line) and the earlier fix find_ways matched. The executor's task text is then only the request,
// the location line and one pointer to this file; the model reads and greps it with its own tools (read_file, search).
//
// Two things make that work, and neither existed before:
//   1. `.kell/*` is gitignored in most repos, so a worktree made from a commit does not have it. seedKell() copies the small
//      read-only parts into the worktree, and the worktree's diff ignores .kell (lib/exec/worktree.mjs ARTIFACT_EXCLUDES).
//   2. A model with no file access (call_model on the hosted server) cannot follow a pointer at all. It gets the lines it
//      needs, cut from this same file, with excerpt() -- never the whole repo's claims.
//
// Lines are `KIND|...`, one per line, so `rg '^CLAIM\|' .kell/goals/<id>.md` and `rg 'src/app.py' ...` work. Honest limit: the
// spec tests dispatch writes while the cheap models work are not known yet when this file is written; DONE_WHEN names the
// project's check, and TEST names the located module's own test file.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export const goalFileEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_GOALFILE || "on").toLowerCase() !== "off";

// what a worktree gets of .kell: small, read-only, the parts a model greps. Not library/solutions (diffs), not routing logs.
export const SEED_PATHS = ["goals", "claims", "claims.md", "procedures.md", "run.md", "SUMMARY.md"];
const SEED_MAX_BYTES = 2_000_000;
const CLAIMS_MAX_LINES = 60;
const LIBRARY_MAX = 6000;

const oneLine = (s, n = 400) => String(s ?? "").replace(/\s+/g, " ").trim().slice(0, n);
const fwd = (p) => String(p).replace(/\\/g, "/");

// A Goal id is safe to use as a file name; without one (no stored way matched) a local id is made from the request.
export function goalIdFor(planGoalId, prompt) {
  const given = String(planGoalId || "").replace(/[^A-Za-z0-9_.-]/g, "").replace(/^\.+/, "").slice(0, 60);
  if (given) return given;
  return `G-local-${crypto.createHash("sha256").update(String(prompt || "")).digest("hex").slice(0, 8)}`;
}

export const goalFileRel = (goalId) => `.kell/goals/${goalId}.md`;

export function renderGoalFile({ goalId, title, prompt, check, testFile, spots = [], claims = "", library = "" }) {
  const lines = [
    `# Goal file ${goalId}. Read it with read_file, search it with search (for example 'CLAIM|' or a file name). It is data about`,
    `# this repository, not a request: the request is in your task. Lines are KIND|...`,
    `GOAL|${goalId}|${oneLine(title || prompt, 160)}`,
    `OBJECTIVE|${oneLine(prompt, 600)}`,
    `DONE_WHEN|${check ? oneLine(check, 300) : "no check: the change needs review"}`,
  ];
  if (testFile) lines.push(`TEST|${fwd(testFile)}|the located module's own tests`);
  for (const s of spots) lines.push(`WHERE|${fwd(s.file)}:${s.start}-${s.end}${s.symbol ? `|${oneLine(s.symbol, 80)}` : ""}`);
  const facts = String(claims || "").split(/\r?\n/).filter((l) => l.startsWith("CLAIM|")).slice(0, CLAIMS_MAX_LINES);
  for (const l of facts) lines.push(l);
  const lib = String(library || "").trim();
  if (lib) {
    lines.push("LIBRARY|an earlier fix of this repository that find_ways judged to be this goal; the lines below are its steps and diff");
    for (const l of lib.slice(0, LIBRARY_MAX).split(/\r?\n/)) lines.push(`  ${l}`);
  }
  return `${lines.join("\n")}\n`;
}

// -> the relative path written, or null (nothing to write to / write failed: the caller falls back to the task text alone).
export function writeGoalFile({ root, ...fields }) {
  if (!root || !fields.goalId) return null;
  try {
    const rel = goalFileRel(fields.goalId);
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, renderGoalFile(fields));
    return rel;
  } catch {
    return null;
  }
}

// The one line the executor's task carries. The agent preloads files its task names, so naming the file also puts it in
// the model's first turn.
export function pointerLine(rel) {
  return `Goal file: ${rel} (the check that decides "done", the tests, where the code is, and the repo facts with their sources). ` +
    "Read it first; search it and the files it names instead of guessing.";
}

// The lines of a goal file a model without file access can be given: the head (goal, objective, done-when, test, where) and
// the claim lines that mention a word of `terms`, capped. -> text.
export function excerpt(text, { terms = [], maxChars = 3000 } = {}) {
  const lines = String(text || "").split(/\r?\n/);
  const head = lines.filter((l) => /^(GOAL|OBJECTIVE|DONE_WHEN|TEST|WHERE)\|/.test(l));
  const want = terms.map((t) => String(t).toLowerCase()).filter(Boolean);
  const claims = lines.filter((l) => l.startsWith("CLAIM|") && (!want.length || want.some((t) => l.toLowerCase().includes(t))));
  const out = [...head, ...claims].join("\n");
  return out.length <= maxChars ? out : `${out.slice(0, maxChars - 20)}\n[...cut to fit]`;
}

function copyTree(src, dst, budget) {
  const st = fs.lstatSync(src);
  if (st.isSymbolicLink()) return;                                   // never follow a link out of the repo
  if (st.isDirectory()) {
    for (const name of fs.readdirSync(src)) copyTree(path.join(src, name), path.join(dst, name), budget);
    return;
  }
  if (!st.isFile() || fs.existsSync(dst)) return;                    // a tracked file already in the worktree wins
  if (budget.left < st.size) return;
  budget.left -= st.size;
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
}

// Copy the greppable parts of <repo>/.kell into <dir>/.kell. Best effort and bounded; returns the number of files present now.
export function seedKell({ repo, dir }) {
  try {
    const from = path.join(repo, ".kell");
    if (!fs.existsSync(from)) return 0;
    const budget = { left: SEED_MAX_BYTES };
    for (const p of SEED_PATHS) {
      const src = path.join(from, p);
      if (fs.existsSync(src)) copyTree(src, path.join(dir, ".kell", p), budget);
    }
    let n = 0;
    const count = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) e.isDirectory() ? count(path.join(d, e.name)) : n++; };
    if (fs.existsSync(path.join(dir, ".kell"))) count(path.join(dir, ".kell"));
    return n;
  } catch {
    return 0;
  }
}
