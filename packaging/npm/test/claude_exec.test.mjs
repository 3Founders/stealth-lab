// Spec section 8 items 11 (installer), 13 (Windows paths, installer side), 15 (--help parity).
// Every test runs against a temp home: CLAUDE_CONFIG_DIR / STEALTHLAB_TEST_HOME / STEALTHLAB_HOME /
// HOME / USERPROFILE all point into a mkdtemp dir, and the CLI test puts a fake `claude` first and
// alone on PATH, so the real ~/.claude and the real `claude` CLI are never reached.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  AGENT_NAMES, EXEC_HOOKS, MANAGED_MARK, execPaths, installExec, removeExecHooks, renderAgent, shellJoin,
  uninstallExec, upsertExecHooks,
} from "../lib/claude_exec.mjs";
import { upsertClaudeHook, removeClaudeHook } from "../lib/clients.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.join(HERE, "..", "bin", "stealthlab-mcp.mjs");
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "slexec-"));
const readJson = (f) => JSON.parse(fs.readFileSync(f, "utf8"));

// A user's settings.json with hooks of their own, including a UserPromptSubmit hook and a
// SubagentStop hook on the very events we add to.
const USER_SETTINGS = {
  model: "opus",
  permissions: { allow: ["Bash(npm test:*)"] },
  hooks: {
    UserPromptSubmit: [{ hooks: [{ type: "command", command: "my-prompt-logger", timeout: 5 }] }],
    SubagentStop: [{ matcher: "db-agent", hooks: [{ type: "command", command: "./scripts/cleanup-db-connection.sh" }] }],
    PreToolUse: [{ matcher: "Bash", hooks: [{ type: "command", command: "guard.sh" }] }],
  },
};

function tempEnv() {
  const home = tmp();
  return { home, env: { CLAUDE_CONFIG_DIR: path.join(home, ".claude"), STEALTHLAB_TEST_HOME: home, STEALTHLAB_HOME: path.join(home, ".stealthlab") } };
}

const LAUNCH = { command: "/usr/bin/node", args: ["/opt/stealthlab-mcp/bin/stealthlab-mcp.mjs"] };

// Pull a top-level scalar/flow value out of the YAML frontmatter (enough for our own templates).
function frontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/.exec(text);
  assert.ok(m, "file starts with a --- frontmatter block");
  const keys = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z][\w]*):\s*(.*)$/.exec(line);
    if (kv) keys[kv[1]] = kv[2];
  }
  return { keys, raw: m[1], body: text.slice(m[0].length) };
}

test("installExec/uninstallExec: other hooks (incl. UserPromptSubmit) survive; settings semantically restored", () => {
  const { env } = tempEnv();
  const { settings } = execPaths(env);
  fs.mkdirSync(path.dirname(settings), { recursive: true });
  fs.writeFileSync(settings, JSON.stringify(USER_SETTINGS, null, 4));   // user's own formatting

  installExec({ env, launch: LAUNCH });
  installExec({ env, launch: LAUNCH });   // idempotent: re-install replaces, never duplicates
  const mid = readJson(settings);
  assert.deepEqual(mid.hooks.UserPromptSubmit, USER_SETTINGS.hooks.UserPromptSubmit);
  assert.deepEqual(mid.hooks.PreToolUse, USER_SETTINGS.hooks.PreToolUse);
  assert.equal(mid.hooks.SubagentStart.length, 1);
  assert.equal(mid.hooks.SubagentStop.length, 2);
  assert.deepEqual(mid.hooks.SubagentStop[0], USER_SETTINGS.hooks.SubagentStop[0]);
  assert.deepEqual(mid.hooks.SubagentStop[1], {
    hooks: [{ type: "command", command: "/usr/bin/node /opt/stealthlab-mcp/bin/stealthlab-mcp.mjs hook subagent-stop", timeout: 15 }],
  });
  assert.equal(mid.hooks.SubagentStart[0].hooks[0].command,
    "/usr/bin/node /opt/stealthlab-mcp/bin/stealthlab-mcp.mjs hook subagent-start");
  assert.equal(mid.model, "opus");
  assert.ok(fs.existsSync(`${settings}.bak`));

  const r = uninstallExec({ env });
  assert.equal(r.hooks, true);
  assert.equal(r.agents.length, 2);
  assert.deepEqual(readJson(settings), USER_SETTINGS);
  assert.equal(uninstallExec({ env }).changed, false);   // second uninstall is a no-op
});

