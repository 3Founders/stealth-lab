import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

import { createConsole, explainFailure } from "../lib/console.mjs";
import { removeEnvFileVar, writeEnvFileVar } from "../lib/envfile.mjs";
import { normaliseProfile, profileKey } from "../lib/executors/profile.mjs";

const mk = () => fs.mkdtempSync(path.join(os.tmpdir(), "console-"));
const SECRET = "sk-test-SECRETVALUE-9876";

async function boot({ cli, fetchImpl, files = {} } = {}) {
  const dir = mk();
  const env = { STEALTHLAB_HOME: dir, HOME: dir, USERPROFILE: dir, PATH: process.env.PATH };
  fs.writeFileSync(path.join(dir, "config.json"), JSON.stringify({ url: "http://127.0.0.1:1/mcp" }));
  for (const [f, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, f)), { recursive: true });
    fs.writeFileSync(path.join(dir, f), text);
  }
  const c = createConsole({ env, cli, fetchImpl });
  const { port } = await c.listen(0);
  const call = (method, p, body, headers = {}) => new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const req = http.request({ host: "127.0.0.1", port, path: p, method,
      headers: { "x-console-token": c.token, ...(data ? { "content-type": "application/json" } : {}), ...headers } }, (res) => {
      let text = "";
      res.on("data", (d) => { text += d; });
      res.on("end", () => resolve({ status: res.statusCode, text, json: () => JSON.parse(text) }));
    });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
  return { c, env, dir, port, call, close: () => c.close() };
}

test("every route but /health wants the token; a wrong host or origin is refused", async () => {
  const t = await boot();
  try {
    assert.equal((await t.call("GET", "/health", undefined, { "x-console-token": "" })).status, 200);
    assert.equal((await t.call("GET", "/clients", undefined, { "x-console-token": "nope" })).status, 401);
    assert.equal((await t.call("GET", "/clients", undefined, { origin: "http://evil.example" })).status, 403);
    assert.equal((await t.call("GET", "/clients", undefined, { host: "evil.example" })).status, 403);
    assert.equal((await t.call("GET", "/clients")).status, 200);
  } finally { await t.close(); }
});

test("clients: the three supported tools, with what each gets", async () => {
  const t = await boot({ files: { ".codex/config.toml": "[mcp_servers.stealthlab]\nurl = \"x\"\n" } });
  try {
    const list = (await t.call("GET", "/clients")).json();
    assert.deepEqual(list.map((c) => c.id), ["claude-code", "cursor", "codex"]);
    assert.equal(list.find((c) => c.id === "codex").hooks, false);
    assert.equal(list.find((c) => c.id === "cursor").hooks, true);
    assert.equal(list.find((c) => c.id === "codex").installed, true);
    assert.equal(list.find((c) => c.id === "cursor").installed, false);
  } finally { await t.close(); }
});

test("install runs the CLI per tool, refuses unknown tools, and saves the Cursor model on success", async () => {
  const seen = [];
  const t = await boot({ cli: async (args) => { seen.push(args); return { status: 0, text: "StealthLab MCP\n  ok    Cursor          C:\\x\\mcp.json\n" }; } });
  try {
    assert.equal((await t.call("POST", "/install", { clients: ["vim"] })).status, 400);
    assert.equal((await t.call("POST", "/install", { clients: [] })).status, 400);
    const r = await t.call("POST", "/install", { clients: ["cursor", "codex"], cursor_model: "claude-sonnet-5-5" });
    const out = r.json();
    assert.deepEqual(out.map((o) => [o.client, o.ok]), [["cursor", true], ["codex", true]]);
    assert.deepEqual(seen[0], ["install", "--client", "cursor", "--with-exec"]);
    assert.deepEqual(seen[1], ["install", "--client", "codex"]);       // Codex gets no dispatch hooks
    assert.equal(JSON.parse(fs.readFileSync(path.join(t.dir, "config.json"), "utf8")).cursor_model, "claude-sonnet-5-5");
    const dry = (await t.call("POST", "/install", { clients: ["codex"], dry_run: true })).json();
    assert.ok(seen.at(-1).includes("--dry-run") && typeof dry[0].diff === "string");
    assert.equal((await t.call("POST", "/install", { clients: ["cursor"], cursor_model: "bad model;rm" })).status, 400);
  } finally { await t.close(); }
});

