// Development and test harness for the console. It starts the REAL helper (packaging/npm/lib/console.mjs, imported, not
// copied) inside a throwaway sandbox, with a fake OpenAI-compatible provider on loopback so streaming can be exercised
// with no key and no network. Nothing here is part of the shipped page; the page has no sample-data mode.
//
// The sandbox: its own HOME / STEALTHLAB_HOME / env file under a temp folder, and an install stub that records what it
// was asked to do instead of touching the real Claude Code, Cursor or Codex settings.
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createConsole } from "../../packaging/npm/lib/console.mjs";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const GOOD_KEY = "sk-test-0123456789abcdef";
export const BAD_KEY = "sk-test-refused-000000";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A tiny OpenAI-compatible server: streams `words` with `gapMs` between them, then a usage chunk. Also answers /mcp. */
export async function startFakeProvider({ gapMs = 40, words = ["Hello", " from", " the", " fake", " provider", "."] } = {}) {
  const seen = { auth: [] };
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const raw = Buffer.concat(chunks).toString("utf8");
    if (req.url === "/mcp") { res.writeHead(200, { "content-type": "application/json" }); return res.end(JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} })); }
    if (req.method !== "POST" || !req.url.endsWith("/chat/completions")) { res.writeHead(404); return res.end(); }
    const key = (/^Bearer (.*)$/.exec(req.headers.authorization || "") || [])[1] || "";
    seen.auth.push(key);
    if (key === BAD_KEY) { res.writeHead(401, { "content-type": "application/json" }); return res.end(JSON.stringify({ error: { message: `bad key ${key}` } })); }
    let body = {}; try { body = JSON.parse(raw); } catch { /* keep {} */ }
    if (body.max_tokens === 16) { res.writeHead(200, { "content-type": "application/json" }); return res.end(JSON.stringify({ choices: [{ message: { content: "OK" } }] })); }
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
    const tag = body.model || "model";
    for (const w of words) {
      if (res.destroyed) return;
      res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: w === words[0] ? `[${tag}] ${w}` : w } }] })}\n\n`);
      await sleep(gapMs);
    }
    res.write(`data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 12, completion_tokens: words.length } })}\n\n`);
    res.write("data: [DONE]\n\n");
    res.end();
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  return { server, port, base: `http://127.0.0.1:${port}`, seen, close: () => new Promise((r) => { server.closeAllConnections?.(); server.close(() => r()); }) };
}

/** The helper in a sandbox. `models` is seeded into exec.json; `calls` records every install/uninstall the page asked for. */
export async function startDevHelper({ uiDir = null, allowOrigins = [], port = 0, provider, withKey = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kel-console-"));
  const home = path.join(dir, "home");
  fs.mkdirSync(home, { recursive: true });
  const envFile = path.join(dir, "keys.env");
  const env = { PATH: process.env.PATH || "", HOME: home, USERPROFILE: home, STEALTHLAB_HOME: path.join(dir, "kell"), STEALTHLAB_ENV_FILE: envFile,
                STEALTHLAB_MCP_URL: `${provider.base}/mcp` };
  fs.mkdirSync(env.STEALTHLAB_HOME, { recursive: true });
  const execFile = path.join(env.STEALTHLAB_HOME, "exec.json");
  fs.writeFileSync(execFile, JSON.stringify({
    executors: { stealth: { models: ["fake-remote", "fake-local"] } },
    profiles: {
      "fake-remote": { model_id: "fake-remote-1", key_env: "FAKE_API_KEY", openai_base_url: `${provider.base}/v1`, input_per_mtok: 1, output_per_mtok: 2 },
      "fake-local": { model_id: "fake-local-1", openai_base_url: `${provider.base}/v1` },
    },
  }, null, 2));
  if (withKey) fs.writeFileSync(envFile, `FAKE_API_KEY=${GOOD_KEY}\n`, { mode: 0o600 });
  const calls = [];
  const cli = async (args) => {
    calls.push(args);
    const id = args[args.indexOf("--client") + 1];
    const dry = args.includes("--dry-run");
    const text = dry ? `would write ${id} settings (dry run)\n  ok    ${id}  ${home}/${id}.json` : `  ok    ${id}  ${home}/${id}.json`;
    return { status: id === "codex" && process.env.KEL_FAIL_CODEX ? 1 : 0, text };
  };
  const c = createConsole({ env, cli, uiDir, allowOrigins });
  const { port: p, url } = await c.listen(port);
  return { console: c, port: p, url, token: c.token, base: `http://127.0.0.1:${p}`, calls, env, dir, execFile, envFile,
           close: async () => { await c.close(); fs.rmSync(dir, { recursive: true, force: true }); } };
}
