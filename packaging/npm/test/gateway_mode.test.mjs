import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { fetchGatewayModels, gatewayBase, gatewayExecConfig } from "../lib/setup.mjs";
import { normaliseProfile, profileKey } from "../lib/executors/profile.mjs";
import { readExecConfig } from "../lib/exec/store.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");
const mk = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const TOKEN = "tok-gateway-test-0123456789";

function run(args, env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [BIN, ...args], { env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let text = "";
    child.stdout.on("data", (d) => { text += d; });
    child.stderr.on("data", (d) => { text += d; });
    child.on("close", (status) => resolve({ status, text }));
  });
}
// a stand-in for the StealthLab server: the MCP endpoint answers initialize, /v1/models lists what the admin allowed
function stub({ models, modelsStatus = 200 } = {}) {
  const seen = { auth: [] };
  const server = http.createServer((req, res) => {
    seen.auth.push(`${req.method} ${req.url} ${req.headers.authorization || ""}`);
    req.resume();
    req.on("end", () => {
      if (req.method === "DELETE") { res.writeHead(200).end(); return; }
      res.writeHead(req.url.startsWith("/v1/models") ? modelsStatus : 200, { "content-type": "application/json" });
      if (req.url.startsWith("/v1/models")) {
        res.end(JSON.stringify({ object: "list", data: models || [] }));
      } else {
        res.end(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { serverInfo: { name: "stub" } } }));
      }
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, origin: `http://127.0.0.1:${server.address().port}` })));
}
const listing = [
  { id: "gpt-oss-120b", object: "model", stealthlab: { input_per_mtok: 0.037, output_per_mtok: 0.17 } },
  { id: "gemma-4-31b-it", object: "model", stealthlab: { input_per_mtok: 0.09, output_per_mtok: 0.34 } },
];
const sandbox = () => ({ STEALTHLAB_TEST_HOME: mk("gw-home-"), STEALTHLAB_HOME: mk("gw-sl-"), STEALTHLAB_MCP_URL: "", STEALTHLAB_TOKEN: "",
                         STEALTHLAB_CURSOR_MODEL: "", GENERAL_COMPUTE_API_KEY: "" });

test("setup --gateway writes exec.json from the gateway's own list, holds no provider key, and is ready", async () => {
  const { server, seen, origin } = await stub({ models: listing });
  try {
    const env = sandbox();
    const tokenFile = path.join(mk("gw-tok-"), "t.txt");
    fs.writeFileSync(tokenFile, TOKEN + "\n");
    const r = await run(["setup", "--gateway", "--client", "cursor", "--url", `${origin}/mcp`, "--token-file", tokenFile, "--cursor-model", "m"], env);
    assert.equal(r.status, 0, r.text);
    assert.match(r.text, /Ready\./);
    assert.match(r.text, /no provider key on this machine/);
    const raw = fs.readFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), "utf8");
    assert.ok(!raw.includes(TOKEN), "the token is never written into exec.json");
    assert.ok(!raw.includes("key_env"), "no provider key variable");
    const cfg = readExecConfig({ ...env });
    assert.deepEqual(cfg.executors.stealth.models, ["gpt-oss-120b", "gemma-4-31b-it"]);
    assert.equal(cfg.profiles["gpt-oss-120b"].key_from, "stealthlab");
    assert.equal(cfg.profiles["gpt-oss-120b"].openai_base_url, `${origin}/v1`);
    assert.equal(cfg.profiles["gpt-oss-120b"].input_per_mtok, 0.037);
    assert.equal(cfg.profiles["gemma-4-31b-it"].no_system_role, true);
    assert.ok(seen.auth.some((a) => a.startsWith("GET /v1/models") && a.endsWith(`Bearer ${TOKEN}`)), "the model list is asked for with the token");
  } finally { server.close(); }
});

test("setup --gateway when the gateway refuses the token: exit 3, names the gateway, writes no exec.json", async () => {
  const { server, origin } = await stub({ modelsStatus: 403 });
  try {
    const env = sandbox();
    const r = await run(["setup", "--gateway", "--client", "cursor", "--url", `${origin}/mcp`, "--token", TOKEN, "--cursor-model", "m"], env);
    assert.equal(r.status, 3, r.text);
    assert.match(r.text, /MISSING\s+gateway/);
    assert.match(r.text, /refused the token/);
    assert.match(r.text, /ask your admin/);
    assert.ok(!fs.existsSync(path.join(env.STEALTHLAB_HOME, "exec.json")));
  } finally { server.close(); }
});