test("a failed install says so, with a fix", async () => {
  const t = await boot({ cli: async () => ({ status: 1, text: "  FAIL  Codex CLI       cannot write\n" }) });
  try {
    const [o] = (await t.call("POST", "/install", { clients: ["codex"] })).json();
    assert.equal(o.ok, false);
    assert.match(o.error, /cannot write/);
    assert.ok(o.fix);
  } finally { await t.close(); }
});

test("keys: saved once, listed by last four characters only, never returned or echoed", async () => {
  const exec = { executors: { stealth: { models: ["m"] } },
                 profiles: { m: { model_id: "m", key_env: "TEST_PROVIDER_KEY", openai_base_url: "https://api.example.com/v1" } } };
  const t = await boot({ files: { "exec.json": JSON.stringify(exec) } });
  try {
    assert.equal((await t.call("GET", "/keys")).json()[0].set, false);
    const put = await t.call("PUT", "/keys/TEST_PROVIDER_KEY", { value: SECRET });
    assert.equal(put.json().last4, SECRET.slice(-4));
    assert.ok(!put.text.includes(SECRET));
    const list = await t.call("GET", "/keys");
    assert.ok(!list.text.includes(SECRET));
    assert.deepEqual(list.json()[0], { name: "TEST_PROVIDER_KEY", set: true, last4: SECRET.slice(-4) });
    assert.ok(!(await t.call("GET", "/models")).text.includes(SECRET));
    assert.ok(!(await t.call("GET", "/readiness")).text.includes(SECRET));
    assert.equal((await t.call("PUT", "/keys/lowercase", { value: "x" })).status, 404);
    const bad = await t.call("PUT", "/keys/TEST_PROVIDER_KEY", { value: "a\nb" });
    assert.equal(bad.status, 400);
    assert.ok(!bad.text.includes("a\nb"));
    assert.ok(fs.readFileSync(path.join(t.dir, "keys.env"), "utf8").includes(SECRET));   // in the user's own file, and only there
    assert.equal((await t.call("DELETE", "/keys/TEST_PROVIDER_KEY")).json().removed, true);
    assert.equal((await t.call("GET", "/keys")).json()[0].set, false);
  } finally { await t.close(); }
});

test("the env file writer replaces one variable and keeps every other line", () => {
  const dir = mk();
  const env = { STEALTHLAB_HOME: dir };
  const file = path.join(dir, "mine.env");
  fs.writeFileSync(file, "# keep\nOTHER=1\nA_KEY=old\nLAST=z\n");
  fs.writeFileSync(path.join(dir, "config.json"), JSON.stringify({ env_file: file }));
  writeEnvFileVar("A_KEY", "new value #1", env);
  assert.equal(fs.readFileSync(file, "utf8"), "# keep\nOTHER=1\nLAST=z\nA_KEY=\"new value #1\"\n");
  assert.equal(removeEnvFileVar("A_KEY", env), true);
  assert.equal(fs.readFileSync(file, "utf8").includes("A_KEY"), false);
  assert.throws(() => writeEnvFileVar("bad name", "x", env), /capital letters/);
});

test("the env file writer also replaces an indented or exported assignment of the same variable", () => {
  const dir = mk();
  const env = { STEALTHLAB_HOME: dir };
  const file = path.join(dir, "mine.env");
  fs.writeFileSync(file, ["export A_KEY=old1", "  A_KEY=old2", "A_KEY_2=keep", ""].join("\n"));
  fs.writeFileSync(path.join(dir, "config.json"), JSON.stringify({ env_file: file }));
  writeEnvFileVar("A_KEY", "fresh", env);
  assert.equal(fs.readFileSync(file, "utf8"), ["A_KEY_2=keep", "A_KEY=fresh", ""].join("\n"));
});

test("models: add a local model with no key, refuse a LAN http address, delete it", async () => {
  const t = await boot();
  try {
    const add = await t.call("POST", "/models", { name: "qwen-local", model_id: "qwen2.5-coder:7b", openai_base_url: "http://127.0.0.1:11434/v1" });
    assert.equal(add.status, 200, add.text);
    const list = (await t.call("GET", "/models")).json();
    assert.equal(list[0].local, true);
    assert.equal(list[0].key_set, true);
    assert.equal(list[0].key_env, null);
    const raw = JSON.parse(fs.readFileSync(path.join(t.dir, "exec.json"), "utf8"));
    assert.deepEqual(raw.executors.stealth.models, ["qwen-local"]);
    assert.equal((await t.call("POST", "/models", { name: "lan", model_id: "x", openai_base_url: "http://192.168.1.20:8000/v1" })).status, 400);
    assert.equal((await t.call("POST", "/models", { name: "remote", model_id: "x", openai_base_url: "https://api.example.com/v1" })).status, 400);
    assert.equal((await t.call("DELETE", "/models/qwen-local")).status, 200);
    assert.equal((await t.call("GET", "/models")).json().length, 0);
  } finally { await t.close(); }
});

