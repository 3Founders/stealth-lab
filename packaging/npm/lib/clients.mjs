// Registering the hosted kell MCP server with each supported agent,
// under the server name "stealthlab". JSON configs are edited in place: other servers
// and keys are preserved, a .bak copy is written first, and a file that
// doesn't parse is left untouched (we never clobber a config we can't read).
import { shellJoin, uninstallExec } from "./claude_exec.mjs";
import { writeConfig } from "./config.mjs";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { addCaptureHooks, removeCaptureHooks } from "./capture_hook.mjs";
import { addRouteHooks, removeRouteHooks } from "./model_guard.mjs";
import { uninstallCursorHooks, upsertCursorHooks } from "./cursor_hooks.mjs";
import { installClaudeMd, uninstallClaudeMd } from "./claude_md.mjs";
import { PACKAGE, isOwnCommand } from "./names.mjs";

export const SERVER_NAME = "stealthlab";

// How Claude Desktop should start the stdio relay. A global install (npm -g, or the
// install.sh / install.ps1 scripts) is pinned by absolute paths, which works
// even for GUI apps that don't inherit the shell's PATH. When we're running
// from the throwaway npx cache, we register `npx` itself instead, since that
// cache can be garbage-collected.
export function launchSpec({ scriptPath = process.argv[1], nodePath = process.execPath, platform = process.platform, pkgName = PACKAGE } = {}) {
  const real = safeRealpath(scriptPath);
  const fromNpx = /[\\/]_npx[\\/]/.test(real);
  if (fromNpx) {
    return platform === "win32"
      ? { command: "cmd", args: ["/c", "npx", "-y", `${pkgName}@latest`] }
      : { command: "npx", args: ["-y", `${pkgName}@latest`] };
  }
  return { command: nodePath, args: [real] };
}

function safeRealpath(p) {
  try { return fs.realpathSync(p); } catch { return p; }
}

function onPath(bin) {
  const r = spawnSync(process.platform === "win32" ? "where" : "which", [bin], { stdio: "ignore" });
  return r.status === 0;
}

function home(env) {
  return env.STEALTHLAB_TEST_HOME || os.homedir();
}

function claudeDesktopConfig(env, platform) {
  if (platform === "darwin") return path.join(home(env), "Library", "Application Support", "Claude", "claude_desktop_config.json");
  if (platform === "win32") return path.join(env.APPDATA || path.join(home(env), "AppData", "Roaming"), "Claude", "claude_desktop_config.json");
  return path.join(env.XDG_CONFIG_HOME || path.join(home(env), ".config"), "Claude", "claude_desktop_config.json");
}

// Cline is a VS Code extension, so its MCP config lives in VS Code's
// globalStorage, not in a dotfile under $HOME. Cline reads it only while VS Code
// is running, so `detect` below checks for the VS Code app data directory rather
// than assuming the file exists -- a user can have Cline installed and never have
// opened it, in which case globalStorage is not there yet and the directory (plus
// the file) is created on install.
export function clineSettingsPath(env = process.env, platform = process.platform) {
  const base =
    platform === "win32"
      ? env.APPDATA || path.join(home(env), "AppData", "Roaming")
      : platform === "darwin"
        ? path.join(home(env), "Library", "Application Support")
        : env.XDG_CONFIG_HOME || path.join(home(env), ".config");
  return path.join(base, "Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json");
}

function vscodeAppData(env = process.env, platform = process.platform) {
  const base =
    platform === "win32"
      ? env.APPDATA || path.join(home(env), "AppData", "Roaming")
      : platform === "darwin"
        ? path.join(home(env), "Library", "Application Support")
        : env.XDG_CONFIG_HOME || path.join(home(env), ".config");
  return path.join(base, "Code");
}

// --- JSON configs (mcpServers map) -----------------------------------------

export function upsertJsonServer(file, entry) {
  let doc = {};
  if (fs.existsSync(file)) {
    const text = fs.readFileSync(file, "utf8");
    if (text.trim()) {
      try {
        doc = JSON.parse(text);
      } catch (err) {
        throw new Error(`${file} is not valid JSON (${err.message}); left untouched -- add the server by hand`);
      }
    }
    fs.copyFileSync(file, `${file}.bak`);
  }
  if (typeof doc !== "object" || doc === null || Array.isArray(doc)) throw new Error(`${file}: expected a JSON object`);
  doc.mcpServers = { ...(doc.mcpServers || {}), [SERVER_NAME]: entry };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
}