test("--gateway with --no-exec is refused, and an existing exec.json is never overwritten", async () => {
  const env = sandbox();
  const bad = await run(["setup", "--gateway", "--no-exec", "--client", "cursor", "--url", "http://127.0.0.1:9/mcp"], env);
  assert.equal(bad.status, 2, bad.text);
  const { server, origin } = await stub({ models: listing });
  try {
    const mine = { executors: { stealth: { models: ["only-mine"] } }, profiles: { "only-mine": { key_env: "MY_KEY", openai_base_url: "https://x.example/v1" } } };
    fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify(mine));
    await run(["setup", "--gateway", "--client", "cursor", "--url", `${origin}/mcp`, "--token", TOKEN, "--cursor-model", "m"], env);
    assert.equal(JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), "utf8")).executors.stealth.models[0], "only-mine");
  } finally { server.close(); }
});

test("a profile takes its key from the saved StealthLab token, and only for the user's own StealthLab host", () => {
  const home = mk("gw-key-");
  fs.writeFileSync(path.join(home, "config.json"), JSON.stringify({ url: "https://kel.example/mcp", token: TOKEN }));
  const env = { STEALTHLAB_HOME: home };
  const ours = normaliseProfile("m", { key_from: "stealthlab", openai_base_url: "https://kel.example/v1" });
  assert.equal(ours.key_from, "stealthlab");
  assert.equal(ours.key_env, null);
  assert.equal(profileKey(ours, env), TOKEN);
  const elsewhere = normaliseProfile("m", { key_from: "stealthlab", openai_base_url: "https://evil.example/v1" });
  assert.throws(() => profileKey(elsewhere, env), /same host|StealthLab server's host/);
  assert.throws(() => profileKey(ours, { STEALTHLAB_HOME: mk("gw-nokey-") }), /no StealthLab token is saved/);
  const tokenless = mk("gw-notoken-");
  fs.writeFileSync(path.join(tokenless, "config.json"), JSON.stringify({ url: "https://kel.example/mcp" }));
  assert.throws(() => profileKey(ours, { STEALTHLAB_HOME: tokenless }), /no StealthLab token is saved/);
});

test("key_from and key_env together, or any other key_from, are rejected; a provider-key profile is unchanged", () => {
  assert.throws(() => normaliseProfile("m", { key_from: "stealthlab", key_env: "K", openai_base_url: "https://x.example/v1" }), /not both/);
  assert.throws(() => normaliseProfile("m", { key_from: "vault", openai_base_url: "https://x.example/v1" }), /can only be "stealthlab"/);
  const direct = normaliseProfile("m", { key_env: "MY_KEY", openai_base_url: "https://x.example/v1" });
  assert.equal(direct.key_env, "MY_KEY");
  assert.equal(direct.key_from, null);
  assert.equal(profileKey(direct, { MY_KEY: "abc" }), "abc");
});

test("gatewayBase, gatewayExecConfig and fetchGatewayModels", async () => {
  assert.equal(gatewayBase("https://kel.example/mcp"), "https://kel.example/v1");
  assert.equal(gatewayBase("https://kel.example/mcp", "https://gw.example/v1/"), "https://gw.example/v1");
  const cfg = gatewayExecConfig("https://kel.example/v1", [{ id: "gemma-4-31b-it" }, { id: "x-1", input_per_mtok: 1, output_per_mtok: 2 }]);
  assert.equal(cfg.profiles["gemma-4-31b-it"].no_system_role, true);
  assert.equal(cfg.profiles["gemma-4-31b-it"].input_per_mtok, undefined, "no price known: none invented");
  assert.equal(cfg.profiles["x-1"].output_per_mtok, 2);
  const { server, origin } = await stub({ models: [{ id: "ok-model" }, { id: "bad id with spaces" }, { id: 5 }, {}] });
  try {
    const got = await fetchGatewayModels(`${origin}/v1`, TOKEN);
    assert.deepEqual(got.models.map((m) => m.id), ["ok-model"], "only plain model ids are kept");
  } finally { server.close(); }
  assert.match((await fetchGatewayModels("http://127.0.0.1:9/v1", TOKEN, { timeoutMs: 2000 })).detail, /unreachable/);
});
