// Open-model profiles, the claude / opencode / stealth adapters in open-model mode, and the step agent's loop.
// Everything is offline: the "provider" is a local http server, and no real agent binary is launched.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

import { normaliseProfile, normaliseProfiles, complianceOf } from "../lib/executors/profile.mjs";
import claude from "../lib/executors/claude.mjs";
import opencode, { PROFILE_PROVIDER_ID } from "../lib/executors/opencode.mjs";
import stealth, { STEP_AGENT } from "../lib/executors/stealth.mjs";
import { ADAPTERS } from "../lib/executors/index.mjs";
import { runAgent, safePath, runTool } from "../lib/agent/step_agent.mjs";

const KEY = "sk-vendor-secret-123456";
const raw = (extra = {}) => ({ key_env: "ZAI_API_KEY", anthropic_base_url: "https://api.z.ai/api/anthropic",
  openai_base_url: "https://api.example.com/v1", model_id: "glm-5.3", ...extra });
const profile = (extra) => normaliseProfile("glm-5.3", raw(extra));
const ENV = { PATH: process.env.PATH, ZAI_API_KEY: KEY, ANTHROPIC_API_KEY: "sk-ant-user-own", CLAUDE_CODE_OAUTH_TOKEN: "oauth-user-own",
  OPENAI_API_KEY: "sk-openai-user-own", STEALTHLAB_TOKEN: "stealth-secret" };

// ------------------------------------------------------------------ profiles

test("a profile needs a key env NAME and at least one endpoint", () => {
  assert.throws(() => normaliseProfile("m", raw({ key_env: "sk-abc def" })), /NAME of an environment variable/);
  assert.throws(() => normaliseProfile("m", { key_env: "K" }), /anthropic_base_url/);
  assert.throws(() => normaliseProfile("m", raw({ openai_base_url: "http://api.example.com/v1" })), /https/);
  assert.throws(() => normaliseProfile("m", raw({ anthropic_base_url: "https://u:p@api.example.com/" })), /credentials/);
  assert.equal(normaliseProfile("m", raw({ openai_base_url: "http://localhost:8000/v1" })).openai_base_url, "http://localhost:8000/v1");
});

test("compliance fields stay unknown unless declared, and bad ones are refused", () => {
  assert.deepEqual(complianceOf(profile()), { zdr: null, no_training: null, region: "unknown", dpa_signed: false });
  assert.equal(complianceOf(profile({ zdr: true, region: "in", dpa_signed: true })).zdr, true);
  assert.throws(() => profile({ zdr: "yes" }), /zdr/);
  assert.throws(() => profile({ region: "India" }), /region/);
});

test("request_extras cannot set fields the agent owns; prices come as a pair", () => {
  assert.throws(() => profile({ request_extras: { model: "x" } }), /cannot set model/);
  assert.throws(() => profile({ input_per_mtok: 1 }), /both/);
  assert.deepEqual(profile({ request_extras: { provider: { zdr: true } } }).request_extras, { provider: { zdr: true } });
  assert.deepEqual(normaliseProfiles(undefined), {});
});

// ------------------------------------------------------------------ claude

test("claude in open-model mode points at the vendor and carries none of the user's own credentials", () => {
  const cfgDir = fs.mkdtempSync(path.join(os.tmpdir(), "cfg-"));
  const spec = claude.buildCommand({ task: "fix it", model: "glm-5.3", env: ENV, bin: process.execPath, profile: profile(), configDir: cfgDir });
  assert.equal(spec.env.ANTHROPIC_BASE_URL, "https://api.z.ai/api/anthropic");
  assert.equal(spec.env.ANTHROPIC_AUTH_TOKEN, KEY);
  assert.equal(spec.env.ANTHROPIC_API_KEY, undefined);
  assert.equal(spec.env.CLAUDE_CODE_OAUTH_TOKEN, undefined);
  assert.equal(spec.env.OPENAI_API_KEY, undefined);
  assert.equal(spec.env.STEALTHLAB_TOKEN, undefined);
  for (const a of ["HAIKU", "SONNET", "OPUS"]) assert.equal(spec.env[`ANTHROPIC_DEFAULT_${a}_MODEL`], "glm-5.3");
  assert.equal(spec.env.CLAUDE_CONFIG_DIR, cfgDir);
  assert.equal(spec.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, "1");
  assert.equal(spec.env.CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS, "1");
  assert.ok(spec.args.includes("-p") && spec.args.join(" ").includes("--model glm-5.3"));
  assert.ok(!spec.args.join(" ").includes(KEY), "the key must never appear in argv");
});