export function removeJsonServer(file) {
  if (!fs.existsSync(file)) return false;
  const doc = JSON.parse(fs.readFileSync(file, "utf8") || "{}");
  if (!doc.mcpServers || !(SERVER_NAME in doc.mcpServers)) return false;
  fs.copyFileSync(file, `${file}.bak`);
  delete doc.mcpServers[SERVER_NAME];
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
  return true;
}

// --- Codex (~/.codex/config.toml) ------------------------------------------

const tomlStr = (s) => JSON.stringify(String(s)); // JSON string escaping is valid TOML basic-string escaping

function stripTomlTable(text, table) {
  // Removes [table] and its [table.*] subtables, up to the next other header.
  const lines = text.split(/\r?\n/);
  const out = [];
  let skipping = false;
  for (const line of lines) {
    const m = line.match(/^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$/);
    if (m) skipping = m[1] === table || m[1].startsWith(`${table}.`);
    if (!skipping) out.push(line);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd();
}

export function upsertCodexServer(file, { url, token }) {
  const table = `mcp_servers.${SERVER_NAME}`;
  let text = "";
  if (fs.existsSync(file)) {
    text = fs.readFileSync(file, "utf8");
    fs.copyFileSync(file, `${file}.bak`);
  }
  const lines = [`[${table}]`, `url = ${tomlStr(url)}`];
  if (token) lines.push(`http_headers = { Authorization = ${tomlStr(`Bearer ${token}`)} }`);
  const base = stripTomlTable(text, table);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, (base ? `${base}\n\n` : "") + lines.join("\n") + "\n");
}

export function removeCodexServer(file) {
  if (!fs.existsSync(file)) return false;
  const text = fs.readFileSync(file, "utf8");
  const next = stripTomlTable(text, `mcp_servers.${SERVER_NAME}`);
  if (next === text.trimEnd()) return false;
  fs.copyFileSync(file, `${file}.bak`);
  fs.writeFileSync(file, next ? next + "\n" : "");
  return true;
}

// --- Claude Code knowledge hook (~/.claude/settings.json) --------------------
//
// A UserPromptSubmit hook that runs `kell hook-prompt`: every task-like prompt gets one
// find_ways lookup and the knowledge is added to the agent's context (lib/hook.mjs). Our entry is
// recognised by HOOK_MARK in its command, so other hooks in the file are never touched.

export const HOOK_MARK = "hook-prompt";

export function claudeSettingsPath(env = process.env) {
  return path.join(env.CLAUDE_CONFIG_DIR || path.join(home(env), ".claude"), "settings.json");
}


function readJsonOrThrow(file) {
  if (!fs.existsSync(file)) return {};
  const text = fs.readFileSync(file, "utf8");
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(`${file} is not valid JSON (${err.message}); left untouched -- add the hook by hand`);
  }
}

function isOurs(group) {
  return (group?.hooks || []).some((h) => {
    const cmd = String(h.command || "");
    return isOwnCommand(cmd) && cmd.trimEnd().endsWith(HOOK_MARK);
  });
}

// The knowledge hook (UserPromptSubmit) and its capture hooks (PostToolUse on Bash, Stop; lib/capture_hook.mjs)
// are installed and removed together. commandSpec is the hook-prompt command; the capture commands are the
// same launcher with their own subcommand.
// 900 s: Claude Code's kill timer for the prompt hook, a ceiling -- the lookup itself gives up after its own timeout,
// but dispatch (lib/dispatch.mjs) runs the plan's cheap rungs to completion inside this hook.
export function upsertClaudeHook(file, commandSpec, timeoutSec = 900, { guard = true } = {}) {
  const doc = readJsonOrThrow(file);
  if (fs.existsSync(file)) fs.copyFileSync(file, `${file}.bak`);
  doc.hooks = doc.hooks || {};
  const groups = (doc.hooks.UserPromptSubmit || []).filter((g) => !isOurs(g));
  groups.push({ hooks: [{ type: "command", command: shellJoin(commandSpec), timeout: timeoutSec }] });
  doc.hooks.UserPromptSubmit = groups;
  const args = commandSpec.args || [];
  const launch = { command: commandSpec.command, args: args[args.length - 1] === HOOK_MARK ? args.slice(0, -1) : args };
  addCaptureHooks(doc, launch);
  if (guard) addRouteHooks(doc, launch);          // the model guard (lib/model_guard.mjs)
  else removeRouteHooks(doc);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
}

export function removeClaudeHook(file) {
  if (!fs.existsSync(file)) return false;
  const doc = readJsonOrThrow(file);
  const before = doc.hooks?.UserPromptSubmit || [];
  const after = before.filter((g) => !isOurs(g));
  const prompt = after.length !== before.length;
  if (prompt) {
    if (after.length) doc.hooks.UserPromptSubmit = after;
    else delete doc.hooks.UserPromptSubmit;
  }
  const capture = removeCaptureHooks(doc);
  const route = removeRouteHooks(doc);
  if (!prompt && !capture && !route) return false;
  fs.copyFileSync(file, `${file}.bak`);
  if (doc.hooks && !Object.keys(doc.hooks).length) delete doc.hooks;
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
  return true;
}

