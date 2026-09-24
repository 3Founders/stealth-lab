import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  launchSpec, removeCodexServer, removeJsonServer, upsertCodexServer, upsertJsonServer,
} from "../lib/clients.mjs";
import { normalizeUrl, resolveSettings, writeConfig } from "../lib/config.mjs";

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "slmcp-"));

test("json upsert keeps other servers and keys, writes a backup, removes cleanly", () => {
  const f = path.join(tmp(), "mcp.json");
  fs.writeFileSync(f, JSON.stringify({ theme: "dark", mcpServers: { other: { url: "x" } } }));
  upsertJsonServer(f, { url: "https://h/mcp" });
  const doc = JSON.parse(fs.readFileSync(f, "utf8"));
  assert.deepEqual(doc, { theme: "dark", mcpServers: { other: { url: "x" }, stealthlab: { url: "https://h/mcp" } } });
  assert.ok(fs.existsSync(`${f}.bak`));
  assert.equal(removeJsonServer(f), true);
  assert.deepEqual(JSON.parse(fs.readFileSync(f, "utf8")).mcpServers, { other: { url: "x" } });
  assert.equal(removeJsonServer(f), false);
});

test("json upsert refuses to clobber an unparseable config", () => {
  const f = path.join(tmp(), "mcp.json");
  fs.writeFileSync(f, "{ // comment\n}");
  assert.throws(() => upsertJsonServer(f, { url: "u" }), /not valid JSON/);
  assert.equal(fs.readFileSync(f, "utf8"), "{ // comment\n}");
});

test("json upsert creates a missing file and directory", () => {
  const f = path.join(tmp(), "a", "b", "mcp.json");
  upsertJsonServer(f, { url: "u" });
  assert.deepEqual(JSON.parse(fs.readFileSync(f, "utf8")), { mcpServers: { stealthlab: { url: "u" } } });
});

test("codex toml: replaces our table and its subtables, leaves the rest", () => {
  const f = path.join(tmp(), "config.toml");
  fs.writeFileSync(f, [
    'model = "o3"', "", "[mcp_servers.stealthlab]", 'command = "old"', "",
    "[mcp_servers.stealthlab.env]", 'A = "1"', "", "[mcp_servers.stealthlabx]", 'url = "keep"', "",
  ].join("\n"));
  upsertCodexServer(f, { url: "https://h/mcp", token: 'a"b' });
  const text = fs.readFileSync(f, "utf8");
  assert.match(text, /^model = "o3"/);
  assert.match(text, /\[mcp_servers\.stealthlabx\]\nurl = "keep"/);
  assert.doesNotMatch(text, /command = "old"|\.stealthlab\.env/);
  assert.match(text, /\[mcp_servers\.stealthlab\]\nurl = "https:\/\/h\/mcp"\nhttp_headers = \{ Authorization = "Bearer a\\"b" \}\n$/);
  assert.equal(removeCodexServer(f), true);
  assert.doesNotMatch(fs.readFileSync(f, "utf8"), /\[mcp_servers\.stealthlab\]/);
  assert.equal(removeCodexServer(f), false);
});

test("launchSpec: npx cache -> npx (cmd /c on Windows), global install -> absolute node + script", () => {
  const npx = "/home/u/.npm/_npx/abc/node_modules/stealthlab-mcp/bin/stealthlab-mcp.mjs";
  assert.deepEqual(launchSpec({ scriptPath: npx, platform: "linux" }), { command: "npx", args: ["-y", "stealthlab-mcp@latest"] });
  assert.deepEqual(launchSpec({ scriptPath: npx, platform: "win32" }).args.slice(0, 2), ["/c", "npx"]);
  const g = "/usr/lib/node_modules/stealthlab-mcp/bin/stealthlab-mcp.mjs";
  assert.deepEqual(launchSpec({ scriptPath: g, nodePath: "/usr/bin/node" }), { command: "/usr/bin/node", args: [g] });
});

test("normalizeUrl: adds /mcp to a bare origin, rejects plain http off loopback", () => {
  assert.equal(normalizeUrl("https://mcp.example.com"), "https://mcp.example.com/mcp");
  assert.equal(normalizeUrl("https://mcp.example.com/mcp/"), "https://mcp.example.com/mcp");
  assert.equal(normalizeUrl("http://127.0.0.1:8765/mcp"), "http://127.0.0.1:8765/mcp");
  assert.throws(() => normalizeUrl("http://mcp.example.com/mcp"), /refusing plain http/);
  assert.throws(() => normalizeUrl("ftp://x"), /http\(s\)/);
});

test("settings precedence: flag > env > saved config", () => {
  const env = { STEALTHLAB_HOME: tmp() };
  writeConfig({ url: "https://saved/mcp", token: "saved" }, env);
  assert.deepEqual(resolveSettings({}, env), { url: "https://saved/mcp", token: "saved" });
  env.STEALTHLAB_MCP_URL = "https://env/mcp";
  env.STEALTHLAB_TOKEN = "envtok";
  assert.deepEqual(resolveSettings({}, env), { url: "https://env/mcp", token: "envtok" });
  assert.deepEqual(resolveSettings({ url: "https://flag/mcp", token: "f" }, env), { url: "https://flag/mcp", token: "f" });
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(path.join(env.STEALTHLAB_HOME, "config.json")).mode & 0o777, 0o600);
  }
});