test("exec hooks and the knowledge hook coexist and are removed independently", () => {
  const { env } = tempEnv();
  const { settings } = execPaths(env);
  fs.mkdirSync(path.dirname(settings), { recursive: true });
  fs.writeFileSync(settings, JSON.stringify(USER_SETTINGS));
  upsertClaudeHook(settings, { ...LAUNCH, args: [...LAUNCH.args, "hook-prompt"] });
  upsertExecHooks(settings, LAUNCH);
  let doc = readJson(settings);
  assert.equal(doc.hooks.UserPromptSubmit.length, 2);
  assert.equal(removeExecHooks(settings), true);
  doc = readJson(settings);
  assert.equal(doc.hooks.UserPromptSubmit.length, 2, "exec uninstall leaves the knowledge hook");
  assert.equal(removeClaudeHook(settings), true);
  assert.deepEqual(readJson(settings), USER_SETTINGS);
});

test("no settings.json before: created 0600 with only our hooks; uninstall leaves an empty object", () => {
  const { env } = tempEnv();
  const { settings } = execPaths(env);
  installExec({ env, launch: LAUNCH });
  assert.deepEqual(Object.keys(readJson(settings).hooks).sort(), Object.keys(EXEC_HOOKS).sort());
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(settings).mode & 0o777, 0o600);
    for (const f of Object.values(execPaths(env).agents)) assert.equal(fs.statSync(f).mode & 0o777, 0o600);
  }
  uninstallExec({ env });
  assert.deepEqual(readJson(settings), {});
});

test("unparseable settings.json: refused before anything is written", () => {
  const { env } = tempEnv();
  const { settings, agents } = execPaths(env);
  fs.mkdirSync(path.dirname(settings), { recursive: true });
  fs.writeFileSync(settings, "{ // jsonc comment\n}");
  assert.throws(() => installExec({ env, launch: LAUNCH }), /not valid JSON/);
  assert.equal(fs.readFileSync(settings, "utf8"), "{ // jsonc comment\n}");
  for (const f of Object.values(agents)) assert.equal(fs.existsSync(f), false);
});

