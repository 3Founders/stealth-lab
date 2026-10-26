import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { checklist, ensureExecConfig, execTemplate, keyIsSet, keyIsSetAsync, probeServer } from "../lib/setup.mjs";
import { cursorPlanModel } from "../lib/cursor_dispatch.mjs";
import { clients } from "../lib/clients.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "kell.mjs");
const mk = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));

// async spawn: the stub server below lives in this process, so a blocking spawnSync would deadlock it
function run(args, env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [BIN, ...args], { env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let text = "";
    child.stdout.on("data", (d) => { text += d; });
    child.stderr.on("data", (d) => { text += d; });
    child.on("close", (status) => resolve({ status, text }));
  });
}
function stubServer(status = 200) {
  const server = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      if (req.method === "DELETE") { res.writeHead(200).end(); return; }
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { protocolVersion: "2025-06-18", serverInfo: { name: "stub", version: "1" } } }));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/mcp` })));
}
const sandbox = () => ({ STEALTHLAB_TEST_HOME: mk("setup-home-"), STEALTHLAB_HOME: mk("setup-sl-"), STEALTHLAB_MCP_URL: "", STEALTHLAB_TOKEN: "", STEALTHLAB_CURSOR_MODEL: "" });

test("setup does the whole install in one command and says it is ready", async () => {
  const { server, url } = await stubServer();
  try {
    const env = { ...sandbox(), MY_TEST_KEY: "sk-test-not-a-real-key" };
    const tokenFile = path.join(mk("setup-tok-"), "token.txt");
    fs.writeFileSync(tokenFile, "tok-from-file\n");
    const r = await run(["setup", "--client", "cursor", "--url", url, "--token-file", tokenFile, "--key-env", "MY_TEST_KEY", "--cursor-model", "my-model"], env);
    assert.equal(r.status, 0, r.text);
    assert.match(r.text, /Ready\./);
    const cfg = JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "config.json"), "utf8"));
    assert.equal(cfg.token, "tok-from-file");
    assert.equal(cfg.dispatch, true);
    assert.equal(cfg.cursor_model, "my-model");
    const exec = fs.readFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), "utf8");
    assert.match(exec, /"key_env": "MY_TEST_KEY"/);
    assert.ok(!exec.includes("sk-test-not-a-real-key"), "the key's value is never written");
    assert.ok(!r.text.includes("sk-test-not-a-real-key"), "the key's value is never printed");
    const hooks = JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_TEST_HOME, ".cursor", "hooks.json"), "utf8")).hooks;
    assert.ok(hooks.beforeSubmitPrompt?.length, "the Cursor prompt hook is installed");
  } finally { server.close(); }
});

test("setup that is not ready exits 3 and names each missing piece with its fix", async () => {
  const env = sandbox();                                        // no server, no key, no Cursor model
  const r = await run(["setup", "--client", "cursor", "--url", "http://127.0.0.1:9/mcp", "--key-env", "MY_MISSING_KEY"], env);
  assert.equal(r.status, 3, r.text);
  assert.match(r.text, /MISSING\s+server/);
  assert.match(r.text, /MISSING\s+key MY_MISSING_KEY/);
  assert.match(r.text, /MISSING\s+cursor model/);
  assert.match(r.text, /--cursor-model/);
  assert.match(r.text, /Still to do:/);
});

test("setup never overwrites an exec.json the user already has", async () => {
  const { server, url } = await stubServer();
  try {
    const env = { ...sandbox(), MY_TEST_KEY: "x" };
    const mine = { executors: { stealth: { models: ["only-mine"] } }, profiles: { "only-mine": { key_env: "MY_TEST_KEY", openai_base_url: "https://x.example/v1" } } };
    fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify(mine));
    const r = await run(["setup", "--client", "cursor", "--url", url, "--key-env", "MY_TEST_KEY", "--cursor-model", "m"], env);
    assert.equal(JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), "utf8")).executors.stealth.models[0], "only-mine");
    assert.match(r.text, /1 model\(s\): only-mine/);
  } finally { server.close(); }
});

test("--no-exec connects only: no exec.json, no dispatch flag", async () => {
  const { server, url } = await stubServer();
  try {
    const env = sandbox();
    const r = await run(["setup", "--client", "cursor", "--url", url, "--no-exec"], env);
    assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "exec.json")));
    assert.equal(JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "config.json"), "utf8")).dispatch, undefined);
    assert.ok([0, 3].includes(r.status), r.text);   // it may report a missing token, but it ran
  } finally { server.close(); }
});

test("--key-env takes a variable NAME: something that looks like a key is refused and not echoed whole", async () => {
  const env = sandbox();
  const secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789";
  const r = await run(["setup", "--client", "cursor", "--url", "http://127.0.0.1:9/mcp", "--key-env", secret], env);
  assert.equal(r.status, 2, r.text);
  assert.match(r.text, /environment variable NAME/);
  assert.ok(!r.text.includes(secret));
});

test("--guided without a terminal has nobody to ask, so it runs straight through", async () => {
  const { server, url } = await stubServer();
  try {
    const env = { ...sandbox(), MY_TEST_KEY: "x" };
    const r = await run(["setup", "--guided", "--client", "cursor", "--url", url, "--token", "t", "--key-env", "MY_TEST_KEY", "--cursor-model", "m"], env);
    assert.equal(r.status, 0, r.text);
    assert.match(r.text, /Ready\./);
  } finally { server.close(); }
});

test("--dry-run changes nothing", async () => {
  const env = sandbox();
  const r = await run(["setup", "--client", "cursor", "--url", "http://127.0.0.1:9/mcp", "--dry-run"], env);
  assert.equal(r.status, 0, r.text);
  assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "config.json")));
  assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "exec.json")));
});

test("the Cursor plan model comes from the environment first, then from the model setup saved; auto is still not a model", () => {
  const home = mk("setup-model-");
  fs.writeFileSync(path.join(home, "config.json"), JSON.stringify({ cursor_model: "saved-model" }));
  assert.equal(cursorPlanModel({ model: "auto" }, { STEALTHLAB_HOME: home }), "saved-model");
  assert.equal(cursorPlanModel({ model: "auto" }, { STEALTHLAB_HOME: home, STEALTHLAB_CURSOR_MODEL: "env-model" }), "env-model");
  assert.equal(cursorPlanModel({ model: "auto" }, { STEALTHLAB_HOME: mk("setup-empty-") }), null);
});

test("the template names the key variable and holds no key; a key-looking name is rejected", () => {
  const t = execTemplate("MY_KEY");
  for (const p of Object.values(t.profiles)) assert.equal(p.key_env, "MY_KEY");
  assert.ok(!JSON.stringify(t).includes("sk-"));
  assert.throws(() => execTemplate("sk-1234567890abcdef"), /environment variable NAME/);
  const dir = mk("setup-tpl-");
  const first = ensureExecConfig({ env: { STEALTHLAB_HOME: dir }, keyEnv: "MY_KEY" });
  assert.equal(first.created, true);
  assert.equal(ensureExecConfig({ env: { STEALTHLAB_HOME: dir }, keyEnv: "OTHER" }).created, false);
});

test("keyIsSet: this shell first; on Windows the user environment (what a restarted agent inherits)", () => {
  assert.deepEqual(keyIsSet("K", { env: { K: "v" }, platform: "linux" }), { set: true, where: "this shell" });
  assert.equal(keyIsSet("K", { env: {}, platform: "linux" }).set, false);
  const reg = (out, status = 0) => () => ({ status, stdout: out });
  assert.equal(keyIsSet("K", { env: {}, platform: "win32", spawn: reg("HKEY_CURRENT_USER\\Environment\n    K    REG_SZ    v\n") }).where, "your user environment");
  assert.equal(keyIsSet("K", { env: {}, platform: "win32", spawn: reg("", 1) }).set, false);
});

test("probeServer tells a refused token from an unreachable server", async () => {
  const { server, url } = await stubServer(401);
  try {
    assert.match((await probeServer(url, "t")).detail, /refused the token/);
  } finally { server.close(); }
  assert.match((await probeServer("http://127.0.0.1:9/mcp", "t", { timeoutMs: 2000 })).detail, /unreachable/);
});

test("checklist: ready only when every item is ok; the Cursor model is asked for only when Cursor is chosen", () => {
  const base = { server: { ok: true, detail: "answers" }, hasToken: true, execOk: true, models: ["a"], keyEnv: "K", key: { set: true, where: "this shell" }, dispatch: true, cursorModel: "m", wantsCursor: true };
  assert.equal(checklist(base).ready, true);
  assert.equal(checklist({ ...base, cursorModel: "" }).ready, false);
  assert.equal(checklist({ ...base, cursorModel: "", wantsCursor: false }).ready, true);
  assert.equal(checklist({ ...base, dispatch: false, key: { set: false }, execOk: false }).ready, true, "connect-only needs no key or exec.json");
  const win = checklist({ ...base, key: { set: false }, platform: "win32" }).items.find((i) => i.name === "key K");
  assert.match(win.fix, /setx K/);
});

test("the client list in --help is the real one (it is written out so --help does not load the installer)", async () => {
  const r = await run(["help"], sandbox());
  assert.ok(r.text.includes(clients().map((c) => c.id).join(", ")), r.text.slice(0, 400));
});

test("keyIsSetAsync agrees with keyIsSet and asks the Windows user environment only when this shell lacks the variable", async () => {
  let asked = 0;
  const reg = (out, err = null) => (cmd, args, opts, cb) => { asked++; cb(err, out); };
  assert.deepEqual(await keyIsSetAsync("K", { env: { K: "v" }, platform: "win32", execFileImpl: reg("") }), { set: true, where: "this shell" });
  assert.equal(asked, 0, "no child process when the shell has it");
  assert.equal((await keyIsSetAsync("K", { env: {}, platform: "linux", execFileImpl: reg("") })).set, false);
  assert.equal(asked, 0, "none off Windows");
  assert.equal((await keyIsSetAsync("K", { env: {}, platform: "win32", execFileImpl: reg("    K    REG_SZ    v\n") })).where, "your user environment");
  assert.equal((await keyIsSetAsync("K", { env: {}, platform: "win32", execFileImpl: reg("", new Error("not found")) })).set, false);
  assert.equal(asked, 2);
});
