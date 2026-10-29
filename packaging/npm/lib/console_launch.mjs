// SessionStart hook, second half: when Claude Code starts and the kell MCP is connected (answering), make sure the console is
// running and open it in the browser, so the person sees the console without typing a command.
//
// How it fits: `kell hook ensure-server` first brings up a local MCP that is down (lib/ensure_server.mjs), then calls this.
//   * "Connected" means the configured MCP URL answers (a local one that was just started counts: the console shows it as closed
//     until it is up, then live). A down or unreachable MCP opens nothing.
//   * The console helper (`kell console`, lib/console.mjs) is started detached if it is not already running, and keeps running
//     after the session ends. It writes ~/.stealthlab/console.json (port, address with its token; private file) while it
//     runs, which is how a later session finds it instead of starting a second one.
//   * The browser opens only on a fresh start of Claude Code (hook source "startup"), not on resume, clear or compact, so a
//     long day does not fill the browser with tabs.
//   * Honest limit: the helper serves the console page from a built copy (console/out in the repo checkout, or the folder named
//     in STEALTHLAB_CONSOLE_UI). A plain npm install has neither, so there is nothing to open and the hook says so in its log
//     and does nothing. STEALTHLAB_CONSOLE_AUTOOPEN=off turns the whole thing off.
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { configDir } from "./config.mjs";
import { openBrowser } from "./oauth.mjs";
import { logHook } from "./subagent_hook.mjs";
import { answers } from "./ensure_server.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.join(HERE, "..", "bin", "kell.mjs");

export function launchEnabled(env = process.env) {
  return (env.STEALTHLAB_CONSOLE_AUTOOPEN || "on").toLowerCase() !== "off";
}

export function statePath(env = process.env) {
  return path.join(configDir(env), "console.json");
}

/** The built console page, or null. */
export function uiDir(env = process.env, exists = fs.existsSync) {
  // a named folder is the only one tried; otherwise the repo checkout's build
  const candidates = env.STEALTHLAB_CONSOLE_UI ? [env.STEALTHLAB_CONSOLE_UI] : [path.join(HERE, "..", "..", "..", "console", "out")];
  return candidates.find((d) => exists(path.join(d, "index.html"))) || null;
}

/** The running helper's { pid, port, url }, or null when none answers. */
export async function runningConsole({ env = process.env, fetchImpl = fetch } = {}) {
  let s;
  try { s = JSON.parse(fs.readFileSync(statePath(env), "utf8")); } catch { return null; }
  if (!s || !Number.isInteger(s.port) || typeof s.url !== "string") return null;
  try {
    const r = await fetchImpl(`http://127.0.0.1:${s.port}/health`, { signal: AbortSignal.timeout(1500) });
    if (!(r.ok ?? (r.status >= 200 && r.status < 300))) return null;
    let h = {};
    try { h = (await r.json?.()) || {}; } catch { /* an older helper: {ok} only */ }
    return { ...s, pageSeenS: typeof h.page_seen_s === "number" ? h.page_seen_s : null };
  } catch { return null; }
}

// a console page polled the helper within this long: it is open (a hidden tab polls about once a minute)
const PAGE_OPEN_S = 90;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Starts the helper detached and waits for it to publish its address. Returns the state, or null. */
export async function startConsole({ ui, env = process.env, fetchImpl = fetch, spawnImpl = spawn, waitMs = 6_000 } = {}) {
  const child = spawnImpl(process.execPath, [BIN, "console", "--ui-dir", ui], { detached: true, stdio: "ignore", windowsHide: true, env });
  child.on?.("error", () => {});
  child.unref?.();
  for (let t = 0; t < waitMs; t += 250) {
    const s = await runningConsole({ env, fetchImpl });
    if (s) return s;
    await sleep(250);
  }
  return null;
}

/**
 * `server` is what ensureServer returned ("up" | "started" | "remote" | "off" | "no-script"), `source` the SessionStart source.
 * Returns "disabled" | "not-connected" | "no-page" | "running" | "opened" | "failed".
 *   "running" = the console is up but this was not a fresh start, so no tab was opened.
 */