// Claude Code with dispatch (`install --with-exec`): hooks only. The prompt hook looks the task up and runs the
// cheap models itself (lib/dispatch.mjs); the capture hooks report outcomes and grow the library. Both call the
// kell server directly with the saved URL and token, so the session gets no MCP servers, no agents and no
// model guard: nothing it would call, and nothing that adds to every turn's context. `dispatch: true` in the saved
// config marks the local executor as installed (the models in exec.json become plan candidates).
// settingsFile: Claude Code's settings.json (user level for an install, a project's .claude/ in the A/B test).
// A CLAUDE.md block (lib/claude_md.mjs) tells the session what only it can do (record fixes, finish the survey worklist);
// STEALTHLAB_CLAUDE_MD=off skips it. A second install replaces the block, never repeats it.
export function installClaudeDispatch({ settingsFile, launch, env = process.env }) {
  // the prompt hook's kill timer must outlast dispatch's own limits (STEALTHLAB_PROMPT_HOOK_TIMEOUT_S; default 1200 s)
  const hookS = Number(env.STEALTHLAB_PROMPT_HOOK_TIMEOUT_S) > 0 ? Number(env.STEALTHLAB_PROMPT_HOOK_TIMEOUT_S) : 1200;
  upsertClaudeHook(settingsFile, { command: launch.command, args: [...launch.args, "hook-prompt"] }, hookS, { guard: false });
  uninstallExec({ env });                         // the delegation layer an older --with-exec install wrote
  writeConfig({ dispatch: true }, env);
  installClaudeMd(settingsFile, env);
  return { settings: settingsFile };
}

// --- CLI-registered clients -------------------------------------------------

