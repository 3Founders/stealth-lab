import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { attachServerStatus, computeStatus, parseGoalStatusLine, runStatusCli } from "../lib/status.mjs";
import { readHostedResource } from "../lib/exec/hosted.mjs";
import { testEnv, tmpDir } from "./fixtures/exec/helpers.mjs";

const GOAL = "11111111-1111-1111-1111-111111111111";
const LINE = `GOAL_STATUS|${GOAL}|verified|guarantees=2|verified=2|failed=0|contested=0|open=0|assumptions=1|closed=0|client_checks=pass:2|why=all 2 guarantee claim(s) have passing evidence and none fails`;
const RESOURCE = `# claims for goal ${GOAL} (Rotate the signing keys)\n${LINE}\nCLAIM|c1|supported|guarantee|repository|keys rotate|version=1\n`;

function repo({ withGoal = true } = {}) {
  const root = tmpDir("st-server-");
  fs.mkdirSync(path.join(root, ".kell"), { recursive: true });
  fs.writeFileSync(path.join(root, ".kell", "library.md"), [
    `GOAL|L-aaaa1111|Rotate keys|unit=.|g=${withGoal ? GOAL : "-"}|outcome=pass|status=current|verified_at=2026-10-20|route=-|tags=-`,
    "PROC|L-aaaa1111.p1|fix|p=-|solution=-|touches=-",
  ].join("\n") + "\n");
  return root;
}

// a fetch that speaks just enough MCP: initialize, initialized, resources/read, DELETE
function fakeFetch({ text = RESOURCE, fail = false } = {}) {
  const seen = [];
  const reply = (status, body, headers = {}) => ({ ok: status < 400, status, headers: { get: (k) => headers[k.toLowerCase()] ?? null }, text: async () => body });
  const fn = async (url, init = {}) => {
    if (fail) throw new Error("network down");
    if (init.method === "DELETE") return reply(200, "");
    const msg = JSON.parse(init.body || "{}");
    seen.push(msg);
    if (msg.method === "initialize") return reply(200, JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { protocolVersion: "2025-06-18" } }), { "mcp-session-id": "s1" });
    if (msg.id === undefined) return reply(202, "");
    if (msg.method === "resources/read") return reply(200, JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { contents: [{ uri: msg.params.uri, text }] } }));
    return reply(200, JSON.stringify({ jsonrpc: "2.0", id: msg.id, error: { message: "unknown" } }));
  };
  fn.seen = seen;
  return fn;
}

test("the GOAL_STATUS line parses; a resource without one gives null", () => {
  const s = parseGoalStatusLine(RESOURCE);
  assert.deepEqual([s.status, s.guarantees, s.verified, s.failed, s.contested, s.open, s.assumptions, s.closed, s.client_checks],
    ["verified", 2, 2, 0, 0, 0, 1, 0, "pass:2"]);
  assert.match(s.why, /all 2 guarantee claim/);
  assert.equal(parseGoalStatusLine("# claims for goal x\nCLAIM|a|b|c|d|e|version=1\n"), null);
  assert.equal(parseGoalStatusLine(""), null);
});

test("readHostedResource speaks resources/read in its own short session and returns the text", async () => {
  const f = fakeFetch();
  const env = testEnv({ url: "http://127.0.0.1:9/mcp" });
  const text = await readHostedResource(`stealth://goals/${GOAL}/claims`, { env, fetchImpl: f });
  assert.equal(text, RESOURCE.trimEnd() === text ? text : RESOURCE);
  assert.ok(text.includes("GOAL_STATUS|"));
  const read = f.seen.find((m) => m.method === "resources/read");
  assert.deepEqual(read.params, { uri: `stealth://goals/${GOAL}/claims` });
  assert.deepEqual(f.seen.map((m) => m.method).filter(Boolean), ["initialize", "notifications/initialized", "resources/read"]);
});

test("--server adds the server's computed status to --json, and changes no local status", async () => {
  const root = repo();
  const before = computeStatus(root).targets[0];
  const f = fakeFetch();
  const out = [];
  const code = await runStatusCli(["--root", root, "--json", "--server"], { out: (s) => out.push(s), err: () => {}, env: testEnv({ url: "http://127.0.0.1:9/mcp" }), fetchImpl: f });
  assert.equal(code, 0);
  const json = JSON.parse(out[0]);
  const row = json.targets[0];
  assert.equal(row.goal_id, GOAL);
  assert.equal(row.server.status, "verified");
  assert.equal(row.server.guarantees, 2);
  assert.deepEqual(json.server, { read: 1, failed: 0 });
  assert.equal(row.status, before.status, "the local status is the local status");
  assert.equal(row.why, before.why);
  assert.equal(f.seen.filter((m) => m.method === "resources/read").length, 1, "one request per Goal");
});

test("without --server nothing is sent, and a server failure leaves the rows as they were", async () => {
  const root = repo();
  const f = fakeFetch();
  const out = [];
  await runStatusCli(["--root", root, "--json"], { out: (s) => out.push(s), err: () => {}, env: testEnv({ url: "http://127.0.0.1:9/mcp" }), fetchImpl: f });
  assert.equal(f.seen.length, 0, "read-only and offline by default");
  assert.equal(JSON.parse(out[0]).targets[0].server, undefined);

  const down = fakeFetch({ fail: true });
  const out2 = [], errs = [];
  const code = await runStatusCli(["--root", root, "--json", "--server"], { out: (s) => out2.push(s), err: (s) => errs.push(s), env: testEnv({ url: "http://127.0.0.1:9/mcp" }), fetchImpl: down });
  assert.equal(code, 0, "an unreachable server is not an error");
  const json = JSON.parse(out2[0]);
  assert.equal(json.targets[0].server, undefined);
  assert.deepEqual(json.server, { read: 0, failed: 1 });
  assert.ok(errs.some((l) => /status --server: L-aaaa1111/.test(l)));
  assert.equal(json.targets[0].status, computeStatus(root).targets[0].status);
});

test("only entries that name a server Goal are asked; the table shows the server's status beside the local one", async () => {
  const none = repo({ withGoal: false });
  const f = fakeFetch();
  const r = computeStatus(none);
  assert.deepEqual(await attachServerStatus(r, { env: testEnv({ url: "http://127.0.0.1:9/mcp" }), fetchImpl: f }), { read: 0, failed: 0 });
  assert.equal(f.seen.length, 0);
  const root = repo();
  const out = [];
  await runStatusCli(["--root", root, "--server"], { out: (s) => out.push(s), err: () => {}, env: testEnv({ url: "http://127.0.0.1:9/mcp" }), fetchImpl: fakeFetch() });
  assert.match(out[0], /L-aaaa1111 +library .*\[server: verified\]/);
});

test("a resource with no GOAL_STATUS line, or an error reply, counts as a failed read, not a status", async () => {
  const root = repo();
  const r = computeStatus(root);
  const res = await attachServerStatus(r, { env: testEnv({ url: "http://127.0.0.1:9/mcp" }), fetchImpl: fakeFetch({ text: "Resource not found" }) });
  assert.deepEqual(res, { read: 0, failed: 1 });
  assert.equal(r.targets[0].server, undefined);
});