test("a local profile needs no key; a remote one still does", () => {
  const p = normaliseProfile("q", { model_id: "q", openai_base_url: "http://127.0.0.1:11434/v1" });
  assert.equal(profileKey(p, {}), "local");
  assert.throws(() => normaliseProfile("r", { model_id: "r", openai_base_url: "https://x.example/v1" }), /key_env/);
});

test("model test: latency on success; refusals and timeouts are explained in words, never the body", async () => {
  let mode = "ok";
  const fetchImpl = async () => {
    if (mode === "ok") return new Response(JSON.stringify({ choices: [{ message: { content: "OK" } }] }), { status: 200 });
    if (mode === "401") return new Response(`{"error":"key ${SECRET} is invalid"}`, { status: 401 });
    const e = new Error("fetch failed");
    e.cause = { code: "ECONNREFUSED" };
    throw e;
  };
  const t = await boot({ fetchImpl });
  try {
    await t.call("POST", "/models", { name: "qwen-local", model_id: "q", openai_base_url: "http://127.0.0.1:11434/v1" });
    const ok = (await t.call("POST", "/models/qwen-local/test")).json();
    assert.equal(ok.ok, true);
    assert.ok(ok.latency_ms >= 0);
    mode = "401";
    const refused = await t.call("POST", "/models/qwen-local/test");
    assert.match(refused.json().error, /refused the key/);
    assert.ok(!refused.text.includes(SECRET));
    mode = "down";
    assert.match((await t.call("POST", "/models/qwen-local/test")).json().error, /Could not reach/);
  } finally { await t.close(); }
  assert.match(explainFailure({ status: 429 }), /rate limiting/);
});

test("chat streams each model's reply tagged by model, with timings and token counts", async () => {
  const fetchImpl = async (url, init) => {
    assert.equal(JSON.parse(init.body).stream, true);
    const enc = new TextEncoder();
    const chunks = ["data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n\n", "data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n",
                    "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2}}\n\n", "data: [DONE]\n\n"];
    return new Response(new ReadableStream({ start(c) { for (const x of chunks) c.enqueue(enc.encode(x)); c.close(); } }), { status: 200 });
  };
  const t = await boot({ fetchImpl });
  try {
    await t.call("POST", "/models", { name: "qwen-local", model_id: "q", openai_base_url: "http://127.0.0.1:11434/v1", input_per_mtok: 0, output_per_mtok: 0 });
    const r = await t.call("POST", "/chat", { models: ["qwen-local", "ghost"], messages: [{ role: "user", content: "hi" }] });
    const events = r.text.split("\n\n").filter(Boolean).map((l) => JSON.parse(l.replace(/^data: /, "")));
    const mine = events.filter((e) => e.model === "qwen-local");
    assert.equal(mine.filter((e) => e.delta).map((e) => e.delta).join(""), "Hello");
    const done = mine.find((e) => e.done);
    assert.equal(done.tokens_in, 5);
    assert.equal(done.tokens_out, 2);
    assert.ok(done.ttft_ms >= 0 && done.total_ms >= done.ttft_ms);
    assert.match(events.find((e) => e.model === "ghost").error, /not set up for chat/);
    assert.equal((await t.call("POST", "/chat", { models: [], messages: [] })).status, 400);
  } finally { await t.close(); }
});

test("static files come only from the ui dir, and never above it", async () => {
  const ui = mk();
  fs.writeFileSync(path.join(ui, "index.html"), "<h1>console</h1>");
  fs.writeFileSync(path.join(path.dirname(ui), "secret.txt"), "nope");
  const dir = mk();
  const c = createConsole({ env: { STEALTHLAB_HOME: dir, HOME: dir, USERPROFILE: dir }, uiDir: ui });
  const { port } = await c.listen(0);
  const get = (p) => new Promise((resolve) => http.get({ host: "127.0.0.1", port, path: p }, (res) => {
    let t = "";
    res.on("data", (d) => { t += d; });
    res.on("end", () => resolve({ status: res.statusCode, t }));
  }));
  try {
    assert.equal((await get("/")).t, "<h1>console</h1>");
    assert.notEqual((await get("/..%2fsecret.txt")).t, "nope");
    assert.notEqual((await get("/../secret.txt")).t, "nope");
  } finally { await c.close(); }
});
