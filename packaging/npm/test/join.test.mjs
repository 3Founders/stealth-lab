import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { cheapest, joinChecklist, MANAGED_BY, normalizeApi, parseInvite, redeem, selfTest, writeExec } from "../lib/join.mjs";
import { normaliseProfile, profileKey } from "../lib/executors/profile.mjs";
import { checklist } from "../lib/setup.mjs";
import { readExecConfig } from "../lib/exec/store.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "kell.mjs");
const mk = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const CODE = `sl1_${"ab".repeat(16)}_${"cd".repeat(12)}`;
const ORG = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0001";
const profiles = (base = "https://api.example.com/v1") => ({
  "model-a": { model_id: "model-a", openai_base_url: base, key_from: "stealthlab", input_per_mtok: 0.5, output_per_mtok: 1.5 },
  "model-b": { model_id: "model-b", openai_base_url: base, key_from: "stealthlab", input_per_mtok: 0.05, output_per_mtok: 0.1, no_system_role: true },
});
const answer = (base) => ({ organization_id: ORG, name: "Acme", gateway: base, models: ["model-a", "model-b"], profiles: profiles(base) });
const reply = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });

test("an invite must look like an invite, and the API address must be https or this machine", () => {
  assert.equal(parseInvite(` ${CODE} `), CODE);
  for (const bad of ["", "nope", CODE + "x", CODE.replace("sl1_", "sl2_"), CODE.toUpperCase()]) assert.throws(() => parseInvite(bad), /invite code/);
  assert.equal(normalizeApi("https://api.example.com/some/path"), "https://api.example.com");
  assert.equal(normalizeApi("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  for (const bad of ["", "http://api.example.com", "https://u:p@api.example.com", "ftp://x"]) assert.throws(() => normalizeApi(bad));
});

test("a profile with key_from stealthlab carries no provider key and reads the person's own sign-in", () => {
  const p = normaliseProfile("m", { openai_base_url: "https://api.example.com/v1", key_from: "stealthlab" });
  assert.equal(p.key_env, undefined);
  assert.equal(profileKey(p, { STEALTHLAB_TOKEN: "tok" }), "tok");
  const home = mk("join-home-");
  fs.writeFileSync(path.join(home, "config.json"), JSON.stringify({ token: "from-config" }));
  assert.equal(profileKey(p, { STEALTHLAB_HOME: home }), "from-config");
  assert.throws(() => profileKey(p, { STEALTHLAB_HOME: mk("join-empty-") }), /not signed in/);
  assert.throws(() => normaliseProfile("m", { key_from: "elsewhere", openai_base_url: "https://x.example.com/v1" }), /key_from/);
  assert.throws(() => normaliseProfile("m", { key_from: "stealthlab" }), /openai_base_url/);
  assert.throws(() => normaliseProfile("m", { key_from: "stealthlab", openai_base_url: "https://x.example.com/v1", anthropic_base_url: "https://y.example.com" }), /no anthropic_base_url/);
  assert.throws(() => normaliseProfile("m", { openai_base_url: "https://x.example.com/v1" }), /key_env/);   // the old rule still holds
});

test("exec.json comes from the server's answer; a file that is not ours is kept, ours is replaced", () => {
  const env = { STEALTHLAB_HOME: mk("join-exec-") };
  const file = path.join(env.STEALTHLAB_HOME, "exec.json");
  fs.writeFileSync(file, JSON.stringify({ profiles: { mine: { key_env: "MY_KEY", openai_base_url: "https://mine.example.com/v1" } } }));
  const first = writeExec(answer("https://api.example.com/v1"), { env });
  assert.equal(first.movedTo, `${file}.before-join`);
  assert.match(fs.readFileSync(first.movedTo, "utf8"), /MY_KEY/);
  const cfg = readExecConfig(env);
  assert.deepEqual(cfg.executors.stealth.models, ["model-a", "model-b"]);
  assert.equal(cfg.profiles["model-b"].key_from, "stealthlab");
  assert.equal(cfg.profiles["model-b"].no_system_role, true);
  assert.equal(JSON.parse(fs.readFileSync(file, "utf8")).managed_by, MANAGED_BY);
  assert.equal(writeExec(answer("https://api.example.com/v1"), { env }).movedTo, null, "re-running replaces our own file without a backup");
  assert.ok(!fs.readFileSync(file, "utf8").match(/sk-|key_env/), "no provider key or key variable");
});

test("a bad profile in the answer fails at join, not at the first prompt", () => {
  const env = { STEALTHLAB_HOME: mk("join-bad-") };
  const bad = answer("https://api.example.com/v1");
  bad.profiles["model-a"].openai_base_url = "http://not-local.example.com/v1";
  assert.throws(() => writeExec(bad, { env }), /https/);
  assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "exec.json")));
});

