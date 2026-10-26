import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { CLAUDE_MD_TEXT, claudeMdPath, installClaudeMd, uninstallClaudeMd } from "../lib/claude_md.mjs";
import { BEGIN, END } from "../lib/instructions.mjs";
import { maybeStartSurvey } from "../lib/auto_survey.mjs";
import { dropExactDuplicates, autoTidy } from "../lib/library_tidy.mjs";
import { addEntry, loadLibrary } from "../lib/library.mjs";
import { clients, installClaudeDispatch } from "../lib/clients.mjs";

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "kell.mjs");

// ---- CLAUDE.md block ---------------------------------------------------------------------------------------------

test("CLAUDE.md block: created once, a re-install does not duplicate it, the user's own text stays, uninstall removes it", () => {
  const dir = tmp("cmd-");
  const settings = path.join(dir, "settings.json");
  const md = claudeMdPath(settings);
  fs.writeFileSync(md, "# My project rules\n\nUse tabs.\n");
  assert.equal(installClaudeMd(settings, {}), "updated");
  assert.equal(installClaudeMd(settings, {}), "unchanged");
  assert.equal(installClaudeMd(settings, {}), "unchanged");
  const text = fs.readFileSync(md, "utf8");
  assert.equal(text.split(BEGIN).length - 1, 1);
  assert.equal(text.split(END).length - 1, 1);
  assert.ok(text.includes("Use tabs.") && text.includes(CLAUDE_MD_TEXT.split("\n")[0]));
  assert.equal(uninstallClaudeMd(settings), "removed");
  assert.equal(fs.readFileSync(md, "utf8").trim(), "# My project rules\n\nUse tabs.");
});

test("CLAUDE.md block: a file we created alone is deleted again; the switch turns it off; absent is absent", () => {
  const dir = tmp("cmd-");
  const settings = path.join(dir, "settings.json");
  assert.equal(installClaudeMd(settings, { STEALTHLAB_CLAUDE_MD: "off" }), "off");
  assert.equal(fs.existsSync(claudeMdPath(settings)), false);
  assert.equal(installClaudeMd(settings, {}), "created");
  assert.equal(uninstallClaudeMd(settings), "deleted");
  assert.equal(fs.existsSync(claudeMdPath(settings)), false);
  assert.equal(uninstallClaudeMd(settings), "absent");
});

test("installClaudeDispatch writes the block once; the Claude Code client's uninstall takes it out with the hooks", () => {
  const dir = tmp("cmd-");
  const env = { STEALTHLAB_HOME: path.join(dir, "home"), CLAUDE_CONFIG_DIR: path.join(dir, "claude"), STEALTHLAB_TEST_HOME: dir };
  const settingsFile = path.join(env.CLAUDE_CONFIG_DIR, "settings.json");
  const launch = { command: "node", args: ["kell.mjs"] };
  installClaudeDispatch({ settingsFile, launch, env });
  installClaudeDispatch({ settingsFile, launch, env });
  const md = claudeMdPath(settingsFile);
  assert.equal(fs.readFileSync(md, "utf8").split(BEGIN).length - 1, 1);
  // a stand-in `claude` first on PATH: the real one must never be asked to remove anything from this machine
  const bin = tmp("fakeclaude-");
  if (process.platform === "win32") fs.writeFileSync(path.join(bin, "claude.cmd"), "@exit /b 0\r\n");
  else { fs.writeFileSync(path.join(bin, "claude"), "#!/bin/sh\nexit 0\n"); fs.chmodSync(path.join(bin, "claude"), 0o755); }
  const saved = process.env.PATH;
  process.env.PATH = `${bin}${path.delimiter}${saved}`;
  try {
    clients({ env }).find((c) => c.id === "claude-code").uninstall();
  } finally {
    process.env.PATH = saved;
  }
  assert.equal(fs.existsSync(md), false);
});

// ---- --keep-mcp -------------------------------------------------------------------------------------------------

// the dry run prints what it would do (to stderr) and changes nothing
function dryRun(extra) {
  const r = spawnSync(process.execPath, [BIN, "install", "--client", "claude-code", "--with-exec", "--dry-run", "--url", "https://x.example/mcp", ...extra],
    { encoding: "utf8", env: { ...process.env, STEALTHLAB_HOME: tmp("kmcp-") } });
  return `${r.stdout || ""}${r.stderr || ""}`;
}

test("--keep-mcp: the CLI accepts the flag, and the dry run says the MCP server stays beside the hooks", () => {
  assert.match(dryRun(["--keep-mcp"]), /and the MCP server/);
  assert.match(dryRun([]), /hooks only/);
});

