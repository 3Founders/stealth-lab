// `kell setup`: the whole first-time install in one command, and an honest checklist of what is still missing.
//
// Why: the install used to be nine separate steps (a server, a token, install, a hand-copied exec.json, a provider key,
// the Cursor model, a check command, a restart, hope). Several of them failed silently: a wrong key name or a missing
// Cursor model just meant "no dispatch" with nothing said. This module does the parts a program can do (write exec.json
// from a template, remember the Cursor model in config.json, read the token from a file) and TESTS the parts it cannot
// do for the user (is the server answering, is the provider key set where the agent will read it, does exec.json parse),
// then says exactly what to run for each one that is not ready.
//
// What it never does: store a provider key. The key stays in an environment variable whose NAME is in exec.json
// (see lib/executors/profile.mjs); setup only checks the variable exists, and for Windows it checks the user-level
// environment (what a freshly started Cursor inherits), not just this shell's.
import fs from "node:fs";
import path from "node:path";
import { execFile, spawnSync } from "node:child_process";
import { execConfigPath, readExecConfig } from "./exec/store.mjs";
import { readEnvFileVar } from "./envfile.mjs";

export const DEFAULT_KEY_ENV = "GENERAL_COMPUTE_API_KEY";
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

// The open models that were run end to end (docs: experiments/abtest/exec.json; prices are list prices per million tokens).
// The open models run in kell's own step agent: with fair sampling, reasoning kept across tool calls and no stall cut-offs it
// solved the same issues as lean Claude Code (through lib/anthropic_bridge.mjs) for a third to a quarter of the cost
// (GLM-5.3-flash replay on sqlglot 508/518/527, 2026-10-28). claudeCode: true runs them inside Claude Code instead.
export function execTemplate(keyEnv = DEFAULT_KEY_ENV, { claudeCode = false } = {}) {
  if (!NAME.test(keyEnv)) throw new Error(`--key-env must be an environment variable NAME, not a key (got "${String(keyEnv).slice(0, 12)}...")`);
  const gc = (id, i, o, extra = {}) => ({ model_id: id, key_env: keyEnv, openai_base_url: "https://api.generalcompute.com/v1",
                                          input_per_mtok: i, output_per_mtok: o, ...extra });
  return {
    executors: { [claudeCode ? "claude" : "stealth"]: { models: ["gemma-4-31b-it", "minimax-m2-7"] } },
    profiles: {
      // each at its vendor's sampling (temperature 1.0); General Compute's own default is greedy for gemma
      "gemma-4-31b-it": gc("gemma-4-31B-it", 0.09, 0.34, { no_system_role: true, temperature: 1.0, top_p: 0.95 }),
      "minimax-m2-7": gc("minimax-m2.7", 0.21, 0.84, { temperature: 1.0, top_p: 0.95 }),
    },
    max_concurrent_runs: 2,
  };
}

// Writes exec.json only when there is none (never overwrites a file the user edited).
export function ensureExecConfig({ env = process.env, keyEnv = DEFAULT_KEY_ENV } = {}) {
  const file = execConfigPath(env);
  if (fs.existsSync(file)) return { file, created: false };
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(execTemplate(keyEnv), null, 2) + "\n", { mode: 0o600 });
  return { file, created: true };
}

// Is the variable set where the agent will read it? On Windows a new process inherits the USER environment, which
// `setx` (and the System dialog) write and a running shell does not see until restarted.
export function keyIsSet(name, { env = process.env, platform = process.platform, spawn = spawnSync } = {}) {
  if (String(env[name] || "").trim()) return { set: true, where: "this shell" };
  if (readEnvFileVar(name, env)) return { set: true, where: "your env file" };
  if (platform === "win32") {
    const r = spawn("reg", ["query", "HKCU\\Environment", "/v", name], { encoding: "utf8", windowsHide: true });
    if (r.status === 0 && new RegExp(`\\b${name}\\b`, "i").test(String(r.stdout || ""))) return { set: true, where: "your user environment" };
  }
  return { set: false, where: "" };
}