test("the self-test uses the cheapest model and says exactly what the server refused", async () => {
  assert.equal(cheapest(profiles()).model_id, "model-b");
  assert.equal(cheapest({ x: { model_id: "x" }, y: { model_id: "y", input_per_mtok: 1, output_per_mtok: 1 } }).model_id, "y");
  let seen;
  const ok = await selfTest({ gateway: "https://api.example.com/v1", token: "tok", orgId: ORG, profile: profiles()["model-b"],
    fetchImpl: async (url, init) => { seen = { url, init }; return reply(200, { choices: [{ message: { content: "ok" } }] }); }, now: (() => { let t = 0; return () => (t += 40); })() });
  assert.deepEqual([ok.ok, ok.detail], [true, "Done by model-b in 40 ms"]);
  assert.equal(seen.url, "https://api.example.com/v1/chat/completions");
  assert.equal(seen.init.headers.authorization, "Bearer tok");
  assert.equal(seen.init.headers["x-stealthlab-org"], ORG);
  assert.equal(JSON.parse(seen.init.body).model, "model-b");
  for (const [status, needle] of [[401, /sign-in was not accepted/], [403, /policy/], [429, /budget or rate limit/], [500, /HTTP 500/]]) {
    const r = await selfTest({ gateway: "https://g.example.com/v1", token: "t", orgId: ORG, profile: profiles()["model-a"], fetchImpl: async () => reply(status, { error: { message: "because" } }) });
    assert.equal(r.ok, false);
    assert.match(r.detail, needle);
    assert.match(r.detail, /because/);
  }
  const down = await selfTest({ gateway: "https://g.example.com/v1", token: "t", orgId: ORG, profile: profiles()["model-a"], fetchImpl: async () => { throw new Error("boom"); } });
  assert.match(down.detail, /did not answer/);
  const odd = await selfTest({ gateway: "https://g.example.com/v1", token: "t", orgId: ORG, profile: profiles()["model-a"], fetchImpl: async () => reply(200, { nope: 1 }) });
  assert.equal(odd.ok, false);
});

test("redeem sends the code only to the join route and turns each refusal into something to do", async () => {
  let call;
  const got = await redeem({ api: "https://api.example.com", token: "tok", code: CODE, fetchImpl: async (url, init) => { call = { url, init }; return reply(200, answer("https://api.example.com/v1")); } });
  assert.equal(got.name, "Acme");
  assert.equal(call.url, "https://api.example.com/v1/join");
  assert.equal(JSON.parse(call.init.body).code, CODE);
  assert.equal(call.init.headers.authorization, "Bearer tok");
  await assert.rejects(redeem({ api: "https://a.example.com", token: "t", code: CODE, fetchImpl: async () => reply(404, { detail: "this invite is not valid" }) }), /expired, or was revoked/);
  await assert.rejects(redeem({ api: "https://a.example.com", token: "t", code: CODE, fetchImpl: async () => reply(401, {}) }), /login/);
  await assert.rejects(redeem({ api: "https://a.example.com", token: "t", code: CODE, fetchImpl: async () => reply(409, { detail: "your admin has not connected a provider key yet" }) }), /not connected a provider key/);
  await assert.rejects(redeem({ api: "https://a.example.com", token: "t", code: CODE, fetchImpl: async () => reply(200, { organization_id: ORG, profiles: {} }) }), /without any models/);
  await assert.rejects(redeem({ api: "https://a.example.com", token: "t", code: CODE, fetchImpl: async () => { throw new Error("offline"); } }), /cannot reach/);
});

test("the readiness list is ready only when every item is, and names the fix for each that is not", () => {
  const base = { org: "Acme", models: ["m"], selfTest: { ok: true, detail: "Done by m in 5 ms" }, hooksOk: true, wantsCursor: true, cursorModel: "x" };
  assert.equal(joinChecklist(base).ready, true);
  const r = joinChecklist({ ...base, selfTest: { ok: false, detail: "HTTP 403: nope" }, cursorModel: undefined, hooksOk: false });
  assert.equal(r.ready, false);
  assert.deepEqual(r.items.filter((i) => !i.ok).map((i) => i.name), ["gateway test", "agent hooks", "cursor model"]);
  assert.ok(r.items.filter((i) => !i.ok).every((i) => i.fix));
  assert.equal(joinChecklist({ ...base, wantsCursor: false, cursorModel: undefined }).ready, true);
});

test("setup does not ask for a provider key when the profiles take the organization's gateway", () => {
  const input = { server: { ok: true, detail: "answers" }, hasToken: true, execOk: true, models: ["m"], keyEnv: "K", key: { set: false }, dispatch: true, cursorModel: "x", wantsCursor: false };
  assert.equal(checklist(input).ready, false);
  const central = checklist({ ...input, central: true });
  assert.equal(central.ready, true);
  assert.ok(!central.items.some((i) => i.name.startsWith("key ")));
});

// ---- the whole command, against a stub server standing in for the API

