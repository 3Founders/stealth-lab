import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { parseSse, Relay } from "../lib/proxy.mjs";

// A tiny Streamable-HTTP-shaped server: initialize -> JSON + session id,
// tools/call -> SSE, notifications -> 202, GET -> 405.
function fakeServer() {
  const seen = [];
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push({ method: req.method, headers: req.headers, body });
      if (req.method === "GET") return res.writeHead(405).end();
      if (req.method === "DELETE") return res.writeHead(200).end();
      const msg = JSON.parse(body);
      if (msg.method === "initialize") {
        res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "sess-1" });
        return res.end(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { protocolVersion: "2099-01-01", serverInfo: { name: "s" }, capabilities: {} } }));
      }
      if (msg.id === undefined) return res.writeHead(202).end();
      if (msg.method === "boom") return res.writeHead(401).end("nope");
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write(`event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n`);
      res.end(`data: ${JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { ok: true } })}\r\n\r\n`);
    });
  });
  return new Promise((r) => srv.listen(0, "127.0.0.1", () => r({ srv, seen, url: `http://127.0.0.1:${srv.address().port}/mcp` })));
}

test("relay carries session id, protocol version and token; relays JSON, SSE and HTTP errors", async () => {
  const { srv, seen, url } = await fakeServer();
  const lines = [];
  const relay = new Relay({ url, token: "tok", userAgent: "t", log: () => {}, write: (s) => lines.push(JSON.parse(s)) });
  relay.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }));
  relay.send(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }));
  relay.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call", params: {} }));
  relay.send(JSON.stringify({ jsonrpc: "2.0", id: 3, method: "boom" }));
  relay.send("not json");
  await relay.close();
  srv.close();

  const byId = Object.fromEntries(lines.filter((l) => "id" in l).map((l) => [l.id, l]));
  assert.equal(byId[1].result.protocolVersion, "2099-01-01");
  assert.deepEqual(byId[2].result, { ok: true });
  assert.match(byId[3].error.message, /HTTP 401.*stealthlab-mcp login/);
  assert.equal(byId[null].error.code, -32700);
  assert.ok(lines.some((l) => l.method === "notifications/progress"));

  const posts = seen.filter((s) => s.method === "POST");
  assert.equal(posts[0].headers["mcp-session-id"], undefined);
  for (const p of posts.slice(1)) {
    assert.equal(p.headers["mcp-session-id"], "sess-1");
    assert.equal(p.headers["mcp-protocol-version"], "2099-01-01");
  }
  for (const p of posts) {
    assert.equal(p.headers.authorization, "Bearer tok");
    assert.match(p.headers.accept, /application\/json.*text\/event-stream/);
  }
  assert.ok(seen.some((s) => s.method === "DELETE" && s.headers["mcp-session-id"] === "sess-1"));
});

test("unreachable server -> JSON-RPC error for requests only", async () => {
  const lines = [];
  const logs = [];
  const relay = new Relay({ url: "http://127.0.0.1:1/mcp", userAgent: "t", log: (m) => logs.push(m), write: (s) => lines.push(JSON.parse(s)) });
  relay.send(JSON.stringify({ jsonrpc: "2.0", id: 7, method: "tools/list" }));
  relay.send(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }));
  await relay.close();
  assert.equal(lines.length, 1);
  assert.equal(lines[0].id, 7);
  assert.match(lines[0].error.message, /unreachable/);
  assert.equal(logs.length, 1);
});

test("parseSse handles split chunks, multi-line data and comments", () => {
  const events = [];
  const feed = parseSse((e) => events.push(e));
  feed(": keepalive\n\nda");
  feed("ta: a\ndata: b\nid: 4\n");
  feed("\nevent: x\ndata: c\n\n");
  assert.deepEqual(events, [
    { event: "message", data: "a\nb", id: "4" },
    { event: "x", data: "c", id: "4" },
  ]);
});
