// Where the MCP endpoint and optional bearer token come from.
//
// Precedence (highest first): explicit flag -> environment -> the user's
// ~/.stealthlab/config.json -> the default baked into package.json. The token
// deliberately lives ONLY here (0600 file), never in any agent's config, so
// rotating it is one edit and it is never copied into five JSON files.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PKG_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "package.json");

export function readPackage() {
  return JSON.parse(fs.readFileSync(PKG_PATH, "utf8"));
}

export function configDir(env = process.env) {
  return env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab");
}

export function configPath(env = process.env) {
  return path.join(configDir(env), "config.json");
}

export function readConfig(env = process.env) {
  const p = configPath(env);
  if (!fs.existsSync(p)) return {};
  try {
    return JSON.parse(fs.readFileSync(p, "utf8")) || {};
  } catch (err) {
    throw new Error(`${p} is not valid JSON (${err.message}) -- fix or delete it`);
  }
}

export function writeConfig(patch, env = process.env) {
  const next = { ...readConfig(env), ...patch };
  for (const k of Object.keys(next)) if (next[k] === undefined || next[k] === null) delete next[k];
  fs.mkdirSync(configDir(env), { recursive: true, mode: 0o700 });
  const p = configPath(env);
  fs.writeFileSync(p, JSON.stringify(next, null, 2) + "\n", { mode: 0o600 });
  try { fs.chmodSync(p, 0o600); } catch { /* best effort on Windows */ }
  return next;
}

export function normalizeUrl(raw) {
  let u;
  try {
    u = new URL(String(raw).trim());
  } catch {
    throw new Error(`not a valid URL: ${raw}`);
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") throw new Error(`URL must be http(s): ${raw}`);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (u.protocol === "http:" && !loopback) {
    throw new Error(`refusing plain http:// for a non-loopback host (${u.host}) -- use https://`);
  }
  if (u.pathname === "/" || u.pathname === "") u.pathname = "/mcp";
  return u.toString().replace(/\/$/, "");
}

export function resolveSettings({ url, token } = {}, env = process.env) {
  const cfg = readConfig(env);
  const rawUrl = url || env.STEALTHLAB_MCP_URL || cfg.url || readPackage().stealthlab?.defaultMcpUrl || "";
  return {
    url: rawUrl ? normalizeUrl(rawUrl) : "",
    token: token || env.STEALTHLAB_TOKEN || cfg.token || "",
  };
}
