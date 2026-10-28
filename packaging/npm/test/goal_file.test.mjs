import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { excerpt, goalFileRel, goalIdFor, pointerLine, renderGoalFile, seedKell, writeGoalFile } from "../lib/goal_file.mjs";
import { ARTIFACT_EXCLUDES, createWorktree, headCommit, removeWorktree, snapshotTree } from "../lib/exec/worktree.mjs";

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "goalfile-"));
const git = (cwd, ...a) => execFileSync("git", ["-C", cwd, ...a], { encoding: "utf8" });

const CLAIMS = [
  "# claims",
  "CLAIM|R-1|x|y|z|The tests run with pytest from backend/|source=backend/pytest.ini:1#sha=abc",
  "CLAIM|R-2|x|y|z|parse_date lives in src/dates.py|source=src/dates.py:40#sha=def",
].join("\n");

test("goalIdFor keeps a safe Goal id and makes a stable local one without it", () => {
  assert.equal(goalIdFor("G-1a2b", "x"), "G-1a2b");
  assert.equal(goalIdFor("../../evil", "x"), "evil");
  assert.match(goalIdFor(null, "fix the date bug"), /^G-local-[0-9a-f]{8}$/);
  assert.equal(goalIdFor(null, "fix the date bug"), goalIdFor(undefined, "fix the date bug"));
});

test("the goal file holds the check, tests, locations and cited claims as greppable lines", () => {
  const text = renderGoalFile({
    goalId: "G-1", title: "dates", prompt: "parse_date drops the timezone", check: "pytest tests/test_dates.py -q",
    testFile: "tests\\test_dates.py", spots: [{ file: "src/dates.py", start: 40, end: 70, symbol: "parse_date" }],
    claims: CLAIMS, library: "Known way:\nstep 1 edit parse_date",
  });
  const lines = text.split("\n");
  assert.ok(lines.includes("DONE_WHEN|pytest tests/test_dates.py -q"));
  assert.ok(lines.includes("TEST|tests/test_dates.py|the located module's own tests"));
  assert.ok(lines.includes("WHERE|src/dates.py:40-70|parse_date"));
  assert.equal(lines.filter((l) => l.startsWith("CLAIM|")).length, 2);
  assert.match(text, /source=src\/dates\.py:40/);
  assert.match(text, /LIBRARY\|/);
});

test("with no check the file says so instead of inventing one", () => {
  assert.match(renderGoalFile({ goalId: "G-2", prompt: "p", check: null }), /DONE_WHEN\|no check: the change needs review/);
});

test("writeGoalFile writes under .kell/goals and returns the relative path; no root writes nothing", () => {
  const root = tmp();
  const rel = writeGoalFile({ root, goalId: "G-3", prompt: "p", claims: CLAIMS });
  assert.equal(rel, goalFileRel("G-3"));
  assert.ok(fs.existsSync(path.join(root, ".kell", "goals", "G-3.md")));
  assert.equal(writeGoalFile({ root: null, goalId: "G-3", prompt: "p" }), null);
  assert.match(pointerLine(rel), /\.kell\/goals\/G-3\.md/);
});

test("excerpt gives a model without files the head and only the claims that mention its terms", () => {
  const text = renderGoalFile({ goalId: "G-4", prompt: "p", check: "c", claims: CLAIMS });
  const out = excerpt(text, { terms: ["parse_date"] });
  assert.match(out, /DONE_WHEN\|c/);
  assert.match(out, /parse_date lives in/);
  assert.doesNotMatch(out, /pytest from backend/);
  assert.ok(excerpt(text, { terms: [], maxChars: 40 }).length <= 40);
});

test("seedKell copies the greppable parts, never library diffs or routing logs, and keeps a file already there", () => {
  const repo = tmp(); const dir = tmp();
  fs.mkdirSync(path.join(repo, ".kell", "goals"), { recursive: true });
  fs.mkdirSync(path.join(repo, ".kell", "library", "solutions"), { recursive: true });
  fs.writeFileSync(path.join(repo, ".kell", "goals", "G-5.md"), "GOAL|G-5|x\n");
  fs.writeFileSync(path.join(repo, ".kell", "claims.md"), CLAIMS);
  fs.writeFileSync(path.join(repo, ".kell", "routing.md"), "secret routing\n");
  fs.writeFileSync(path.join(repo, ".kell", "library", "solutions", "L-1.diff"), "diff\n");
  fs.mkdirSync(path.join(dir, ".kell"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".kell", "claims.md"), "tracked version");
  const n = seedKell({ repo, dir });
  assert.equal(fs.readFileSync(path.join(dir, ".kell", "goals", "G-5.md"), "utf8"), "GOAL|G-5|x\n");
  assert.equal(fs.readFileSync(path.join(dir, ".kell", "claims.md"), "utf8"), "tracked version");
  assert.ok(!fs.existsSync(path.join(dir, ".kell", "routing.md")));
  assert.ok(!fs.existsSync(path.join(dir, ".kell", "library")));
  assert.equal(n, 2);
});

test("seedKell with no .kell in the repo does nothing", () => {
  assert.equal(seedKell({ repo: tmp(), dir: tmp() }), 0);
});

test("a real worktree of a repo that gitignores .kell has the goal file, and the run's diff does not", async () => {
  const repo = tmp();
  git(repo, "init", "-q");
  git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t");
  fs.writeFileSync(path.join(repo, ".gitignore"), ".kell/*\n");
  fs.writeFileSync(path.join(repo, "a.txt"), "one\n");
  git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "init");
  writeGoalFile({ root: repo, goalId: "G-6", prompt: "p", claims: CLAIMS, check: "true" });
  fs.writeFileSync(path.join(repo, ".kell", "claims.md"), CLAIMS);
  const env = process.env;
  const dir = path.join(tmp(), "wt");
  const wt = await createWorktree({ repo, commit: await headCommit(repo, env), dir, env });
  try {
    assert.ok(fs.existsSync(path.join(dir, ".kell", "goals", "G-6.md")), "the goal file is in the worktree");
    assert.match(fs.readFileSync(path.join(dir, ".kell", "claims.md"), "utf8"), /parse_date/);
    fs.writeFileSync(path.join(dir, "a.txt"), "two\n");
    fs.writeFileSync(path.join(dir, ".kell", "goals", "G-6.md"), "edited by the model\n");
    const after = await snapshotTree(dir, env);
    const names = git(dir, "diff", "--name-only", wt.baseTree, after).split("\n").filter(Boolean);
    assert.deepEqual(names, ["a.txt"]);
  } finally {
    await removeWorktree({ repo, dir, env });
  }
  assert.ok(ARTIFACT_EXCLUDES.includes("**/.kell/**"));
});