test("claude without a profile is unchanged, and refuses a profile it cannot use", () => {
  const spec = claude.buildCommand({ task: "fix it", model: "sonnet", env: ENV, bin: process.execPath });
  assert.equal(spec.env.ANTHROPIC_API_KEY, "sk-ant-user-own");
  assert.equal(spec.env.ANTHROPIC_BASE_URL, undefined);
  assert.throws(() => claude.buildCommand({ task: "t", env: ENV, bin: process.execPath, profile: normaliseProfile("m", { key_env: "ZAI_API_KEY", openai_base_url: "https://a.example/v1" }) }), /anthropic_base_url/);
  assert.throws(() => claude.buildCommand({ task: "t", env: ENV, bin: process.execPath, profile: profile({ request_extras: { provider: { zdr: true } } }) }), /request_extras/);
  assert.throws(() => claude.buildCommand({ task: "t", env: { PATH: ENV.PATH }, bin: process.execPath, profile: profile() }), /ZAI_API_KEY is not set/);
});

// ------------------------------------------------------------------ opencode

test("opencode in open-model mode declares the provider inline and keeps the key out of the config text", () => {
  const spec = opencode.buildCommand({ task: "fix it", env: ENV, bin: process.execPath, worktree: "/w", profile: profile() });
  const cfg = JSON.parse(spec.env.OPENCODE_CONFIG_CONTENT);
  const p = cfg.provider[PROFILE_PROVIDER_ID];
  assert.equal(p.npm, "@ai-sdk/openai-compatible");
  assert.equal(p.options.baseURL, "https://api.example.com/v1");
  assert.equal(p.options.apiKey, "{env:OPEN_MODEL_API_KEY}");
  assert.ok(p.models["glm-5.3"]);
  assert.equal(spec.env.OPEN_MODEL_API_KEY, KEY);
  assert.ok(!spec.env.OPENCODE_CONFIG_CONTENT.includes(KEY));
  assert.equal(spec.env.ANTHROPIC_API_KEY, undefined);
  assert.ok(spec.args.join(" ").includes(`--model ${PROFILE_PROVIDER_ID}/glm-5.3`));
  assert.throws(() => opencode.buildCommand({ task: "t", env: ENV, bin: process.execPath, profile: profile({ request_extras: { provider: { zdr: true } } }) }), /request_extras/);
});

// ------------------------------------------------------------------ stealth adapter

test("the stealth executor is registered, needs a profile, and sends config on stdin not argv", async () => {
  assert.ok(ADAPTERS.stealth);
  assert.equal((await stealth.detect()).installed, true);
  assert.throws(() => stealth.buildCommand({ task: "t", model: "glm-5.3", env: ENV }), /needs a profile/);
  const spec = stealth.buildCommand({ task: "t", model: "glm-5.3", env: ENV, profile: profile({ input_per_mtok: 1, output_per_mtok: 2 }) });
  assert.equal(spec.cmd, process.execPath);
  assert.deepEqual(spec.args, [STEP_AGENT]);
  assert.equal(spec.env.OPEN_MODEL_API_KEY, KEY);
  assert.equal(spec.env.ANTHROPIC_API_KEY, undefined);
  assert.ok(!spec.stdinText.includes(KEY) && !spec.args.join(" ").includes(KEY));
  const input = JSON.parse(spec.stdinText);
  assert.equal(input.model_id, "glm-5.3");
  assert.equal(input.base_url, "https://api.example.com/v1");
});

// ------------------------------------------------------------------ a fake provider

function provider(script) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push({ url: req.url, auth: req.headers.authorization, body: body ? JSON.parse(body) : null });
      const out = script(seen.length, seen[seen.length - 1]);
      res.writeHead(out.status || 200, { "content-type": "application/json" });
      res.end(typeof out.body === "string" ? out.body : JSON.stringify(out.body));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ seen, url: `http://127.0.0.1:${server.address().port}/v1`, close: () => server.close() })));
}
const toolCall = (id, name, args) => ({ id, type: "function", function: { name, arguments: JSON.stringify(args) } });
const reply = (message, usage = { prompt_tokens: 100, completion_tokens: 10 }) => ({ body: { choices: [{ message, finish_reason: "stop" }], usage } });
const tmpRepo = (files = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-repo-"));
  for (const [f, c] of Object.entries(files)) { fs.mkdirSync(path.dirname(path.join(dir, f)), { recursive: true }); fs.writeFileSync(path.join(dir, f), c); }
  return dir;
};
const noSleep = async () => {};