function run(bin, args) {
  // .cmd shims on Windows need a shell; every arg we pass is quoted.
  const win = process.platform === "win32";
  const quoted = win ? args.map((a) => (/[\s"&|<>^]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a)) : args;
  const r = spawnSync(bin, quoted, { encoding: "utf8", shell: win });
  return { ok: r.status === 0, out: `${r.stdout || ""}${r.stderr || ""}`.trim() };
}

// --- The client table -------------------------------------------------------
//
// install(ctx) gets { url, token, stdio }. Clients that speak Streamable HTTP
// natively are pointed straight at the hosted URL -- no local process at all.
// Only Claude Desktop (whose config file accepts local commands only) gets
// the stdio relay. A token is optional: v1 reads are anonymous, and only
// report_discovery needs a signed-in user.

const bearer = (token) => (token ? { Authorization: `Bearer ${token}` } : undefined);
const withHeaders = (obj, token) => (token ? { ...obj, headers: bearer(token) } : obj);

export function clients({ env = process.env, platform = process.platform } = {}) {
  const h = home(env);
  const cursor = path.join(h, ".cursor", "mcp.json");
  const cursorHooks = path.join(h, ".cursor", "hooks.json");
  const windsurf = path.join(h, ".codeium", "windsurf", "mcp_config.json");
  const codex = path.join(env.CODEX_HOME || path.join(h, ".codex"), "config.toml");
  const cline = clineSettingsPath(env, platform);
  const desktop = claudeDesktopConfig(env, platform);

  return [
    {
      id: "claude-code",
      label: "Claude Code",
      detect: () => onPath("claude"),
      where: () => "claude mcp (user scope) + knowledge hook in " + claudeSettingsPath(env),
      // keepMcp (`install --with-exec --keep-mcp`): the hooks-only install AND the MCP server, so the session can also call
      // kell's tools (find_ways, call_model ...) itself. Without it the MCP registration is removed, as before.
      install: ({ url, token, hooks = true, hookCommand, dispatch = false, launch, keepMcp = false }) => {
        run("claude", ["mcp", "remove", "--scope", "user", SERVER_NAME]);
        if (dispatch && hooks) {                  // hooks only: see installClaudeDispatch
          installClaudeDispatch({ settingsFile: claudeSettingsPath(env), launch, env });
          if (!keepMcp) return "hooks only (prompt hook with dispatch + capture hooks) in " + claudeSettingsPath(env);
          const kept = ["mcp", "add", "--scope", "user", "--transport", "http", SERVER_NAME, url];
          if (token) kept.push("--header", `Authorization: Bearer ${token}`);
          const r = run("claude", kept);
          if (!r.ok) throw new Error(`claude mcp add failed: ${r.out}`);
          return "hooks (prompt hook with dispatch + capture hooks) AND the MCP server, in " + claudeSettingsPath(env);
        }
        const args = ["mcp", "add", "--scope", "user", "--transport", "http", SERVER_NAME, url];
        if (token) args.push("--header", `Authorization: Bearer ${token}`);
        const r = run("claude", args);
        if (!r.ok) throw new Error(`claude mcp add failed: ${r.out}`);
        if (hooks && hookCommand) upsertClaudeHook(claudeSettingsPath(env), hookCommand);
        else removeClaudeHook(claudeSettingsPath(env));
      },
      uninstall: () => {
        const hook = removeClaudeHook(claudeSettingsPath(env));
        const md = uninstallClaudeMd(claudeSettingsPath(env)) !== "absent";
        return run("claude", ["mcp", "remove", "--scope", "user", SERVER_NAME]).ok || hook || md;
      },
    },
    {
      id: "cursor",
      label: "Cursor",
      detect: () => fs.existsSync(path.dirname(cursor)),
      where: () => `${cursor} + hooks in ${cursorHooks}`,
      // The hooks (lib/cursor_hooks.mjs) are Cursor's version of the Claude Code knowledge + capture hooks;
      // --no-hooks registers the server only (and takes out hooks an earlier install added). --with-exec adds
      // dispatch: the prompt hook runs the cheap open models first (lib/cursor_dispatch.mjs), with a longer time limit.
      install: ({ url, token, hooks = true, launch, dispatch = false }) => {
        upsertJsonServer(cursor, withHeaders({ url }, token));
        if (hooks && launch) {
          upsertCursorHooks(cursorHooks, launch, platform, { dispatch });
          if (dispatch) {
            writeConfig({ dispatch: true }, env);   // the models in exec.json become plan candidates
            return `${cursor} + hooks with dispatch in ${cursorHooks}`;
          }
        } else uninstallCursorHooks(cursorHooks);
      },
      uninstall: () => {
        const hooksRemoved = uninstallCursorHooks(cursorHooks);
        return removeJsonServer(cursor) || hooksRemoved;
      },
    },
    {
      id: "vscode",
      label: "VS Code",
      detect: () => onPath("code"),
      where: () => "code --add-mcp (user profile)",
      install: ({ url, token }) => {
        const entry = withHeaders({ name: SERVER_NAME, type: "http", url }, token);
        const r = run("code", ["--add-mcp", JSON.stringify(entry)]);
        if (!r.ok) throw new Error(`code --add-mcp failed: ${r.out}`);
      },
      uninstall: () => {
        throw new Error('VS Code has no CLI removal -- run "MCP: List Servers" and remove "stealthlab"');
      },
    },
    {
      id: "windsurf",
      label: "Windsurf",
      detect: () => fs.existsSync(path.dirname(windsurf)),
      where: () => windsurf,
      install: ({ url, token }) => upsertJsonServer(windsurf, withHeaders({ serverUrl: url }, token)),
      uninstall: () => removeJsonServer(windsurf),
    },
    {
      id: "cline",
      label: "Cline",
      // Cline ships no CLI for MCP registration, and its config lives in VS Code
      // globalStorage, so this is a plain JSON upsert like Cursor's. `detect`
      // deliberately does NOT require the settings file to exist: a user can have
      // Cline installed and never have opened it, so globalStorage is created on
      // install (upsertJsonServer mkdirs). We also detect the Cline CLI, which
      // lib/executors/cline.mjs drives as a delegated harness.
      detect: () => fs.existsSync(vscodeAppData(env, platform)) || onPath("cline"),
      where: () => cline,
      install: ({ url, token }) => upsertJsonServer(cline, withHeaders({ url }, token)),
      uninstall: () => removeJsonServer(cline),
    },
    {
      id: "codex",
      label: "Codex CLI",
      detect: () => fs.existsSync(path.dirname(codex)) || onPath("codex"),
      where: () => codex,
      install: ({ url, token }) => upsertCodexServer(codex, { url, token }),
      uninstall: () => removeCodexServer(codex),
    },
    {
      id: "claude-desktop",
      label: "Claude Desktop",
      detect: () => fs.existsSync(path.dirname(desktop)),
      where: () => desktop,
      // Local-command config only, so this one client runs the relay. (The
      // alternative with nothing local: Settings -> Connectors -> Add custom
      // connector, paste the URL.)
      install: ({ stdio }) => upsertJsonServer(desktop, stdio),
      uninstall: () => removeJsonServer(desktop),
    },
  ];
}