// ---- first survey ---------------------------------------------------------------------------------------------

function gitRepo() {
  const root = tmp("svy-");
  execFileSync("git", ["-C", root, "init", "-q"]);
  return root;
}

test("auto survey: the first prompt starts one detached survey; the next prompts do not; the marker survives", () => {
  const root = gitRepo();
  const started = [];
  const run = () => maybeStartSurvey({ cwd: root, env: { STEALTHLAB_AUTO_SURVEY: "on" }, start: (r) => started.push(r) });
  const a = run();
  assert.equal(a.started, true);
  assert.equal(started.length, 1);
  assert.ok(fs.existsSync(path.join(root, ".kell", ".auto-survey")));
  assert.deepEqual(run(), { started: false, reason: "already attempted", root: a.root });
  assert.equal(started.length, 1);
});

test("auto survey: not for an already surveyed repo, a non-repo, the off switch; a start that throws never throws", () => {
  const root = gitRepo();
  fs.mkdirSync(path.join(root, ".kell"));
  fs.writeFileSync(path.join(root, ".kell", "claims.md"), "x");
  assert.equal(maybeStartSurvey({ cwd: root, env: { STEALTHLAB_AUTO_SURVEY: "on" }, start: () => assert.fail("must not start") }).reason, "already surveyed");
  assert.equal(maybeStartSurvey({ cwd: tmp("plain-"), env: { STEALTHLAB_AUTO_SURVEY: "on" }, repoRootImpl: () => null }).reason, "not a git repo");
  assert.equal(maybeStartSurvey({ cwd: gitRepo(), env: { STEALTHLAB_AUTO_SURVEY: "off" } }).reason, "off");
  const r = maybeStartSurvey({ cwd: gitRepo(), env: { STEALTHLAB_AUTO_SURVEY: "on" }, start: () => { throw new Error("boom"); } });
  assert.equal(r.started, false);
});

test("auto survey: under node --test an unset switch is off", () => {
  assert.equal(maybeStartSurvey({ cwd: gitRepo(), env: {} }).reason, "off");
});

// ---- tidy: exact duplicates only ---------------------------------------------------------------------------------

const DIFF_A = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n";
const DIFF_B = DIFF_A.replace("x = 2", "x = 3");

function libRepo() {
  const root = tmp("lib-");
  execFileSync("git", ["-C", root, "init", "-q"]);
  fs.writeFileSync(path.join(root, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(root, ".kell"));
  return root;
}
const add = (root, title, diff, day) => addEntry(root, { title, check: "python -m pytest -q", observed: true, diff }, { now: new Date(`2026-10-${day}T00:00:00Z`) });

test("tidy --exact: the same title, check and diff recorded twice -> the newer stays, the older is dropped and archived", () => {
  const root = libRepo();
  const old = add(root, "Fix the calc constant", DIFF_A, "01");
  const fresh = add(root, "fix  the calc constant", DIFF_A, "05");
  const dropped = dropExactDuplicates(root);
  assert.deepEqual(dropped, [{ dropped: old.id, kept: fresh.id }]);
  assert.deepEqual(loadLibrary(root).entries.map((e) => e.id), [fresh.id]);
});

test("tidy --exact: same title with a different diff, a similar title, and entries with no diff are all left alone", () => {
  const root = libRepo();
  add(root, "Fix the calc constant", DIFF_A, "01");
  add(root, "Fix the calc constant", DIFF_B, "02");           // same title, different change
  add(root, "Fix the calc constants", DIFF_A, "03");           // similar title, same diff: a judgement call, not exact
  addEntry(root, { title: "Note without a diff", outcome: "pass", verify: false });
  assert.deepEqual(dropExactDuplicates(root), []);
  assert.equal(loadLibrary(root).entries.length, 4);
});

test("auto tidy: on by default, the switch turns it off, and it never throws", () => {
  const root = libRepo();
  add(root, "Fix the calc constant", DIFF_A, "01");
  add(root, "Fix the calc constant", DIFF_A, "05");
  assert.equal(autoTidy(root, { STEALTHLAB_LIBRARY_AUTOTIDY: "off" }).length, 0);
  assert.equal(loadLibrary(root).entries.length, 2);
  assert.equal(autoTidy(root, {}).length, 1);
  assert.equal(loadLibrary(root).entries.length, 1);
  assert.deepEqual(autoTidy(path.join(os.tmpdir(), "no-such-repo-xyz"), {}), []);
});