function stubApi({ joinStatus = 200, chatStatus = 200 } = {}) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => { body += d; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, auth: req.headers.authorization, org: req.headers["x-stealthlab-org"], body });
      const send = (status, json) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(json)); };
      const base = `http://127.0.0.1:${server.address().port}`;
      if (req.url === "/v1/join") return joinStatus === 200 ? send(200, answer(`${base}/v1`)) : send(joinStatus, { detail: "this invite is not valid" });
      if (req.url === "/v1/chat/completions") return chatStatus === 200 ? send(200, { choices: [{ message: { content: "ok" } }] }) : send(chatStatus, { error: { message: "model_not_allowed" } });
      send(404, {});
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, api: `http://127.0.0.1:${server.address().port}` })));
}
function runCli(args, env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [BIN, ...args], { env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let text = "";
    child.stdout.on("data", (d) => { text += d; });
    child.stderr.on("data", (d) => { text += d; });
    child.on("close", (status) => resolve({ status, text }));
  });
}
const sandbox = () => ({ STEALTHLAB_TEST_HOME: mk("join-th-"), STEALTHLAB_HOME: mk("join-sl-"), STEALTHLAB_MCP_URL: "", STEALTHLAB_TOKEN: "dev-token", STEALTHLAB_CURSOR_MODEL: "" });

test("join: one command signs the person in, writes the models, registers Cursor and proves the gateway works", async () => {
  const { server, seen, api } = await stubApi();
  try {
    const env = sandbox();
    const r = await runCli(["join", CODE, "--client", "cursor", "--url", `${api}/mcp`, "--api", api, "--cursor-model", "my-model"], env);
    assert.equal(r.status, 0, r.text);
    assert.match(r.text, /Joined Acme/);
    assert.match(r.text, /ok\s+gateway test\s+Done by model-b/);
    assert.match(r.text, /Ready\./);
    const cfg = JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "config.json"), "utf8"));
    assert.equal(cfg.organization_id, ORG);
    assert.equal(cfg.api_url, api);
    assert.equal(cfg.cursor_model, "my-model");
    const exec = readExecConfig({ STEALTHLAB_HOME: env.STEALTHLAB_HOME });
    assert.equal(exec.profiles["model-a"].key_from, "stealthlab");
    assert.ok(JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_TEST_HOME, ".cursor", "hooks.json"), "utf8")).hooks.beforeSubmitPrompt?.length);
    const joinCall = seen.find((s) => s.url === "/v1/join");
    assert.equal(joinCall.auth, "Bearer dev-token");
    assert.equal(JSON.parse(joinCall.body).code, CODE);
    const chat = seen.find((s) => s.url === "/v1/chat/completions");
    assert.equal(chat.org, ORG);
    assert.equal(JSON.parse(chat.body).model, "model-b");
    assert.ok(!r.text.includes(CODE), "the invite code is not echoed");
  } finally { server.close(); }
});

test("join: a refused gateway test ends not-ready (exit 3) with the server's reason", async () => {
  const { server, api } = await stubApi({ chatStatus: 403 });
  try {
    const r = await runCli(["join", CODE, "--client", "cursor", "--url", `${api}/mcp`, "--api", api, "--cursor-model", "m"], sandbox());
    assert.equal(r.status, 3, r.text);
    assert.match(r.text, /MISSING\s+gateway test.*policy.*model_not_allowed/);
  } finally { server.close(); }
});

test("join: an invalid invite stops before anything is written", async () => {
  const { server, api } = await stubApi({ joinStatus: 404 });
  try {
    const env = sandbox();
    const r = await runCli(["join", CODE, "--client", "cursor", "--url", `${api}/mcp`, "--api", api], env);
    assert.notEqual(r.status, 0);
    assert.match(r.text, /not valid, has expired, or was revoked/);
    assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "exec.json")));
    assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_TEST_HOME, ".cursor", "hooks.json")));
  } finally { server.close(); }
});

test("join: a malformed code or a missing API address is refused up front; --dry-run touches nothing", async () => {
  const env = sandbox();
  assert.equal((await runCli(["join", "nope", "--url", "http://127.0.0.1:1/mcp", "--api", "http://127.0.0.1:1"], env)).status, 2);
  assert.equal((await runCli(["join", CODE, "--url", "http://127.0.0.1:1/mcp"], env)).status, 2);
  const dry = await runCli(["join", CODE, "--client", "cursor", "--url", "http://127.0.0.1:1/mcp", "--api", "http://127.0.0.1:1", "--dry-run"], env);
  assert.equal(dry.status, 0, dry.text);
  assert.match(dry.text, /dry run/);
  assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "config.json")));
});

test("join --no-exec connects and tests the gateway but leaves dispatch off", async () => {
  const { server, api } = await stubApi();
  try {
    const env = sandbox();
    const r = await runCli(["join", CODE, "--client", "cursor", "--url", `${api}/mcp`, "--api", api, "--cursor-model", "m", "--no-exec"], env);
    assert.equal(r.status, 0, r.text);
    assert.equal(JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "config.json"), "utf8")).dispatch, false);
  } finally { server.close(); }
});