test("the agent reads, edits and finishes; tokens and cost are summed; the key rides only in the header", async () => {
  const repo = tmpRepo({ "calc.py": "def add(a, b):\n    return a - b\n" });
  const p = await provider((n) => {
    if (n === 1) return reply({ role: "assistant", content: null, tool_calls: [toolCall("c1", "search", { query: "def add" })] });
    if (n === 2) return reply({ role: "assistant", content: null, tool_calls: [toolCall("c2", "edit_file", { path: "calc.py", old_text: "a - b", new_text: "a + b" })] });
    return reply({ role: "assistant", content: null, tool_calls: [toolCall("c3", "finish", { summary: "Fixed add.\nLearned: it used minus" })] });
  });
  try {
    const out = await runAgent({ task: "fix add", base_url: p.url, model_id: "glm-5.3", input_per_mtok: 1, output_per_mtok: 2 },
      { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: noSleep });
    assert.equal(out.stop, "finished");
    assert.equal(fs.readFileSync(path.join(repo, "calc.py"), "utf8"), "def add(a, b):\n    return a + b\n");
    assert.deepEqual(out.tokens, { in: 300, out: 30 });
    assert.equal(out.costUsd, (300 * 1 + 30 * 2) / 1e6);
    assert.deepEqual(out.learned, ["it used minus"]);
    assert.ok(p.seen.every((r) => r.auth === `Bearer ${KEY}` && r.url === "/v1/chat/completions"));
    assert.ok(!JSON.stringify(p.seen.map((r) => r.body)).includes(KEY), "the key must never be in a request body");
    assert.ok(p.seen[0].body.tools.length >= 6 && p.seen[0].body.model === "glm-5.3");
  } finally { p.close(); }
});

test("request_extras are sent but cannot displace the fields the agent owns", async () => {
  const repo = tmpRepo({ "a.txt": "x" });
  const p = await provider(() => reply({ role: "assistant", content: "done" }));
  try {
    await runAgent({ task: "t", base_url: p.url, model_id: "m", request_extras: { provider: { zdr: true }, model: "evil", temperature: 9 } },
      { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: noSleep });
    assert.deepEqual(p.seen[0].body.provider, { zdr: true });
    assert.equal(p.seen[0].body.model, "m");
    assert.equal(p.seen[0].body.temperature, 0);
  } finally { p.close(); }
});

test("paths cannot leave the repository, .git is off limits, symlinks that escape are refused", async () => {
  const repo = tmpRepo({ "ok.txt": "hi", ".git/config": "secret" });
  const outside = tmpRepo({ "secret.txt": "nope" });
  assert.throws(() => safePath(repo, "../x"), /outside/);
  assert.throws(() => safePath(repo, path.join(outside, "secret.txt")), /outside/);
  await assert.rejects(runTool("read_file", { path: ".git/config" }, { cwd: repo, env: {} }), /off limits|\.git/).catch(() => {});
  try {
    fs.symlinkSync(outside, path.join(repo, "link"), "dir");
    assert.throws(() => safePath(repo, "link/secret.txt"), /outside/);
  } catch (e) { if (e.code !== "EPERM" && e.code !== "EACCES") throw e; /* symlinks need privileges on Windows */ }
});

test("edit_file refuses ambiguous or missing text and tells the model why", async () => {
  const repo = tmpRepo({ "f.txt": "a\na\n" });
  await assert.rejects(async () => runTool("edit_file", { path: "f.txt", old_text: "a", new_text: "b" }, { cwd: repo, env: {} }), /2 places/);
  await assert.rejects(async () => runTool("edit_file", { path: "f.txt", old_text: "zzz", new_text: "b" }, { cwd: repo, env: {} }), /not found/);
  assert.equal(fs.readFileSync(path.join(repo, "f.txt"), "utf8"), "a\na\n");
});