export async function ensureConsole({ url, server, source = "startup", env = process.env, fetchImpl = fetch, spawnImpl = spawn,
                                      open = openBrowser } = {}) {
  if (!launchEnabled(env)) return "disabled";
  const connected = server === "started" || ((server === "up" || server === "remote" || server === "off") && (await answers(url, { fetchImpl })));
  if (!connected) { logHook(env, "console", "the MCP is not answering: console not opened"); return "not-connected"; }
  let state = await runningConsole({ env, fetchImpl });
  if (!state) {
    const ui = uiDir(env);
    if (!ui) { logHook(env, "console", "no built console page here (console/out or STEALTHLAB_CONSOLE_UI): nothing to open"); return "no-page"; }
    state = await startConsole({ ui, env, fetchImpl, spawnImpl });
    if (!state) { logHook(env, "console", "the console helper did not start"); return "failed"; }
  }
  if (source !== "startup") return "running";
  // a page already showing this console: no second tab (each new session opened one, 2026-10-29)
  if (typeof state.pageSeenS === "number" && state.pageSeenS <= PAGE_OPEN_S) {
    logHook(env, "console", `the console is already open in a browser tab (port ${state.port}): not opened again`);
    return "already-open";
  }
  open(state.url);
  logHook(env, "console", `opened the console on port ${state.port}`);
  return "opened";
}

// ---- the small command: `kell console open | close | status`, and the /kell-console slash command that runs it ----------------
// Why a slash command and not an MCP tool: a tool's description sits in every turn's context; a command file costs nothing until
// it is used. The file is written next to the hooks by `install` (lib/clients.mjs) and runs `kell console <arg>`.

/** Opens the console on request (no "is the MCP connected" test: the person asked). Returns a one-line message. */
export async function openConsole({ env = process.env, fetchImpl = fetch, spawnImpl = spawn, open = openBrowser } = {}) {
  let state = await runningConsole({ env, fetchImpl });
  let started = false;
  if (!state) {
    const ui = uiDir(env);
    if (!ui) return "No built console page here. Build it once (npm run build in console/) or set STEALTHLAB_CONSOLE_UI to its folder.";
    state = await startConsole({ ui, env, fetchImpl, spawnImpl });
    if (!state) return "The console did not start. See ~/.stealthlab/hooks.log.";
    started = true;
  }
  open(state.url);
  return `Console ${started ? "started and opened" : "opened"}: ${state.url}`;
}

/** Stops the helper this machine's state file names. The MCP is a separate process and is left running. */
export async function closeConsole({ env = process.env, fetchImpl = fetch, kill = process.kill.bind(process) } = {}) {
  const state = await runningConsole({ env, fetchImpl });
  if (!state) { try { fs.rmSync(statePath(env), { force: true }); } catch { /* none */ } return "The console is not running."; }
  try { kill(state.pid); } catch { return `Could not stop the console (process ${state.pid}).`; }
  try { fs.rmSync(statePath(env), { force: true }); } catch { /* none */ }
  return "Console closed. The MCP is still running.";
}

export async function consoleStatus({ env = process.env, fetchImpl = fetch } = {}) {
  const state = await runningConsole({ env, fetchImpl });
  return state ? `Console running: ${state.url}` : "The console is not running. `/kell-console open` starts it.";
}

export async function consoleCtl(arg, deps = {}) {
  const a = String(arg || "status").trim().toLowerCase() || "status";
  if (a === "open") return openConsole(deps);
  if (a === "close") return closeConsole(deps);
  if (a === "status") return consoleStatus(deps);
  return "Usage: /kell-console open | close | status";
}

/** The Claude Code slash command file (markdown with a shell line). `launch` is { command, args } for this package's CLI. */
export function renderConsoleCommand(launch) {
  const quote = (a) => (/[\s"\\]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);
  const base = [launch.command, ...launch.args].map(quote).join(" ");
  return [
    "---",
    "description: Open, close or check the kell console (open | close | status)",
    "argument-hint: open | close | status",
    `allowed-tools: Bash(${base} console:*)`,
    "---",
    `!\`${base} console $ARGUMENTS status\``,
    "",
    "Reply with the line printed above, exactly, and nothing else.",
    "",
  ].join("\n");
}

export const COMMAND_FILE = "kell-console.md";

export function installConsoleCommand(claudeDir, launch) {
  const file = path.join(claudeDir, "commands", COMMAND_FILE);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, renderConsoleCommand(launch));
  return file;
}

export function removeConsoleCommand(claudeDir) {
  const file = path.join(claudeDir, "commands", COMMAND_FILE);
  try { fs.rmSync(file, { force: true }); return true; } catch { return false; }
}
