import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { KEEP, createUndoPoint, discardUndoPoint, finishUndoPoint, listUndoPoints, runUndoCli, undo } from "../lib/undo.mjs";
import { dispatch, triedNote } from "../lib/dispatch.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");
const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));

function repoWith(files) {
  const repo = tmp("undo-repo-");
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe", encoding: "utf8" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(repo, rel)), { recursive: true });
    fs.writeFileSync(path.join(repo, rel), text);
  }
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { repo, g };
}
const read = (repo, rel) => fs.readFileSync(path.join(repo, rel), "utf8");
const env = () => ({ STEALTHLAB_HOME: tmp("undo-home-") });

// ---- the undo point is invisible to the user's git, and restores ---------------------------------------------------------

test("an undo point is a hidden ref: no commit on the branch, nothing staged, no stash entry; the user's uncommitted edit is in it", () => {
  const { repo, g } = repoWith({ "calc.py": "x = 1\n", "lib/util.py": "u = 1\n" });
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n# my uncommitted note\n");      // the user's own work in progress
  const before = { log: g("log", "--oneline"), status: g("status", "--porcelain"), stash: g("stash", "list") };
  const e = env();
  const p = createUndoPoint({ repo, files: ["calc.py", "lib/util.py", "new/created.py"], model: "gemma", task: "fix x", env: e });
  assert.deepEqual({ log: g("log", "--oneline"), status: g("status", "--porcelain"), stash: g("stash", "list") }, before);
  assert.match(g("for-each-ref", "refs/stealthlab/undo/"), new RegExp(`refs/stealthlab/undo/${p.id}`));
  assert.equal(g("show", `${p.ref}:calc.py`), "x = 1\n# my uncommitted note\n", "the point holds the file as it is NOW, not as committed");
});

test("undo puts back edited files exactly, removes files the answer created, and says what it did", () => {
  const { repo } = repoWith({ "calc.py": "x = 1\n" });
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n# mine\n");
  const e = env();
  const p = createUndoPoint({ repo, files: ["calc.py", "added.py"], model: "gemma", env: e });
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n");                              // the answer applied
  fs.writeFileSync(path.join(repo, "added.py"), "new = 1\n");
  finishUndoPoint(p, { repo, env: e });
  const r = undo({ repo, env: e });
  assert.deepEqual({ restored: r.restored, removed: r.removed }, { restored: ["calc.py"], removed: ["added.py"] });
  assert.equal(read(repo, "calc.py"), "x = 1\n# mine\n");
  assert.equal(fs.existsSync(path.join(repo, "added.py")), false);
  assert.throws(() => undo({ repo, env: e }), /nothing to undo/);                       // once
});

test("undo refuses when you edited a file after the answer; --force does it anyway; an unapplied point cannot be undone", () => {
  const { repo } = repoWith({ "calc.py": "x = 1\n" });
  const e = env();
  const p = createUndoPoint({ repo, files: ["calc.py"], env: e });
  assert.throws(() => undo({ repo, id: p.id, env: e }), /was never applied/);
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n");
  finishUndoPoint(p, { repo, env: e });
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n# edited after\n");
  assert.throws(() => undo({ repo, env: e }), /calc\.py changed since the answer was applied/);
  assert.equal(read(repo, "calc.py"), "x = 2\n# edited after\n", "nothing was thrown away");
  undo({ repo, env: e, force: true });
  assert.equal(read(repo, "calc.py"), "x = 1\n");
});

test("discard removes the ref and the manifest; the 20 newest points per repository are kept", () => {
  const { repo, g } = repoWith({ "calc.py": "x = 1\n" });
  const e = env();
  const gone = createUndoPoint({ repo, files: ["calc.py"], env: e });
  discardUndoPoint(gone, { repo, env: e });
  assert.equal(g("for-each-ref", "refs/stealthlab/undo/").trim(), "");
  for (let i = 0; i < KEEP + 3; i++) createUndoPoint({ repo, files: ["calc.py"], env: e, now: new Date(Date.UTC(2026, 9, 1, 0, 0, i)) });
  assert.equal(listUndoPoints({ repo, env: e }).length, KEEP);
  assert.equal(g("for-each-ref", "refs/stealthlab/undo/").trim().split("\n").length, KEEP);
});

test("binary and executable-looking content survive the round trip byte for byte", () => {
  const { repo } = repoWith({ "data.bin": "x" });
  const bytes = Buffer.from([0, 255, 13, 10, 13, 10, 7, 200]);
  fs.writeFileSync(path.join(repo, "data.bin"), bytes);
  const e = env();
  const p = createUndoPoint({ repo, files: ["data.bin"], env: e });
  fs.writeFileSync(path.join(repo, "data.bin"), "changed");
  finishUndoPoint(p, { repo, env: e });
  undo({ repo, env: e });
  assert.ok(fs.readFileSync(path.join(repo, "data.bin")).equals(bytes));
});