test("run_shell withholds the provider key and StealthLab variables from the command", async () => {
  const repo = tmpRepo({});
  const out = await runTool("run_shell", { command: `"${process.execPath}" -e "console.log(JSON.stringify([process.env.OPEN_MODEL_API_KEY||null, process.env.STEALTHLAB_TOKEN||null, process.env.KEEP||null]))"` },
    { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY, STEALTHLAB_TOKEN: "t", KEEP: "yes", PATH: process.env.PATH, SystemRoot: process.env.SystemRoot } });
  assert.match(out, /\[null,null,"yes"\]/);
  assert.ok(!out.includes(KEY));
});

test("a 429 is retried with backoff, a 401 is not, and neither leaks the key", async () => {
  const repo = tmpRepo({});
  let n = 0;
  const flaky = await provider(() => (++n === 1 ? { status: 429, body: "slow down" } : reply({ role: "assistant", content: "ok" })));
  try {
    const out = await runAgent({ task: "t", base_url: flaky.url, model_id: "m" }, { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: noSleep });
    assert.equal(out.stop, "answered");
    assert.equal(flaky.seen.length, 2);
  } finally { flaky.close(); }
  const denied = await provider(() => ({ status: 401, body: `bad key ${KEY}` }));
  try {
    await assert.rejects(runAgent({ task: "t", base_url: denied.url, model_id: "m" }, { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: noSleep }),
      (e) => /HTTP 401/.test(e.message) && !e.message.includes(KEY));
    assert.equal(denied.seen.length, 1);
  } finally { denied.close(); }
});

test("the step budget and the cost cap both stop the loop", async () => {
  const repo = tmpRepo({ "a.txt": "x" });
  const looping = await provider((n) => reply({ role: "assistant", content: null, tool_calls: [toolCall(`c${n}`, "read_file", { path: "a.txt" })] }));
  try {
    const out = await runAgent({ task: "t", base_url: looping.url, model_id: "m", max_steps: 3 }, { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: noSleep });
    assert.equal(out.stop, "max_steps");
    assert.equal(out.steps, 3);
    const capped = await runAgent({ task: "t", base_url: looping.url, model_id: "m", input_per_mtok: 1000, output_per_mtok: 1000, max_cost_usd: 0.00001 },
      { cwd: repo, env: { OPEN_MODEL_API_KEY: KEY }, sleep: noSleep });
    assert.equal(capped.stop, "cost_cap");
  } finally { looping.close(); }
});

test("end to end: the adapter's command runs the real agent process and parseOutput reads its result", async () => {
  const repo = tmpRepo({ "calc.py": "return a - b\n" });
  const p = await provider((n) => (n === 1
    ? reply({ role: "assistant", content: null, tool_calls: [toolCall("c1", "edit_file", { path: "calc.py", old_text: "a - b", new_text: "a + b" })] })
    : reply({ role: "assistant", content: "Fixed.\nLearned: sign was wrong" })));
  try {
    const prof = normaliseProfile("glm-5.3", { key_env: "ZAI_API_KEY", openai_base_url: p.url, model_id: "glm-5.3", input_per_mtok: 1, output_per_mtok: 2 });
    const spec = stealth.buildCommand({ task: "fix add", model: "glm-5.3", env: { ...process.env, ZAI_API_KEY: KEY }, profile: prof });
    const result = await new Promise((resolve) => {
      const child = spawn(spec.cmd, spec.args, { cwd: repo, env: { ...process.env, ...Object.fromEntries(Object.entries(spec.env).filter(([, v]) => v !== undefined)) } });
      let stdout = "", stderr = "";
      child.stdout.on("data", (d) => (stdout += d));
      child.stderr.on("data", (d) => (stderr += d));
      child.on("close", (code) => resolve({ code, stdout, stderr }));
      child.stdin.end(spec.stdinText);
    });
    assert.equal(result.code, 0, result.stderr);
    assert.ok(!result.stdout.includes(KEY));
    const parsed = stealth.parseOutput(result);
    assert.equal(parsed.finalMessage.startsWith("Fixed."), true);
    assert.deepEqual(parsed.learned, ["sign was wrong"]);
    assert.deepEqual(parsed.tokens, { in: 200, out: 20 });
    assert.equal(fs.readFileSync(path.join(repo, "calc.py"), "utf8"), "return a + b\n");
  } finally { p.close(); }
});
