// SessionStart hook: when Claude Code starts and the configured kell server is a LOCAL one that is not answering,
// start it, so the MCP the person enabled is up without a separate terminal.
//
// Why only loopback: a hosted URL is somebody else's process to run. Why only a down server: the start script
// replaces whatever holds the port, so it must never run against a healthy one. Honest limit: this needs the
// backend checkout (backend/scripts/start_mcp_local.ps1, Windows) next to this package or named in
// STEALTHLAB_SERVER_START; a plain npm install has neither and the hook does nothing. The server is started
// detached and keeps running after the session ends.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { logHook } from "./subagent_hook.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

export function ensureEnabled(env = process.env) {
  return (env.STEALTHLAB_SERVER_AUTOSTART || "on").toLowerCase() !== "off";
}

export function startScript(env = process.env) {
  const candidates = [env.STEALTHLAB_SERVER_START, path.join(HERE, "..", "..", "..", "backend", "scripts", "start_mcp_local.ps1")];
  return candidates.filter(Boolean).find((p) => fs.existsSync(p)) || null;
}

export async function answers(url, { fetchImpl = fetch, timeoutMs = 1500 } = {}) {
  try {
    const r = await fetchImpl(url, { signal: AbortSignal.timeout(timeoutMs) });
    return r.status > 0;
  } catch {
    return false;
  }
}

// Returns "off" | "remote" | "up" | "no-script" | "started".
export async function ensureServer({ url, env = process.env, fetchImpl, spawnImpl = spawn, platform = process.platform } = {}) {
  if (!ensureEnabled(env)) return "off";
  let u;
  try { u = new URL(url); } catch { return "remote"; }
  if (!LOOPBACK.has(u.hostname)) return "remote";
  if (await answers(url, { fetchImpl })) return "up";
  const script = startScript(env);
  if (!script || platform !== "win32") { logHook(env, "ensure-server", "local server is down; no start script for this platform/install"); return "no-script"; }
  const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script];
  if (u.port) args.push("-Port", u.port);
  const child = spawnImpl("powershell.exe", args, { detached: true, stdio: "ignore", windowsHide: true });
  child.unref?.();
  logHook(env, "ensure-server", `local server was down; started ${path.basename(script)}`);
  return "started";
}