test("stealthlab-mcp undo: --list, the latest point, an error outside a repo", () => {
  const { repo } = repoWith({ "calc.py": "x = 1\n" });
  const e = env();
  const p = createUndoPoint({ repo, files: ["calc.py"], model: "gemma", task: "fix x", env: e });
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n");
  finishUndoPoint(p, { repo, env: e });
  const out = [];
  assert.equal(runUndoCli(["--list"], { cwd: repo, env: e, print: (s) => out.push(s) }), 0);
  assert.match(out[0], new RegExp(`${p.id}.*gemma.*calc\\.py.*fix x`));
  assert.equal(runUndoCli([], { cwd: repo, env: e, print: (s) => out.push(s) }), 0);
  assert.match(out[1], /undone .*restored calc\.py/);
  assert.equal(read(repo, "calc.py"), "x = 1\n");
  const real = spawnSync(process.execPath, [BIN, "undo", "--list"], { cwd: tmp("not-a-repo-"), encoding: "utf8", env: { ...process.env, ...e } });
  assert.equal(real.status, 1);
  assert.match(real.stdout, /not inside a git repository/);
});

// ---- through dispatch ----------------------------------------------------------------------------------------------------

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "claude-opus-5-5|claude-code"] };
const MINE = "claude-opus-5-5|claude-code";

