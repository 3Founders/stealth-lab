import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { baselineKey, readBaseline, writeBaseline } from "../lib/baseline_cache.mjs";

function repo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "base-repo-"));
  const g = (...a) => execFileSync("git", ["-C", dir, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(dir, "a.py"), "x = 1\n");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { dir, g };
}

test("the key follows everything the result depends on: commit, uncommitted and untracked changes, check, Python", () => {
  const { dir, g } = repo();
  const env = {};
  const k0 = baselineKey(dir, "python -m pytest -q", env);
  assert.match(k0, /^[0-9a-f]{32}$/);
  assert.equal(baselineKey(dir, "python -m pytest -q", env), k0, "same state, same key");
  assert.notEqual(baselineKey(dir, "python -m pytest -q -x", env), k0, "another check");
  assert.notEqual(baselineKey(dir, "python -m pytest -q", { VIRTUAL_ENV: "/v" }), k0, "another Python");
  fs.writeFileSync(path.join(dir, "a.py"), "x = 2\n");
  const k1 = baselineKey(dir, "python -m pytest -q", env);
  assert.notEqual(k1, k0, "an uncommitted edit");
  fs.writeFileSync(path.join(dir, "new.py"), "y = 1\n");
  const k2 = baselineKey(dir, "python -m pytest -q", env);
  assert.notEqual(k2, k1, "an untracked file");
  fs.writeFileSync(path.join(dir, "new.py"), "y = 2\n");
  assert.notEqual(baselineKey(dir, "python -m pytest -q", env), k2, "an untracked file's content");
  fs.rmSync(path.join(dir, "new.py")); fs.writeFileSync(path.join(dir, "a.py"), "x = 1\n");
  assert.equal(baselineKey(dir, "python -m pytest -q", env), k0, "back to the committed state");
  g("commit", "--allow-empty", "-q", "-m", "next");
  assert.notEqual(baselineKey(dir, "python -m pytest -q", env), k0, "another commit");
  assert.equal(baselineKey(fs.mkdtempSync(path.join(os.tmpdir(), "not-git-")), "x", env), null, "no git checkout: no key");
});

test("a finished run is kept and read back; a stopped one (no exit code) or the switch off is not", () => {
  const env = { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "base-home-")) };
  assert.equal(readBaseline("k1", env), null);
  assert.equal(writeBaseline("k1", { code: 1, out: "FAILED t.py::test_a\n" }, env), true);
  assert.deepEqual(readBaseline("k1", env), { code: 1, out: "FAILED t.py::test_a\n" });
  assert.equal(writeBaseline("k2", { code: null, out: "" }, env), false, "timed out or stopped");
  assert.equal(readBaseline("k2", env), null);
  assert.equal(readBaseline("k1", { ...env, STEALTHLAB_BASELINE_CACHE: "off" }), null);
  assert.equal(readBaseline(null, env), null);
});

test("baselineHealth: pytest's count on the unchanged code, bare (-q) or between bars; weak when a third or more fail", async () => {
  const { baselineHealth } = await import("../lib/dispatch.mjs");
  assert.deepEqual(baselineHealth("FAILED a::b\n36 failed, 971 passed, 54 skipped, 125 subtests passed in 206.34s (0:03:26)\n"),
    { failed: 36, passed: 971, weak: false });
  assert.deepEqual(baselineHealth("===== 417 failed, 590 passed, 2 errors in 150.2s =====\n"), { failed: 419, passed: 590, weak: true });
  assert.equal(baselineHealth("collected 0 items"), null);
});

test("the suite's last duration is kept per repository and check, whatever the checkout's state", async () => {
  const { recordSuiteSecs, suiteSecs } = await import("../lib/baseline_cache.mjs");
  const env = { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "suite-home-")) };
  assert.equal(suiteSecs("/r", "pytest", env), null);
  assert.equal(recordSuiteSecs("/r", "pytest", 210.4, env), true);
  assert.equal(recordSuiteSecs("/r", "pytest -x", 12, env), true);
  assert.equal(suiteSecs("/r", "pytest", env), 210);
  assert.equal(suiteSecs("/r", "pytest -x", env), 12);
  assert.equal(recordSuiteSecs("/r", "pytest", NaN, env), false);
});

test("kell's own notes in .kell/ do not change the key: they change on every dispatch and no test reads them", () => {
  const { dir } = repo();
  const k0 = baselineKey(dir, "pytest", {});
  fs.mkdirSync(path.join(dir, ".kell", "goals"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".kell", "goals", "G-1.md"), "GOAL|x\n");
  assert.equal(baselineKey(dir, "pytest", {}), k0);
});
