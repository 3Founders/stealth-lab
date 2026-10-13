import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { installRefusal, isInstallCommand, linkedDeps, shellCommands } from "../lib/agent/installs.mjs";
import { runAgent, runTool } from "../lib/agent/step_agent.mjs";
import stealth from "../lib/executors/stealth.mjs";
import { normaliseProfile } from "../lib/executors/profile.mjs";
import { createWorktree, removeWorktree } from "../lib/exec/worktree.mjs";

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const link = (target, at) => fs.symlinkSync(target, at, process.platform === "win32" ? "junction" : "dir");

// a worktree dir whose node_modules (and optionally .venv) are links to a real project directory
function linked(...names) {
  const real = tmp("ig-real-");
  const cwd = tmp("ig-wt-");
  for (const n of names) { const target = path.join(real, n); fs.mkdirSync(target); link(target, path.join(cwd, n)); }
  return { cwd, real };
}

test("isInstallCommand: the install forms of npm, pnpm, yarn, bun, pip, python -m pip, uv, poetry; flags, sudo, env words and chains", () => {
  for (const c of ["npm install", "npm i", "npm ci", "npm install left-pad", "npm i -D vitest", "pnpm add zod", "pnpm install --frozen-lockfile",
    "yarn", "yarn install", "yarn add react", "yarn --frozen-lockfile", "bun add x", "npm --prefix web install", "npm install-test", "npm -g install x",
    "pip install requests", "pip3 install -r requirements.txt", "python -m pip install x", "python3 -m pip install x", "py -m pip install x",
    "pip --quiet install x", "uv pip install x", "uv add x", "uv sync", "poetry add x", "pipenv install",
    "sudo npm install", "CI=1 npm install", "env FOO=bar pip install x", "cd web && npm install", "make build; pip install x",
    "echo hi | npm i", "npm test\nnpm install"]) assert.equal(isInstallCommand(c), true, c);
});

test("isInstallCommand: running, listing and merely mentioning are not installs", () => {
  for (const c of ["npm test", "npm run build", "npm ls", "npm view install", "pip list", "pip show x", "python -m pytest -q", "python script.py install",
    "echo npm install", "grep -r 'pip install' .", "yarn test", "uv run pytest", "pnpm run lint", "# npm install", "node install.js", "ls node_modules", "git log"]) {
    assert.equal(isInstallCommand(c), false, c);
  }
  assert.deepEqual(shellCommands("CI=1 npm i && sudo pip install x; echo done"), ["npm i", "pip install x", "echo done"]);
});

test("installRefusal: only while a dependency directory is a link; the message says why and what to do", () => {
  const { cwd } = linked("node_modules");
  assert.deepEqual(linkedDeps(cwd), ["node_modules"]);
  const msg = installRefusal(cwd, "npm install left-pad");
  assert.match(msg, /^package installs are refused here: node_modules in this worktree is a link to the project's own/);
  assert.match(msg, /would change the real dependencies/);
  assert.match(msg, /finish and say which package the task needs/);
  assert.equal(installRefusal(cwd, "npm test"), null, "not an install");
  assert.equal(installRefusal(cwd, "npm install x", { enabled: false }), null, "switched off");
  assert.equal(installRefusal(tmp("ig-plain-"), "npm install x"), null, "no links, no refusal");
  const real = tmp("ig-realdir-");
  fs.mkdirSync(path.join(real, "node_modules"));
  assert.equal(installRefusal(real, "npm install x"), null, "a REAL node_modules in a plain checkout is not a link");
  assert.match(installRefusal(linked("node_modules", ".venv").cwd, "pip install x"), /node_modules and \.venv in this worktree are links/);
});

test("the shell tool refuses an install with the error and does NOT run it; other commands run; with no links an install is allowed", async () => {
  const { cwd } = linked("node_modules");
  await assert.rejects(runTool("run_shell", { command: "npm install & echo ran > ran.txt" }, { cwd, env: {} }), /package installs are refused here/);
  await assert.rejects(runTool("run_shell", { command: "pip install requests && echo ran > ran.txt" }, { cwd, env: {} }), /refused/);
  assert.equal(fs.existsSync(path.join(cwd, "ran.txt")), false, "the install line never ran");
  const ok = await runTool("run_shell", { command: "echo fine" }, { cwd, env: { PATH: process.env.PATH } });
  assert.match(ok, /fine/);
  const plain = tmp("ig-plain-");
  const out = await runTool("run_shell", { command: "pip install --help" }, { cwd: plain, env: { PATH: process.env.PATH } });
  assert.doesNotMatch(String(out), /refused/, "without links the install form is allowed to run (it only shows help here)");
  const off = await runTool("run_shell", { command: "npm install --help" }, { cwd, env: { PATH: process.env.PATH }, blockInstalls: false });
  assert.doesNotMatch(String(off), /refused/);
});

// ---- inside the agent loop and the executor ----------------------------------------------------------------------------------------

function provider(script) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push(JSON.parse(body));
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ choices: [{ message: script(seen.length) }], usage: { prompt_tokens: 10, completion_tokens: 5 } }));
    });
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r({ seen, url: `http://127.0.0.1:${server.address().port}/v1`, close: () => server.close() })));
}
const call = (id, name, args) => ({ id, type: "function", function: { name, arguments: JSON.stringify(args) } });
const turn = (...c) => ({ role: "assistant", content: null, tool_calls: c });