function setup(extra = {}) {
  const home = tmp("disp-home-");
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const { repo } = repoWith({ "calc.py": "x = 1\n" });
  fs.mkdirSync(path.join(repo, ".kell"));
  fs.writeFileSync(path.join(repo, ".kell", ".keep"), "");
  return { repo, env: { STEALTHLAB_HOME: home, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                        STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", ...extra } };
}
function stubRuntime(repo, { applyError = null } = {}) {
  const calls = [];
  return { calls,
    async achieve(input) { calls.push(input); return { run_id: "r-20261012000000-abc123" }; },
    async runResult() { return { state: "verified", cost_usd: 0.0012, diff: { files: ["calc.py"], stat: " calc.py | 2 +-\n 1 file changed" }, checks: [{ tail: "ok" }] }; },
    async applyRun() { if (applyError) throw applyError; fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun() { return {}; } };
}
const run = (s, rt) => dispatch({ payload: { cwd: s.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: s.repo, mine: MINE, env: s.env, runtime: rt });

test("dispatch: the answer is applied with an undo point, the message names model, files, cost and the undo command, and undo restores", async () => {
  const s = setup();
  const d = await run(s, stubRuntime(s.repo));
  assert.equal(d.handled, true);
  assert.match(d.text, /Done by gemma-4-31b-it .*\$0\.0012/);
  assert.match(d.text, /applied: calc\.py/);
  assert.match(d.text, /To put it back as it was: stealthlab-mcp undo\./);
  assert.equal(read(s.repo, "calc.py"), "x = 2\n");
  const r = undo({ repo: s.repo, env: s.env });
  assert.deepEqual(r.restored, ["calc.py"]);
  assert.equal(read(s.repo, "calc.py"), "x = 1\n");
});

test("dispatch: a file edited during the run is NOT overwritten: 'not applied, you edited <file>', no undo point left, the session is told", async () => {
  const s = setup();
  const err = Object.assign(new Error("refusing: target file(s) changed since the run started: calc.py"), { changed: ["calc.py"] });
  const d = await run(s, stubRuntime(s.repo, { applyError: err }));
  assert.equal(d.handled, false);
  assert.deepEqual(d.notApplied, { files: ["calc.py"], model: "gemma-4-31b-it", run_id: "r-20261012000000-abc123" });
  assert.match(triedNote(d), /gemma-4-31b-it solved this and its check passed, but the change was NOT applied: you edited calc\.py while it ran/);
  assert.equal(read(s.repo, "calc.py"), "x = 1\n", "the checkout is untouched");
  assert.deepEqual(listUndoPoints({ repo: s.repo, env: s.env }), []);
});

test("dispatch: any other apply error is not swallowed, and its undo point is discarded", async () => {
  const s = setup();
  await assert.rejects(run(s, stubRuntime(s.repo, { applyError: new Error("refusing: unresolved merge") })), /unresolved merge/);
  assert.deepEqual(listUndoPoints({ repo: s.repo, env: s.env }), []);
});

test("dispatch: STEALTHLAB_DISPATCH_ASK=on stops before applying and shows the summary and the apply command", async () => {
  const s = setup({ STEALTHLAB_DISPATCH_ASK: "on" });
  const rt = stubRuntime(s.repo);
  rt.applyRun = async () => assert.fail("ask mode never applies");
  const d = await run(s, rt);
  assert.equal(d.handled, true);
  assert.equal(d.ask, true);
  assert.match(d.text, /Ready, NOT applied: gemma-4-31b-it/);
  assert.match(d.text, /calc\.py \| 2 \+-/);
  assert.match(d.text, /stealthlab-mcp exec apply r-20261012000000-abc123/);
  assert.equal(read(s.repo, "calc.py"), "x = 1\n");
});

test("dispatch: STEALTHLAB_DISPATCH_UNDO=off applies without a point, as before", async () => {
  const s = setup({ STEALTHLAB_DISPATCH_UNDO: "off" });
  const d = await run(s, stubRuntime(s.repo));
  assert.equal(d.handled, true);
  assert.doesNotMatch(d.text, /stealthlab-mcp undo/);
  assert.deepEqual(listUndoPoints({ repo: s.repo, env: s.env }), []);
  assert.equal(read(s.repo, "calc.py"), "x = 2\n");
});

// ---- exec apply (and ask mode's next step) makes an undo point too -----------------------------------------------------------------------

import { applyWithUndo } from "../lib/undo.mjs";
import { ExecRuntime } from "../lib/exec/runtime.mjs";
import { git as gitIn, makeRepo, stubAdapter, testEnv } from "./fixtures/exec/helpers.mjs";
import { LEGACY_POLICY } from "./helpers/legacy_policy.mjs";

async function verifiedRun(extraEnv = {}) {
  const repo = makeRepo();
  const env = testEnv({ extra: extraEnv });
  const rt = new ExecRuntime({ env, adapters: { stub: stubAdapter({ scenario: { edits: [{ path: "calc.txt", content: "fixed\n" }], finalMessage: "fixed calc" } }) } });
  const { run_id } = await rt.achieve({ repo_path: repo, task: "Fix calc so it says fixed", checks: ["node check.mjs"], scope: ["calc.txt"], executor: "stub", model: "m1" });
  assert.equal((await rt.runResult(run_id, 30)).state, "verified");
  return { repo, env, rt, run_id };
}

test("exec apply: the run is applied with an undo point, and undo puts calc.txt back", async () => {
  const { repo, env, rt, run_id } = await verifiedRun();
  const before = { log: gitIn(repo, "log", "--oneline"), stash: gitIn(repo, "stash", "list") };
  const r = await applyWithUndo(rt, run_id, { env });
  assert.equal(r.applied, true);
  assert.match(r.undo_point, /^u-\d{14}-[0-9a-f]{6}$/);
  assert.equal(r.undo_command, "stealthlab-mcp undo");
  assert.equal(read(repo, "calc.txt"), "fixed\n");
  assert.deepEqual({ log: gitIn(repo, "log", "--oneline"), stash: gitIn(repo, "stash", "list") }, before, "nothing on the user's branch or stash");
  const u = undo({ repo, env });
  assert.deepEqual(u.restored, ["calc.txt"]);
  assert.equal(read(repo, "calc.txt"), "broken\n");
});

test("exec apply: a file edited since the run started is not overwritten and leaves no undo point; an unknown run id writes nothing", async () => {
  const { repo, env, rt, run_id } = await verifiedRun();
  fs.writeFileSync(path.join(repo, "calc.txt"), "my own edit\n");                   // the user edits the same file during the run
  await assert.rejects(applyWithUndo(rt, run_id, { env }), /changed since the run started/);
  assert.equal(read(repo, "calc.txt"), "my own edit\n");
  assert.deepEqual(listUndoPoints({ repo, env }), []);
  await assert.rejects(applyWithUndo(rt, "r-20260101000000-abc123", { env }), /unknown run/);
});

test("exec apply: STEALTHLAB_DISPATCH_UNDO=off applies without a point and says why", async () => {
  const { repo, env, rt, run_id } = await verifiedRun();
  const r = await applyWithUndo(rt, run_id, { env: { ...env, STEALTHLAB_DISPATCH_UNDO: "off" } });
  assert.equal(r.applied, true);
  assert.equal(r.undo_point, null);
  assert.match(r.undo_note, /switched off/);
  assert.deepEqual(listUndoPoints({ repo, env }), []);
});

test("the real CLI: stealthlab-mcp exec apply <run> in another process prints the undo point, and stealthlab-mcp undo restores", async () => {
  const { repo, env, run_id } = await verifiedRun();
  const proc = spawnSync(process.execPath, [BIN, "exec", "apply", run_id], { cwd: repo, encoding: "utf8", env });
  assert.equal(proc.status, 0, proc.stderr);
  const printed = JSON.parse(proc.stderr.trim().split("\n").pop());
  assert.equal(printed.applied, true);
  assert.match(printed.undo_point, /^u-/);
  assert.equal(read(repo, "calc.txt"), "fixed\n");
  const back = spawnSync(process.execPath, [BIN, "undo"], { cwd: repo, encoding: "utf8", env });
  assert.equal(back.status, 0, back.stdout + back.stderr);
  assert.match(back.stdout, /undone .*restored calc\.txt/);
  assert.equal(read(repo, "calc.txt"), "broken\n");
}, { timeout: 120000 });
