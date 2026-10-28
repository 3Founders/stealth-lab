// The local helper behind the MCP console (`kell console`): a loopback HTTP server that does what a browser
// tab cannot -- register kell with Claude Code, Cursor and Codex, keep provider keys and local models, and stream a
// model's reply -- by calling the code `install`, `setup` and the dispatch profiles already use. The API it serves is the
// one in .scratch/prompts/mcp_console_frontend_brief.md; the page is served from --ui-dir (or any origin in --allow-origin).
//
// Why it is built this way, including its limits:
//   * Loopback only, and three checks on every request: the per-run token (header or ?t=), the Host header (DNS
//     rebinding), and the Origin header when a browser sends one. A web page the user happens to have open cannot drive it.
//   * It only ever touches the files `install` and `setup` already touch, plus ONE named variable in the keys file. A key
//     comes back from no route: lists show the last four characters, and no error message carries a value.
//   * Install runs the real CLI as a child process, so there is one implementation of setup and the console cannot drift
//     from it. The cost is that "what changed" is the CLI's own output, not a structured diff.
//   * It is a playground for streamed replies, not the dispatch path: nothing here is checked by tests.
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { readConfig, resolveSettings, writeConfig } from "./config.mjs";
import { clients } from "./clients.mjs";
import { checklist, keyIsSet, probeServer } from "./setup.mjs";
import { envFilePath, isKeyName, readEnvFileVar, removeEnvFileVar, writeEnvFileVar } from "./envfile.mjs";
import { execConfigPath, readExecConfig, readJson, writePrivate } from "./exec/store.mjs";
import { isLoopbackUrl, normaliseProfile, profileKey } from "./executors/profile.mjs";
import { costOf } from "./spend.mjs";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "kell.mjs");
const SUPPORTED = ["claude-code", "cursor", "codex"];
const HOOKS = { "claude-code": true, cursor: true, codex: false };
const NOTES = {
  "claude-code": "MCP tools and per-prompt hooks. Restart Claude Code.",
  cursor: "MCP tools and hooks. Name your Cursor model; close every Cursor window and reopen.",
  codex: "MCP tools. Hooks for Codex are not integrated yet. Restart Codex.",
};
const MAX_BODY = 64 * 1024;

const home = (env) => env.HOME || env.USERPROFILE || os.homedir();
const last4 = (v) => (String(v || "").length >= 8 ? String(v).slice(-4) : "");

// Where each tool keeps what we write, to tell "installed" from "detected".
function installedMarks(env) {
  const h = home(env);
  const codexHome = env.CODEX_HOME || path.join(h, ".codex");
  const claude = env.CLAUDE_CONFIG_DIR ? path.join(env.CLAUDE_CONFIG_DIR, "settings.json") : path.join(h, ".claude", "settings.json");
  return {
    "claude-code": [[claude, /kell|stealthlab-mcp|hook-prompt/]],
    cursor: [[path.join(h, ".cursor", "hooks.json"), /kell|stealthlab/i], [path.join(h, ".cursor", "mcp.json"), /kell|stealthlab/i]],
    codex: [[path.join(codexHome, "config.toml"), /\[mcp_servers\.(stealthlab|kell)\]/]],
  };
}
function isInstalled(id, env) {
  return (installedMarks(env)[id] || []).some(([file, re]) => { try { return re.test(fs.readFileSync(file, "utf8")); } catch { return false; } });
}