test("agent: the refusal reaches the model as a tool error", async () => {
  const { cwd } = linked("node_modules");
  const steps = [turn(call("1", "run_shell", { command: "npm install lodash" })), turn(call("2", "finish", { summary: "done without it" }))];
  const p = await provider((n) => steps[n - 1]);
  try {
    const out = await runAgent({ task: "t", base_url: p.url, model_id: "m" }, { cwd, env: { OPEN_MODEL_API_KEY: "sk-test-key-123456" }, sleep: async () => {} });
    assert.equal(out.stop, "finished");
    const toolMsg = p.seen[1].messages.find((m) => m.role === "tool").content;
    assert.match(toolMsg, /^error: package installs are refused here/);
  } finally { p.close(); }
});

test("agent: block_installs=false lets the install through to the shell", async () => {
  const { cwd } = linked("node_modules");
  const steps = [turn(call("1", "run_shell", { command: "npm install --help" })), turn(call("2", "finish", { summary: "ok" }))];
  const p = await provider((n) => steps[n - 1]);
  try {
    await runAgent({ task: "t", base_url: p.url, model_id: "m", block_installs: false }, { cwd, env: { OPEN_MODEL_API_KEY: "sk-test-key-123456", PATH: process.env.PATH }, sleep: async () => {} });
    const toolMsg = p.seen[1].messages.find((m) => m.role === "tool").content;
    assert.doesNotMatch(toolMsg, /refused/);
  } finally { p.close(); }
});

test("the stealth executor passes block_installs=false only when STEALTHLAB_EXEC_BLOCK_INSTALLS=off", () => {
  const profile = normaliseProfile("m", { key_env: "K", openai_base_url: "https://api.example.com/v1", model_id: "m" });
  const input = (env) => JSON.parse(stealth.buildCommand({ task: "t", model: "m", worktree: ".", timeoutS: 10, env: { K: "k", PATH: process.env.PATH, ...env }, profile }).stdinText);
  assert.equal(input({}).block_installs, undefined);
  assert.equal(input({ STEALTHLAB_EXEC_BLOCK_INSTALLS: "off" }).block_installs, false);
});

test("with the real worktree manager: a worktree built with links refuses an install, and removing it leaves the project's dependencies", async () => {
  const repo = tmp("ig-repo-");
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe", encoding: "utf8" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, ".gitignore"), "node_modules\n");
  fs.writeFileSync(path.join(repo, "a.js"), "1");
  g("add", "-A"); g("commit", "-q", "-m", "i");
  fs.mkdirSync(path.join(repo, "node_modules", "pkg"), { recursive: true });
  fs.writeFileSync(path.join(repo, "node_modules", "pkg", "index.js"), "1");
  const dir = path.join(tmp("ig-wt2-"), "wt");
  await createWorktree({ repo, commit: g("rev-parse", "HEAD").trim(), dir, patch: null, env: process.env });
  assert.ok(linkedDeps(dir).length > 0, "the worktree has the link");
  assert.match(installRefusal(dir, "npm install"), /refused/);
  await removeWorktree({ repo, dir, env: process.env });
  assert.ok(fs.existsSync(path.join(repo, "node_modules", "pkg", "index.js")));
});
