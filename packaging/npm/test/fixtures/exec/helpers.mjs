// Shared test helpers for the executor runtime tests: stub adapters (same interface as
// lib/executors/<id>.mjs), throwaway git repos, a temp STEALTHLAB_HOME, and a mock hosted MCP endpoint.
// Nothing here touches the real home directory or the StealthLab checkout.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const STUB_AGENT = path.join(HERE, "stub_agent.mjs");

const created = [];
process.on("exit", () => {
  for (const d of created) { try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ } }
});

export function tmpDir(prefix = "sl-exec-") {
  const d = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
  created.push(d);
  return d;
}

// scenario: object, or (model) => object
export function stubAdapter({ id = "stub", version = "1.4.2", verifiedWith = { version: "1.0.0", date: "2026-09-27", source: "test" },
                              installed = true, healthy = true, scenario = {} } = {}) {
  return {
    id,
    VERIFIED_WITH: verifiedWith,
    async detect() { return { installed, version: installed ? version : null, bin: installed ? process.execPath : null }; },
    async health() { return { healthy, detail: healthy ? "ok" : "unhealthy" }; },
    buildCommand({ model }) {
      const sc = typeof scenario === "function" ? scenario(model) : scenario;
      return { cmd: process.execPath, args: [STUB_AGENT], env: { EXEC_STUB_SCENARIO: JSON.stringify(sc) } };
    },
    parseOutput({ stdout }) {
      const last = stdout.trim().split(/\r?\n/).reverse().find((l) => l.startsWith("{"));
      try {
        const j = JSON.parse(last);
        return { finalMessage: j.final, learned: j.learned, tokens: { in: 10, out: 5 }, costUsd: null };
      } catch {
        return { finalMessage: stdout.slice(-500), learned: [], tokens: null, costUsd: null };
      }
    },
  };
}

const gitq = (cwd, ...args) => execFileSync("git", args, { cwd, stdio: ["ignore", "pipe", "pipe"] }).toString();

// A throwaway repo: calc.txt ("broken"), other.txt, and check.mjs, which exits 0 only when calc.txt says
// "fixed" and otherwise prints a fake secret (redaction must remove it from the tail).
export function makeRepo() {
  const dir = tmpDir("sl repo-"); // a space in the path, on purpose
  gitq(dir, "init", "-q");
  gitq(dir, "config", "user.email", "t@example.com");
  gitq(dir, "config", "user.name", "t");
  gitq(dir, "config", "core.autocrlf", "false");
  fs.writeFileSync(path.join(dir, "calc.txt"), "broken\n");
  fs.writeFileSync(path.join(dir, "other.txt"), "leave me\n");
  fs.writeFileSync(path.join(dir, "check.mjs"),
    "import fs from 'node:fs';\n" +
    "const t = fs.readFileSync('calc.txt', 'utf8');\n" +
    "if (t.includes('fixed')) { console.log('1 passed'); process.exit(0); }\n" +
    "for (let i = 0; i < 60; i++) console.log('noise line ' + i);\n" +
    "console.log('FAILED: expected fixed; debug key sk-test-ABCDEFGHIJKLMNOPQRSTUVWX');\n" +
    "process.exit(1);\n");
  gitq(dir, "add", "-A");
  gitq(dir, "commit", "-q", "-m", "init");
  return dir;
}

export function git(dir, ...args) { return gitq(dir, ...args); }

export function testEnv({ url = "", token = "tok-test-123456789", extra = {} } = {}) {
  const home = tmpDir("sl-home-");
  return {
    ...process.env,
    STEALTHLAB_HOME: home,
    STEALTHLAB_MCP_URL: url,
    STEALTHLAB_TOKEN: token,
    STEALTHLAB_EXEC_WORKTREE_ROOT: path.join(home, "wt"),
    ...extra,
  };
}

export function writeExecConfig(env, cfg) {
  fs.mkdirSync(env.STEALTHLAB_HOME, { recursive: true });
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify(cfg));
}

export function outboxFiles(env, sub = "") {
  const d = path.join(env.STEALTHLAB_HOME, "outbox", sub);
  try {
    return fs.readdirSync(d).filter((f) => f.endsWith(".json")).map((f) => JSON.parse(fs.readFileSync(path.join(d, f), "utf8")));
  } catch {
    return [];
  }
}

// Mock hosted MCP (Streamable HTTP shape). `tool(name, args)` returns {status?, text?, isError?, rpcError?}.
export function mockHosted(tool) {
  const calls = [];
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      if (req.method === "DELETE") return res.writeHead(200).end();
      const msg = JSON.parse(body || "{}");
      if (msg.method === "initialize") {
        res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "s1" });
        return res.end(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { protocolVersion: "2025-06-18", serverInfo: { name: "mock" }, capabilities: {} } }));
      }
      if (msg.id === undefined) return res.writeHead(202).end();
      const { name, arguments: args } = msg.params || {};
      calls.push({ name, args, auth: req.headers.authorization });
      const r = tool(name, args) || {};
      if (r.status && r.status >= 400) return res.writeHead(r.status).end(r.text || "boom");
      res.writeHead(200, { "content-type": "text/event-stream" });
      const payload = r.rpcError
        ? { jsonrpc: "2.0", id: msg.id, error: { code: -32000, message: r.rpcError } }
        : { jsonrpc: "2.0", id: msg.id, result: { content: [{ type: "text", text: r.text ?? "{}" }], isError: !!r.isError } };
      res.end(`event: message\ndata: ${JSON.stringify(payload)}\n\n`);
    });
  });
  return new Promise((resolve) => srv.listen(0, "127.0.0.1", () => resolve({
    calls, url: `http://127.0.0.1:${srv.address().port}/mcp`, close: () => new Promise((r) => { srv.close(r); srv.closeAllConnections?.(); }),
  })));
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function pidsFrom(file) {
  try {
    return fs.readFileSync(file, "utf8").trim().split(/\r?\n/).filter(Boolean).map((l) => {
      const [role, pid] = l.split(" ");
      return { role, pid: Number(pid) };
    });
  } catch {
    return [];
  }
}

export function alive(pid) {
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; }
}

export async function waitFor(fn, { timeoutMs = 20000, stepMs = 50 } = {}) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await sleep(stepMs);
  }
  throw new Error("waitFor timed out");
}