// The same check without blocking: setup runs it alongside the server probe (the `reg` process costs tens of milliseconds).
export function keyIsSetAsync(name, { env = process.env, platform = process.platform, execFileImpl = execFile } = {}) {
  if (String(env[name] || "").trim()) return Promise.resolve({ set: true, where: "this shell" });
  if (readEnvFileVar(name, env)) return Promise.resolve({ set: true, where: "your env file" });
  if (platform !== "win32") return Promise.resolve({ set: false, where: "" });
  return new Promise((resolve) => {
    execFileImpl("reg", ["query", "HKCU\\Environment", "/v", name], { windowsHide: true }, (err, stdout) => {
      const found = !err && new RegExp(`\\b${name}\\b`, "i").test(String(stdout || ""));
      resolve(found ? { set: true, where: "your user environment" } : { set: false, where: "" });
    });
  });
}

export async function probeServer(url, token, { fetchImpl = fetch, timeoutMs = 15000 } = {}) {
  const headers = { accept: "application/json, text/event-stream", "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const body = { jsonrpc: "2.0", id: 1, method: "initialize",
                 params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "kell-setup", version: "0" } } };
  let res;
  try {
    res = await fetchImpl(url, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) });
  } catch (err) {
    return { ok: false, detail: `unreachable (${err.cause?.message || err.message})` };
  }
  if (res.status === 401 || res.status === 403) return { ok: false, detail: `the server refused the token (HTTP ${res.status})` };
  if (!res.ok) return { ok: false, detail: `HTTP ${res.status}` };
  const sid = res.headers.get("mcp-session-id");
  if (sid) await fetchImpl(url, { method: "DELETE", headers: { ...headers, "mcp-session-id": sid } }).catch(() => {});
  return { ok: true, detail: "answers" };
}

// -> { ready, items: [{ name, ok, detail, fix? }] }. Pure given its inputs, so tests can drive every branch.
export function checklist({ server, hasToken, execOk, execDetail, models, keyEnv, key, dispatch, cursorModel, wantsCursor, central = false, platform = process.platform }) {
  const items = [];
  items.push({ name: "server", ok: server.ok, detail: server.detail, fix: server.ok ? undefined : "start it, or pass --url <https://host/mcp>; check with `kell doctor`" });
  items.push({ name: "token", ok: hasToken, detail: hasToken ? "saved" : "none (reads are anonymous; reporting outcomes needs one)",
               fix: hasToken ? undefined : "kell login   (or: setup --token-file <file>)" });
  if (dispatch) {
    items.push({ name: "exec.json", ok: execOk, detail: execOk ? `${models.length} model(s): ${models.join(", ")}` : execDetail,
                 fix: execOk ? undefined : "fix or delete ~/.stealthlab/exec.json, then run setup again" });
    const setx = platform === "win32" ? `setx ${keyEnv} "<your key>"   then restart the agent` : `export ${keyEnv}=<your key>   (in your shell profile), then restart the agent`;
    // profiles written by `join` (key_from: "stealthlab") carry no provider key: the organization's gateway holds it
    if (central) items.push({ name: "provider key", ok: true, detail: "held by your organization's gateway (nothing to set here)" });
    else items.push({ name: `key ${keyEnv}`, ok: key.set, detail: key.set ? `set in ${key.where}` : "not set", fix: key.set ? undefined : setx });
    if (wantsCursor) {
      items.push({ name: "cursor model", ok: Boolean(cursorModel), detail: cursorModel ? cursorModel : "not set ('auto' is not a model: dispatch stays off)",
                   fix: cursorModel ? undefined : "kell setup --cursor-model <the model you picked in Cursor>" });
    }
  }
  return { ready: items.every((i) => i.ok), items };
}