test("agent files: written with verified frontmatter keys, removed on uninstall; a user's own file is never touched", () => {
  const { env } = tempEnv();
  const { agents } = execPaths(env);
  installExec({ env, launch: LAUNCH });

  const ex = frontmatter(fs.readFileSync(agents["stealth-executor"], "utf8"));
  assert.equal(ex.keys.name, "stealth-executor");
  assert.equal(ex.keys.isolation, "worktree");
  assert.equal(ex.keys.maxTurns, "40");
  assert.equal(ex.keys.tools, "Read, Edit, Write, Bash, Grep, Glob");
  assert.match(ex.keys.description, /one node/i);
  assert.match(ex.raw, new RegExp(`# ${MANAGED_MARK}`));
  assert.match(ex.body, /STEALTH_RESULT node=<node_id> cwd=/);
  assert.match(ex.body, /check=/);

  const de = frontmatter(fs.readFileSync(agents["stealth-delegator"], "utf8"));
  assert.equal(de.keys.name, "stealth-delegator");
  assert.match(de.keys.tools, /mcp__stealthlab-exec__achieve/);
  assert.match(de.keys.tools, /mcp__stealthlab-exec__run_result/);
  assert.doesNotMatch(de.keys.tools, /apply_run/);
  assert.equal(de.keys.disallowedTools, "mcp__stealthlab-exec__apply_run");
  assert.match(de.raw, /mcpServers:\n {2}- stealthlab-exec:\n {6}type: stdio\n {6}command: "\/usr\/bin\/node"\n {6}args: \["\/opt\/stealthlab-mcp\/bin\/stealthlab-mcp.mjs","exec"\]/);
  assert.doesNotMatch(de.raw, /\{\{/);
  assert.doesNotMatch(de.body, /\{\{/);

  // A same-named agent the user wrote: install refuses, uninstall leaves it.
  uninstallExec({ env });
  for (const f of Object.values(agents)) assert.equal(fs.existsSync(f), false);
  fs.writeFileSync(agents["stealth-executor"], "---\nname: stealth-executor\ndescription: mine\n---\nmine\n");
  assert.throws(() => installExec({ env, launch: LAUNCH }), /not written by stealthlab-mcp/);
  assert.equal(fs.existsSync(agents["stealth-delegator"]), false, "all-or-nothing: no partial agent install");
  uninstallExec({ env });
  assert.match(fs.readFileSync(agents["stealth-executor"], "utf8"), /description: mine/);
});

test("Windows launch paths: quoted in the hook command, JSON-escaped in the agent YAML, still recognised on uninstall", () => {
  const launch = { command: "C:\\Program Files\\nodejs\\node.exe", args: ["C:\\Users\\A B\\AppData\\Roaming\\npm\\node_modules\\stealthlab-mcp\\bin\\stealthlab-mcp.mjs"] };
  assert.equal(shellJoin({ command: launch.command, args: [...launch.args, "hook", "subagent-stop"] }),
    '"C:\\Program Files\\nodejs\\node.exe" "C:\\Users\\A B\\AppData\\Roaming\\npm\\node_modules\\stealthlab-mcp\\bin\\stealthlab-mcp.mjs" hook subagent-stop');
  const fm = frontmatter(renderAgent("stealth-delegator", launch)).raw;
  const cmd = /command: (.*)/.exec(fm)[1];
  const args = /args: (.*)/.exec(fm)[1];
  assert.equal(JSON.parse(cmd), launch.command);   // a JSON string is a valid YAML double-quoted scalar
  assert.deepEqual(JSON.parse(args), [...launch.args, "exec"]);
  const body = renderAgent("stealth-delegator", launch);
  assert.ok(!body.includes("{{"), "every placeholder is filled");
  assert.ok(body.includes(`Apply with: ${shellJoin({ command: launch.command, args: [...launch.args, "exec", "apply"] })} <run_id>`));

  const { env } = tempEnv();
  const { settings } = execPaths(env);
  fs.mkdirSync(path.dirname(settings), { recursive: true });
  fs.writeFileSync(settings, JSON.stringify(USER_SETTINGS));
  installExec({ env, launch });
  // npx launch spec on Windows (cmd /c npx -y stealthlab-mcp@latest) is also recognised
  upsertExecHooks(settings, { command: "cmd", args: ["/c", "npx", "-y", "stealthlab-mcp@latest"] });
  assert.equal(readJson(settings).hooks.SubagentStop.length, 2, "re-install with another launch spec replaces ours");
  uninstallExec({ env });
  assert.deepEqual(readJson(settings), USER_SETTINGS);
});

// --- the real CLI, with a fake `claude` on PATH ---------------------------------------------------

function fakeClaudeDir() {
  const dir = tmp();
  if (process.platform === "win32") {
    fs.writeFileSync(path.join(dir, "claude.cmd"), '@echo %*>> "%~dp0claude.log"\r\n@exit /b 0\r\n');
  } else {
    const f = path.join(dir, "claude");
    fs.writeFileSync(f, '#!/bin/sh\necho "$@" >> "$(dirname "$0")/claude.log"\nexit 0\n');
    fs.chmodSync(f, 0o755);
  }
  return dir;
}

function cli(args, { home, fakeBin }) {
  const env = {
    PATH: fakeBin, HOME: home, USERPROFILE: home, CLAUDE_CONFIG_DIR: path.join(home, ".claude"),
    STEALTHLAB_TEST_HOME: home, STEALTHLAB_HOME: path.join(home, ".stealthlab"),
    APPDATA: path.join(home, "AppData", "Roaming"),
    ...(process.platform === "win32" ? { ComSpec: process.env.ComSpec, SystemRoot: process.env.SystemRoot } : {}),
  };
  const r = spawnSync(process.execPath, [BIN, ...args], { env, encoding: "utf8", timeout: 30000 });
  return { code: r.status, err: r.stderr };
}

test("CLI: default install writes no exec entries; install --with-exec then uninstall restores settings.json", () => {
  const home = tmp();
  const fakeBin = fakeClaudeDir();
  const settings = path.join(home, ".claude", "settings.json");
  fs.mkdirSync(path.dirname(settings), { recursive: true });
  fs.writeFileSync(settings, JSON.stringify(USER_SETTINGS, null, 2) + "\n");
  const before = readJson(settings);
  const agentsDir = path.join(home, ".claude", "agents");
  const url = ["--url", "https://mcp.example.test/mcp", "--client", "claude-code"];

  let r = cli(["install", ...url], { home, fakeBin });
  assert.equal(r.code, 0, r.err);
  let doc = readJson(settings);
  assert.deepEqual(doc.hooks.SubagentStop, USER_SETTINGS.hooks.SubagentStop, "default install: no exec hooks");
  assert.equal(doc.hooks.SubagentStart, undefined);
  assert.equal(fs.existsSync(agentsDir), false, "default install: no agent files");
  assert.equal(doc.hooks.UserPromptSubmit.length, 2, "default install still adds the knowledge hook");

  r = cli(["install", "--with-exec", ...url], { home, fakeBin });
  assert.equal(r.code, 0, r.err);
  assert.match(r.err, /ok {4}Executor layer/);
  doc = readJson(settings);
  assert.equal(doc.hooks.SubagentStart.length, 1);
  assert.equal(doc.hooks.SubagentStop.length, 2);
  assert.match(doc.hooks.SubagentStop[1].hooks[0].command, /stealthlab-mcp\.mjs"? hook subagent-stop$/);
  for (const n of AGENT_NAMES) assert.ok(fs.existsSync(path.join(agentsDir, `${n}.md`)));
  const during = doc;

  r = cli(["uninstall", "--client", "claude-code"], { home, fakeBin });
  assert.equal(r.code, 0, r.err);
  assert.match(r.err, /removed {2}Executor layer/);
  const after = readJson(settings);
  assert.deepEqual(after, before, "pre-existing hooks intact, ours gone");
  for (const n of AGENT_NAMES) assert.equal(fs.existsSync(path.join(agentsDir, `${n}.md`)), false);
  const log = fs.readFileSync(path.join(fakeBin, "claude.log"), "utf8");
  assert.match(log, /mcp add --scope user --transport http stealthlab https:\/\/mcp\.example\.test\/mcp/);

  if (process.env.STEALTHLAB_PRINT_SETTINGS_DIFF) {   // evidence for EXECUTOR_SUMMARY.md section (e)
    process.stdout.write(`\n--- before\n${JSON.stringify(before, null, 2)}\n--- after install --with-exec\n${JSON.stringify(during, null, 2)}\n--- after uninstall\n${JSON.stringify(after, null, 2)}\n`);
  }
});

test("CLI: --with-exec --dry-run writes nothing; --with-exec without Claude Code writes nothing", () => {
  const home = tmp();
  const fakeBin = fakeClaudeDir();
  let r = cli(["install", "--with-exec", "--dry-run", "--url", "https://h.test/mcp", "--client", "claude-code"], { home, fakeBin });
  assert.equal(r.code, 0, r.err);
  assert.match(r.err, /would write .*stealth-executor\.md/);
  assert.equal(fs.existsSync(path.join(home, ".claude")), false);
  r = cli(["install", "--with-exec", "--url", "https://h.test/mcp", "--client", "cursor"], { home, fakeBin });
  assert.equal(r.code, 0, r.err);
  assert.match(r.err, /Claude Code not selected/);
  assert.equal(fs.existsSync(path.join(home, ".claude")), false);
});

test("--help lists every dispatched subcommand (dispatch <-> help parity)", () => {
  const src = fs.readFileSync(BIN, "utf8");
  const cases = [...src.matchAll(/^\s*case "([\w-]+)":/gm)].map((m) => m[1]);
  assert.ok(cases.includes("exec") && cases.includes("hook") && cases.includes("config"));
  const r = spawnSync(process.execPath, [BIN, "--help"], { encoding: "utf8" });
  const help = r.stderr;
  for (const c of cases) {
    assert.match(help, new RegExp(`stealthlab-mcp (\\[)?${c}\\b`), `HELP is missing subcommand "${c}"`);
  }
  for (const ev of ["subagent-start", "subagent-stop"]) assert.match(help, new RegExp(`hook ${ev}`));
  const opts = [...src.matchAll(/^\s*"?([\w-]+)"?: \{ type: "(?:string|boolean)"/gm)].map((m) => m[1]);
  for (const o of opts) assert.match(help, new RegExp(`--${o}\\b`), `HELP is missing option --${o}`);
});

test("CLI: exec apply needs a run id, and refuses an unknown run with exit 1", () => {
  const home = tmp();
  const env = { ...process.env, STEALTHLAB_HOME: path.join(home, ".stealthlab"), STEALTHLAB_TEST_HOME: home };
  const none = spawnSync(process.execPath, [BIN, "exec", "apply"], { env, encoding: "utf8", timeout: 30000 });
  assert.equal(none.status, 2);
  const unknown = spawnSync(process.execPath, [BIN, "exec", "apply", "r-20261009120000-abc123"], { env, encoding: "utf8", timeout: 30000 });
  assert.equal(unknown.status, 1);
  assert.match(unknown.stderr, /unknown run/);
});

test("hook commands run through bash with Windows backslash paths (Claude Code on Windows uses Git Bash)", (t) => {
  const bash = spawnSync("bash", ["-c", "echo ok"], { encoding: "utf8" });
  if (bash.status !== 0) return t.skip("no bash on this machine");
  const dir = tmp();
  const script = path.join(dir, "hook probe.mjs");           // path.join: backslashes on Windows, and a space
  fs.writeFileSync(script, "process.stdout.write('ran:' + process.argv.slice(2).join(','))\n");
  const cmd = shellJoin({ command: process.execPath, args: [script, "hook", "prompt"] });
  assert.ok(cmd.includes(`"${script}"`), "a path with backslashes or spaces is quoted");
  const r = spawnSync("bash", ["-c", cmd], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, "ran:hook,prompt");
});
