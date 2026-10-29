import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { LINK_NAMES, depsEnv, linkProjectDeps, unlinkProjectDeps } from "../lib/exec/deps.mjs";
import { relatedChecks } from "../lib/exec/related.mjs";
import { createWorktree, removeWorktree } from "../lib/exec/worktree.mjs";
import { ExecRuntime } from "../lib/exec/runtime.mjs";
import { CONTEXT_MAX, repoContext } from "../lib/task_context.mjs";
import { addEntry } from "../lib/library.mjs";
import { dispatch } from "../lib/dispatch.mjs";
import { git, makeRepo, stubAdapter, testEnv, tmpDir } from "./fixtures/exec/helpers.mjs";
import { LEGACY_POLICY } from "./helpers/legacy_policy.mjs";

const g = (repo, ...a) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
const write = (root, rel, text = "x") => { fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true }); fs.writeFileSync(path.join(root, rel), text); };

// a repo that ignores its dependency dirs and has a real node_modules and venv on disk
function projectRepo({ ignore = "node_modules\n.venv\nvenv\n" } = {}) {
  const repo = tmpDir("deps-repo-");
  g(repo, "init", "-q"); g(repo, "config", "user.email", "t@e"); g(repo, "config", "user.name", "t"); g(repo, "config", "core.autocrlf", "false");
  write(repo, ".gitignore", ignore);
  write(repo, "app.py", "x = 1\n");
  g(repo, "add", "-A"); g(repo, "commit", "-q", "-m", "init");
  write(repo, "node_modules/pkg/index.js", "module.exports = 1\n");
  write(repo, "node_modules/.bin/tool", "#!/bin/sh\n");
  write(repo, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python", "x");
  return repo;
}
const worktreeOf = async (repo) => {
  const dir = path.join(tmpDir("deps-wt-"), "wt");
  const env = process.env;
  await createWorktree({ repo, commit: g(repo, "rev-parse", "HEAD").trim(), dir, patch: null, env });
  return { dir, env };
};

// ---- B2: dependency links ------------------------------------------------------------------------------------------------

test("B2: the project's node_modules and venv are linked into the worktree; git sees no change; PATH leads with them", async () => {
  const repo = projectRepo();
  const { dir, env } = await worktreeOf(repo);
  assert.ok(fs.existsSync(path.join(dir, "node_modules", "pkg", "index.js")), "node_modules reachable in the worktree");
  assert.ok(fs.lstatSync(path.join(dir, "node_modules")).isSymbolicLink());
  assert.equal(g(dir, "status", "--porcelain").trim(), "", "the links are not changes");
  const e = depsEnv(dir, { PATH: "/usr/bin" });
  assert.ok(e.PATH.startsWith(path.join(dir, process.platform === "win32" ? ".venv\\Scripts" : ".venv/bin")), e.PATH);
  assert.ok(e.PATH.includes(path.join(dir, "node_modules", ".bin")));
  assert.equal(e.VIRTUAL_ENV, fs.realpathSync(path.join(repo, ".venv")));
  await removeWorktree({ repo, dir, env });
});

test("B2 (the one that matters): removing the worktree leaves the project's REAL node_modules and venv untouched", async () => {
  const repo = projectRepo();
  const { dir, env } = await worktreeOf(repo);
  assert.ok(fs.lstatSync(path.join(dir, "node_modules")).isSymbolicLink(), "precondition: linked");
  await removeWorktree({ repo, dir, env });
  assert.ok(!fs.existsSync(dir), "the worktree is gone");
  assert.ok(fs.existsSync(path.join(repo, "node_modules", "pkg", "index.js")), "node_modules survived");
  assert.ok(fs.existsSync(path.join(repo, "node_modules", ".bin", "tool")));
  assert.ok(fs.existsSync(path.join(repo, ".venv")), "the venv survived");
});

test("B2: nothing is linked when git does not ignore the directory, when the switch is off, or when it is not there", async () => {
  const tracked = projectRepo({ ignore: "# nothing ignored\n" });
  const a = await worktreeOf(tracked);
  assert.ok(!fs.existsSync(path.join(a.dir, "node_modules")));
  const repo = projectRepo();
  const dir = path.join(tmpDir("deps-wt-"), "wt");
  g(repo, "worktree", "add", "--detach", dir, "HEAD");
  assert.deepEqual(linkProjectDeps({ repo, dir, env: { STEALTHLAB_EXEC_LINK_DEPS: "off" } }), []);
  assert.ok(!fs.existsSync(path.join(dir, "node_modules")));
  const bare = tmpDir("deps-bare-");
  assert.deepEqual(linkProjectDeps({ repo: bare, dir: tmpDir("deps-dst-"), env: {} }), []);
  assert.equal(depsEnv(bare, { PATH: "a" }).PATH, "a", "no venv, no change to PATH");
});

test("B2: unlinking only ever removes a link, never a real directory", () => {
  const dir = tmpDir("deps-real-");
  write(dir, "node_modules/keep.js", "k");
  unlinkProjectDeps(dir);
  assert.ok(fs.existsSync(path.join(dir, "node_modules", "keep.js")));
  assert.deepEqual([...LINK_NAMES], ["node_modules", ".venv", "venv"]);
});

// ---- B3: related tests first ---------------------------------------------------------------------------------------------

test("B3: pytest -- the tests next to a changed module run first; a changed test file counts itself; pinned checks are left alone", () => {
  const cwd = tmpDir("rel-");
  write(cwd, "src/calc.py"); write(cwd, "tests/test_calc.py"); write(cwd, "tests/test_other.py"); write(cwd, "src/util_test.py");
  write(cwd, "node_modules/x/test_calc.py");                                  // never searched
  const one = relatedChecks(["python -m pytest -q"], ["src/calc.py"], cwd);
  assert.deepEqual(one, [{ cmd: "python -m pytest -q -x tests/test_calc.py", files: ["tests/test_calc.py"] }]);
  assert.deepEqual(relatedChecks(["python -m pytest -q"], ["tests/test_other.py"], cwd)[0].files, ["tests/test_other.py"]);
  assert.deepEqual(relatedChecks(["python -m pytest -q"], ["src/util.py"], cwd)[0].files, ["src/util_test.py"]);
  assert.deepEqual(relatedChecks(["python -m pytest -q test_check.py"], ["src/calc.py"], cwd), [], "already names its tests");
  assert.deepEqual(relatedChecks(["python -m pytest -q"], ["src/nothing_related.py"], cwd), []);
  assert.deepEqual(relatedChecks(["python -m pytest -q"], ["README.md"], cwd), []);
});

test("B3: a compound check (project check, then the test-first runner) uses its first part; vitest and jest use their own flags; other checks and the switch", () => {
  const cwd = tmpDir("rel-");
  write(cwd, "tests/test_calc.py");
  const compound = relatedChecks(['python -m pytest -q && python "C:/spec/run_spec.py"'], ["calc.py"], cwd);
  assert.equal(compound[0].cmd, "python -m pytest -q -x tests/test_calc.py");
  assert.deepEqual(relatedChecks(["npx vitest run"], ["src/a.ts", "README.md"], cwd), [{ cmd: "npx vitest related src/a.ts --run", files: ["src/a.ts"] }]);
  assert.deepEqual(relatedChecks(["npx jest"], ["src/a.js"], cwd), [{ cmd: "npx jest --findRelatedTests src/a.js", files: ["src/a.js"] }]);
  assert.deepEqual(relatedChecks(["npm test"], ["src/a.js"], cwd), []);
  assert.deepEqual(relatedChecks(["go test ./..."], ["a.go"], cwd), []);
  assert.deepEqual(relatedChecks(["python -m pytest -q"], ["calc.py"], cwd, { STEALTHLAB_EXEC_RELATED: "off" }), []);
});

function repoWithTests() {
  const repo = makeRepo();
  write(repo, "tests/test_calc.py", "def test_x(): pass\n");
  git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "tests");
  return repo;
}
const events = (env, runId) => fs.readFileSync(path.join(env.STEALTHLAB_HOME, "runs", runId, "events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));

test("B3 through the runtime: a related-test failure ends the attempt at once; a pass is followed by the full check", async () => {
  // check.mjs passes only when calc.txt says "fixed"; the word pytest in the command makes it a pytest-style check
  const CHECK = "node check.mjs pytest";
  const bad = repoWithTests();
  const envBad = testEnv();
  const rtBad = new ExecRuntime({ env: envBad, adapters: { stub: stubAdapter({ scenario: { edits: [{ path: "calc.py", content: "x = 2\n" }, { path: "calc.txt", content: "still broken\n" }], finalMessage: "done" } }) } });
  const a = await rtBad.achieve({ repo_path: bad, task: "Fix calc.", checks: [CHECK], scope: ["calc.py", "calc.txt"], executor: "stub", model: "m1" });
  const resA = await rtBad.runResult(a.run_id, 30);
  assert.equal(resA.state, "failed");
  assert.equal(resA.checks.length, 1, "only the related run happened");
  assert.match(resA.checks[0].cmd, /pytest -x tests\/test_calc\.py$/);
  assert.equal(events(envBad, a.run_id).filter((e) => e.event === "check_start").length, 1);

  const good = repoWithTests();
  const envGood = testEnv();
  const rtGood = new ExecRuntime({ env: envGood, adapters: { stub: stubAdapter({ scenario: { edits: [{ path: "calc.py", content: "x = 2\n" }, { path: "calc.txt", content: "fixed\n" }], finalMessage: "done" } }) } });
  const b = await rtGood.achieve({ repo_path: good, task: "Fix calc.", checks: [CHECK], scope: ["calc.py", "calc.txt"], executor: "stub", model: "m1" });
  const resB = await rtGood.runResult(b.run_id, 30);
  assert.equal(resB.state, "verified");
  assert.deepEqual(resB.checks.map((c) => c.cmd), [CHECK], "the verdict is the FULL check's");
  const starts = events(envGood, b.run_id).filter((e) => e.event === "check_start").map((e) => e.cmd);
  assert.equal(starts.length, 2, "related first, then the full check");
  assert.match(starts[0], /-x tests\/test_calc\.py/);
  assert.equal(starts[1], CHECK);
  await rtBad.cancelRun(a.run_id); await rtGood.cancelRun(b.run_id);
});

test("B3: STEALTHLAB_EXEC_RELATED=off is the old behaviour (the full check alone)", async () => {
  const repo = repoWithTests();
  const env = testEnv({ extra: { STEALTHLAB_EXEC_RELATED: "off" } });
  const rt = new ExecRuntime({ env, adapters: { stub: stubAdapter({ scenario: { edits: [{ path: "calc.py", content: "x = 2\n" }, { path: "calc.txt", content: "fixed\n" }], finalMessage: "done" } }) } });
  const r = await rt.achieve({ repo_path: repo, task: "Fix calc.", checks: ["node check.mjs pytest"], scope: ["calc.py", "calc.txt"], executor: "stub", model: "m1" });
  assert.equal((await rt.runResult(r.run_id, 30)).state, "verified");
  assert.equal(events(env, r.run_id).filter((e) => e.event === "check_start").length, 1);
  await rt.cancelRun(r.run_id);
});

// ---- B1: repo facts and library matches in the task text -------------------------------------------------------------------

function surveyedRepo() {
  const repo = tmpDir("ctx-repo-");
  g(repo, "init", "-q"); g(repo, "config", "user.email", "t@e"); g(repo, "config", "user.name", "t");
  write(repo, "calc.py", "x = 1\n");
  g(repo, "add", "-A"); g(repo, "commit", "-q", "-m", "init");
  fs.mkdirSync(path.join(repo, ".kell"));
  write(repo, ".kell/claims.md", "# Claims\n- Tests run with `python -m pytest -q` from the repo root (pyproject.toml:12)\n- calc.py owns every constant (calc.py:1)\nCLAIM|R-009|current|code|repository|calc.py owns every constant|source=calc.py:1#sha=abc|version=1|by=agent|key=calc\n");
  return repo;
}

test("B1: the context holds the survey's facts and the judged library match with its diff, within 4 KB; off, no root and an empty repo give nothing", () => {
  const repo = surveyedRepo();
  const diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n";
  const entry = addEntry(repo, { title: "Fix the calc constant", check: "python -m pytest -q", observed: true, diff });
  const reply = { library_matches: [{ id: entry.id, relation: "matches", title: entry.title, outcome: "pass", verified_at: "2026-10-12", judged: true, confidence: 0.9 }] };
  const ctx = repoContext({ reply, root: repo, cwd: repo, prompt: "fix the calc constant", env: {} });
  assert.match(ctx, /^Context from this repository/);
  assert.match(ctx, /Tests run with `python -m pytest -q` from the repo root/);
  assert.match(ctx, /Solved before in THIS repo \(matches, confidence 0\.9\) -- Fix the calc constant/);
  assert.match(ctx, /\+x = 2/);
  assert.ok(ctx.length <= CONTEXT_MAX);
  assert.equal(repoContext({ reply, root: repo, cwd: repo, prompt: "x", env: { STEALTHLAB_DISPATCH_CONTEXT: "off" } }), "");
  assert.equal(repoContext({ reply, root: null, env: {} }), "");
  const bare = tmpDir("ctx-bare-");
  fs.mkdirSync(path.join(bare, ".kell"));
  assert.equal(repoContext({ reply: {}, root: bare, cwd: bare, prompt: "x", env: {} }), "");
  const huge = surveyedRepo();
  write(huge, ".kell/claims.md", `# Claims\n${"- a fact that goes on and on (a.py:1)\n".repeat(900)}`);
  assert.ok(repoContext({ reply: {}, root: huge, cwd: huge, prompt: "x", env: {} }).length <= CONTEXT_MAX);
});

test("B1 through dispatch: the goal file holds the context and the task points to it; inline it is the prompt then the context; with both off it is the prompt alone", async () => {
  const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "claude-opus-5-5|claude-code"] };
  for (const mode of ["goalfile", "inline", "none"]) {
    const repo = surveyedRepo();
    const home = tmpDir("ctx-home-");
    fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
    const seen = [];
    const rt = { async achieve(i) { seen.push(i.task); return { run_id: "r-1" }; },
      async runResult() { return { state: "failed", cost_usd: 0.001, checks: [{ tail: "no" }] }; }, async cancelRun() { return {}; } };
    const extra = mode === "goalfile" ? {} : mode === "inline" ? { STEALTHLAB_DISPATCH_GOALFILE: "off" }
      : { STEALTHLAB_DISPATCH_GOALFILE: "off", STEALTHLAB_DISPATCH_CONTEXT: "off" };
    await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: repo, mine: "claude-opus-5-5|claude-code",
      env: { STEALTHLAB_HOME: home, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off", STEALTHLAB_DISPATCH_CROSSCHECK: "off",
             STEALTHLAB_BRIEF: "off", STEALTHLAB_DISPATCH_LOCATE: "off", ...extra }, runtime: rt });
    assert.ok(seen[0].startsWith("Fix calc.py so x is 2"));
    if (mode === "none") assert.equal(String(seen[0]).replace(/\n\nTesting: [^\n]*$/, ""), "Fix calc.py so x is 2", "the prompt, then only the testing guidance");
    else if (mode === "inline") assert.match(seen[0], /\n\nContext from this repository[\s\S]*calc\.py owns every constant/);
    else {
      assert.match(seen[0], /Goal file: \.kell\/goals\/g1\.md/);
      assert.doesNotMatch(seen[0], /owns every constant/, "the facts are in the file, not the prompt");
      const file = fs.readFileSync(path.join(repo, ".kell", "goals", "g1.md"), "utf8");
      assert.match(file, /^CLAIM\|.*calc\.py owns every constant/m);
      assert.match(file, /^DONE_WHEN\|python -m pytest( --continue-on-collection-errors)? -q$/m);
    }
  }
});

test("the task text never exceeds the executor's 40,000 characters: the request first, the context in what is left", async () => {
  const { fitTask } = await import("../lib/dispatch.mjs");
  const ctx = "Context from this repository:\n" + "fact. ".repeat(3000);
  assert.equal(fitTask("short task", "").length, "short task".length);
  const a = fitTask("t".repeat(30000), ctx);
  assert.ok(a.length <= 40000 && a.startsWith("t".repeat(30000)) && a.includes("Context from this repository"));
  assert.equal(fitTask("t".repeat(39800), ctx), "t".repeat(39800), "under 300 characters of room: no context");
});