// Run the real CLI. -> {status, text}. Never throws.
function runCli(args, env, timeoutMs = 90000) {
  return new Promise((resolve) => {
    let text = "";
    const child = spawn(process.execPath, [BIN, ...args], { env, windowsHide: true });
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.on("data", (d) => { text += d; });
    child.stderr.on("data", (d) => { text += d; });
    child.on("error", (err) => { text += err.message; });
    child.on("close", (status) => { clearTimeout(timer); resolve({ status, text: text.replace(/\x1b\[[0-9;]*m/g, "") }); });
  });
}

// What a provider's refusal means to a person. Never the response body (it can echo what was sent).
export function explainFailure({ status, code, name }) {
  if (status === 401 || status === 402 || status === 403) return "The provider refused the key. Check that it is right and has credit.";
  if (status === 429) return "The provider is rate limiting this key. Wait a minute, or use another key.";
  if (status === 404) return "The provider does not know this model name at that address.";
  if (status >= 500) return "The provider had an error. Try again shortly.";
  if (name === "TimeoutError" || name === "AbortError") return "The model did not answer in time.";
  if (code === "ECONNREFUSED" || code === "ENOTFOUND" || code === "ECONNRESET") return "Could not reach that address. Is the server running?";
  return "The request failed.";
}
const failureOf = (err) => explainFailure({ name: err?.name, code: err?.cause?.code || err?.code });

function readExecRaw(env) {
  const raw = readJson(execConfigPath(env), {}) || {};
  raw.executors = raw.executors && typeof raw.executors === "object" ? raw.executors : {};
  raw.profiles = raw.profiles && typeof raw.profiles === "object" ? raw.profiles : {};
  return raw;
}
function writeExecRaw(env, raw) { writePrivate(execConfigPath(env), JSON.stringify(raw, null, 2) + "\n"); }

function keyValue(name, env) {
  return (env[name] && String(env[name]).trim()) || readEnvFileVar(name, env) || "";
}

function describeModel(p, env) {
  const local = !p.key_env;
  const v = local ? "" : keyValue(p.key_env, env);
  return { name: p.name, model_id: p.model_id, base_url: p.openai_base_url || p.anthropic_base_url || null, key_env: p.key_env || null,
           local: local || isLoopbackUrl(p.openai_base_url), key_set: local ? true : Boolean(v), key_last4: last4(v) || null,
           input_per_mtok: p.input_per_mtok ?? null, output_per_mtok: p.output_per_mtok ?? null, tier: p.tier };
}

export function createConsole({ env = process.env, token = crypto.randomBytes(18).toString("hex"), uiDir = null, allowOrigins = [],
                                fetchImpl = globalThis.fetch, cli = runCli } = {}) {
  let port = 0;
  const hosts = () => new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  const origins = () => new Set([`http://127.0.0.1:${port}`, `http://localhost:${port}`, ...allowOrigins]);
  const same = (a, b) => { const x = Buffer.from(String(a || "")), y = Buffer.from(b); return x.length === y.length && crypto.timingSafeEqual(x, y); };

  const send = (res, status, body, type = "application/json") => {
    res.writeHead(status, { "content-type": type, "cache-control": "no-store", "x-content-type-options": "nosniff" });
    res.end(typeof body === "string" ? body : JSON.stringify(body));
  };
  const readBody = (req) => new Promise((resolve, reject) => {
    let n = 0; const chunks = [];
    req.on("data", (c) => { n += c.length; if (n > MAX_BODY) { reject(new Error("body too large")); req.destroy(); } else chunks.push(c); });
    req.on("end", () => { try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {}); } catch { reject(new Error("body is not JSON")); } });
    req.on("error", reject);
  });

  // The router's own feed: what find_ways (and call_model, report_result, use_tool) returned, as the MCP records it.
  // The page asks the helper, never the MCP, so the page needs no CORS and no MCP address; the MCP only serves it to
  // a loopback caller.
  async function activity(req, res, url) {
    const { url: mcp } = resolveSettings({}, env);
    const fail = (status, error) => send(res, status, { error });
    if (!mcp) return fail(424, "no MCP address is set");
    const feed = new URL(mcp); feed.pathname = "/activity"; feed.search = "";
    const after = Number(url.searchParams.get("after")) || 0;
    if (after > 0) feed.searchParams.set("after", String(after));
    const ac = new AbortController();
    res.on("close", () => ac.abort());
    let up;
    try { up = await fetchImpl(feed, { redirect: "error", signal: ac.signal, headers: { accept: "text/event-stream" } }); }
    catch (err) { return ac.signal.aborted ? undefined : fail(424, /fetch failed|ECONNREFUSED/.test(String(err?.message)) ? "The MCP is not running at its address." : "The MCP could not be reached."); }
    if (up.status === 403) return fail(424, "The MCP only shows this feed to a local caller.");
    if (up.status === 404) return fail(424, "This MCP is older than the activity feed. Restart it from this checkout.");
    if (!up.ok || !up.body) return fail(424, `The MCP answered ${up.status}.`);
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", "x-content-type-options": "nosniff" });
    try {
      const reader = up.body.getReader();
      for (;;) { const { value, done } = await reader.read(); if (done) break; if (!res.write(value)) await new Promise((r) => res.once("drain", r)); }
    } catch { /* the page closed, or the MCP stopped: either way the stream just ends */ }
    res.end();
  }

  async function readiness() {
    const settings = resolveSettings({}, env);
    const server = await probeServer(settings.url, settings.token).catch((e) => ({ ok: false, detail: e.message }));
    let models = [], execOk = true, execDetail = "", profiles = {};
    try { const cfg = readExecConfig(env); profiles = cfg.profiles; models = Object.values(cfg.executors).flatMap((e) => (Array.isArray(e?.models) ? e.models : []).map(String)); if (!models.length) { execOk = false; execDetail = "no models listed"; } }
    catch (err) { execOk = false; execDetail = err.message; }
    const keyNames = [...new Set(Object.values(profiles).map((p) => p.key_env).filter(Boolean))];
    const first = keyNames[0] || "GENERAL_COMPUTE_API_KEY";
    const cfg = readConfig(env);
    const wantsCursor = isInstalled("cursor", env);
    const res = checklist({ server, hasToken: Boolean(settings.token), execOk, execDetail, models, keyEnv: first,
                            key: { set: Boolean(keyValue(first, env)) || keyIsSet(first, { env }).set, where: "" }, dispatch: cfg.dispatch !== false,
                            cursorModel: cfg.cursor_model, wantsCursor });
    for (const k of keyNames.slice(1)) {
      const set = Boolean(keyValue(k, env)) || keyIsSet(k, { env }).set;
      res.items.push({ name: `key ${k}`, ok: set, detail: set ? "found" : "not set", fix: set ? undefined : "add it under Keys" });
    }
    return res.items.map((i) => ({ name: i.name, ok: i.ok, detail: i.detail, ...(i.fix ? { fix: i.fix } : {}) }));
  }

  async function chat(req, res, body) {
    const names = Array.isArray(body.models) ? body.models.map(String).slice(0, 4) : [];
    const messages = Array.isArray(body.messages) ? body.messages.slice(-40) : [];
    if (!names.length || !messages.length) return send(res, 400, { error: "give models and messages" });
    let cfg; try { cfg = readExecConfig(env); } catch (e) { return send(res, 500, { error: "exec.json is not valid" }); }
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive", "x-content-type-options": "nosniff" });
    const emit = (o) => { if (!res.writableEnded) res.write(`data: ${JSON.stringify(o)}\n\n`); };
    const ac = new AbortController();
    res.on("close", () => ac.abort());
    await Promise.all(names.map(async (name) => {
      const p = cfg.profiles[name];
      if (!p?.openai_base_url) return emit({ model: name, error: "That model is not set up for chat (it needs an OpenAI-compatible address)." });
      const t0 = Date.now(); let first = null, usage = null;
      try {
        const r = await fetchImpl(`${p.openai_base_url}/chat/completions`, {
          method: "POST", redirect: "error", signal: AbortSignal.any([ac.signal, AbortSignal.timeout(180000)]),
          headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(p, env)}` },
          body: JSON.stringify({ model: p.model_id, messages, stream: true, stream_options: { include_usage: true }, ...(p.request_extras || {}) }) });
        if (!r.ok) return emit({ model: name, error: explainFailure({ status: r.status }) });
        const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
        for (;;) {
          const { value, done } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim(); if (data === "[DONE]") continue;
            let j; try { j = JSON.parse(data); } catch { continue; }
            if (j.usage) usage = j.usage;
            const delta = j.choices?.[0]?.delta?.content;
            if (delta) { if (first === null) first = Date.now() - t0; emit({ model: name, delta }); }
          }
        }
        emit({ model: name, done: true, ttft_ms: first, total_ms: Date.now() - t0, tokens_in: usage?.prompt_tokens ?? null, tokens_out: usage?.completion_tokens ?? null,
               cost_usd: usage ? costOf(p, usage) : null });
      } catch (err) {
        if (!ac.signal.aborted) emit({ model: name, error: err?.message === "no key" ? "no key" : /not set/.test(String(err?.message)) ? "The key for this model is not set. Add it under Keys." : failureOf(err) });
      }
    }));
    res.end();
  }

  async function route(req, res, url) {
    const m = req.method, p = url.pathname;
    if (m === "GET" && p === "/health") return send(res, 200, { ok: true });
    if (m === "GET" && p === "/clients") {
      const all = new Map(clients({ env }).map((c) => [c.id, c]));
      return send(res, 200, SUPPORTED.map((id) => ({ id, label: all.get(id)?.label || id, detected: Boolean(all.get(id)?.detect()), installed: isInstalled(id, env), hooks: HOOKS[id], notes: NOTES[id] })));
    }
    if (m === "POST" && (p === "/install" || p === "/uninstall")) {
      const b = await readBody(req);
      const ids = Array.isArray(b.clients) ? b.clients.map(String) : [];
      const bad = ids.filter((i) => !SUPPORTED.includes(i));
      if (!ids.length || bad.length) return send(res, 400, { error: `clients must be from: ${SUPPORTED.join(", ")}` });
      if (b.cursor_model !== undefined && b.cursor_model !== null && !/^[\w.:/@+-]{1,80}$/.test(String(b.cursor_model))) return send(res, 400, { error: "that is not a model name" });
      const out = [];
      for (const id of ids) {
        const args = p === "/install" ? ["install", "--client", id, ...(HOOKS[id] ? ["--with-exec"] : []), ...(b.dry_run ? ["--dry-run"] : [])] : ["uninstall", "--client", id];
        const r = await cli(args, env);
        const ok = r.status === 0 && !/\n\s*FAIL\s/.test(r.text);
        const where = (/\bok\s+[^\n]*?\s{2,}(\S.*)/.exec(r.text) || [])[1];
        if (ok && id === "cursor" && p === "/install" && !b.dry_run && b.cursor_model) writeConfig({ cursor_model: String(b.cursor_model) }, env);
        out.push({ client: id, ok, changed_files: b.dry_run || !ok || !where ? [] : [where.trim()], ...(b.dry_run ? { diff: r.text.trim() } : {}),
                   ...(ok ? {} : { error: r.text.trim().split("\n").filter(Boolean).slice(-3).join(" "), fix: "Run `kell doctor`, then try again." }) });
      }
      return send(res, 200, out);
    }
    if (m === "GET" && p === "/readiness") return send(res, 200, await readiness());
    if (m === "GET" && p === "/models") { try { return send(res, 200, Object.values(readExecConfig(env).profiles).map((x) => describeModel(x, env))); } catch (e) { return send(res, 500, { error: "exec.json is not valid" }); } }
    if (m === "POST" && p === "/models") {
      const b = await readBody(req);
      const name = String(b.name || "").trim();
      if (!/^[A-Za-z0-9][\w.-]{0,63}$/.test(name)) return send(res, 400, { error: "the name must be letters, digits, dots, dashes" });
      const raw = { model_id: b.model_id, openai_base_url: b.openai_base_url, ...(b.key_env ? { key_env: b.key_env } : {}),
                    ...(b.input_per_mtok !== undefined && b.input_per_mtok !== null ? { input_per_mtok: b.input_per_mtok, output_per_mtok: b.output_per_mtok } : {}),
                    ...(b.no_system_role ? { no_system_role: true } : {}) };
      try { normaliseProfile(name, raw); } catch (e) { return send(res, 400, { error: e.message }); }
      const cur = readExecRaw(env);
      cur.profiles[name] = raw;
      cur.executors.stealth = cur.executors.stealth || { models: [] };
      const list = Array.isArray(cur.executors.stealth.models) ? cur.executors.stealth.models : [];
      if (!list.includes(name)) list.push(name);
      cur.executors.stealth.models = list;
      writeExecRaw(env, cur);
      return send(res, 200, { ok: true, name });
    }
    let mm;
    if (m === "DELETE" && (mm = /^\/models\/([^/]+)$/.exec(p))) {
      const name = decodeURIComponent(mm[1]); const cur = readExecRaw(env);
      if (!cur.profiles[name]) return send(res, 404, { error: "no such model" });
      delete cur.profiles[name];
      for (const e of Object.values(cur.executors)) if (Array.isArray(e?.models)) e.models = e.models.filter((x) => x !== name);
      writeExecRaw(env, cur);
      return send(res, 200, { ok: true });
    }
    if (m === "POST" && (mm = /^\/models\/([^/]+)\/test$/.exec(p))) {
      const name = decodeURIComponent(mm[1]); let prof;
      try { prof = readExecConfig(env).profiles[name]; } catch { return send(res, 500, { error: "exec.json is not valid" }); }
      if (!prof?.openai_base_url) return send(res, 404, { ok: false, error: "no such model, or it has no OpenAI-compatible address" });
      const t0 = Date.now();
      try {
        const r = await fetchImpl(`${prof.openai_base_url}/chat/completions`, { method: "POST", redirect: "error", signal: AbortSignal.timeout(30000),
          headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(prof, env)}` },
          body: JSON.stringify({ model: prof.model_id, messages: [{ role: "user", content: "Reply with the word OK." }], max_tokens: 16, temperature: 0, ...(prof.request_extras || {}) }) });
        if (!r.ok) return send(res, 200, { ok: false, latency_ms: Date.now() - t0, error: explainFailure({ status: r.status }) });
        return send(res, 200, { ok: true, latency_ms: Date.now() - t0 });
      } catch (err) {
        return send(res, 200, { ok: false, latency_ms: Date.now() - t0, error: /is not set/.test(String(err?.message)) ? "The key for this model is not set. Add it under Keys." : failureOf(err) });
      }
    }
    if (m === "GET" && p === "/keys") {
      let names = [];
      try { names = [...new Set(Object.values(readExecConfig(env).profiles).map((x) => x.key_env).filter(Boolean))]; } catch { /* none */ }
      return send(res, 200, names.map((n) => { const v = keyValue(n, env); return { name: n, set: Boolean(v) || keyIsSet(n, { env }).set, last4: last4(v) || null }; }));
    }
    if ((mm = /^\/keys\/([A-Z][A-Z0-9_]{1,63})$/.exec(p)) && (m === "PUT" || m === "DELETE")) {
      const name = mm[1];
      if (m === "PUT") {
        const b = await readBody(req);
        try { writeEnvFileVar(name, b.value, env); } catch (e) { return send(res, 400, { error: String(e.message).replace(String(b.value ?? "\0"), "") }); }
        return send(res, 200, { ok: true, last4: last4(b.value) || null });
      }
      return send(res, 200, { ok: true, removed: removeEnvFileVar(name, env) });
    }
    if (m === "GET" && p === "/activity/stream") return activity(req, res, url);
    if (m === "POST" && p === "/chat") return chat(req, res, await readBody(req));
    if (m === "GET" && uiDir) return serveStatic(res, p);
    if (m === "GET" && p === "/") return send(res, 200, { name: "stealthlab console", ui: false, note: "no --ui-dir given; the API is here" });
    return send(res, 404, { error: "not found" });
  }

  function serveStatic(res, p) {
    const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2" };
    const root = path.resolve(uiDir);
    let file = path.resolve(root, "." + decodeURIComponent(p === "/" ? "/index.html" : p));
    if (file !== root && !file.startsWith(root + path.sep)) return send(res, 403, { error: "forbidden" });
    try { if (fs.statSync(file).isDirectory()) file = path.join(file, "index.html"); } catch { /* 404 below */ }
    try { const data = fs.readFileSync(file); res.writeHead(200, { "content-type": types[path.extname(file)] || "application/octet-stream", "cache-control": "no-store" }); res.end(data); }
    catch { send(res, 404, { error: "not found" }); }
  }

  const server = http.createServer(async (req, res) => {
    try {
      if (!hosts().has(String(req.headers.host || ""))) return send(res, 403, { error: "bad host" });
      if (req.headers.origin && !origins().has(req.headers.origin)) return send(res, 403, { error: "bad origin" });
      const url = new URL(req.url, `http://${req.headers.host}`);
      const given = (/^Bearer (.+)$/.exec(req.headers.authorization || "") || [])[1] || req.headers["x-console-token"] || url.searchParams.get("t");
      const isPage = req.method === "GET" && !["/health"].includes(url.pathname) && (url.pathname === "/" || /\.[a-z0-9]+$/.test(url.pathname));
      if (!isPage && url.pathname !== "/health" && !same(given, token)) return send(res, 401, { error: "missing or wrong console token" });
      if (isPage && uiDir === null && !same(given, token)) return send(res, 401, { error: "missing or wrong console token" });
      await route(req, res, url);
    } catch (err) {
      if (!res.headersSent) send(res, /too large|not JSON/.test(String(err?.message)) ? 400 : 500, { error: /too large|not JSON/.test(String(err?.message)) ? err.message : "something went wrong" });
      else res.end();
    }
  });

  return {
    server, token,
    listen: (wantPort = 0) => new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(wantPort, "127.0.0.1", () => { port = server.address().port; resolve({ port, url: `http://127.0.0.1:${port}/?t=${token}` }); });
    }),
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}
