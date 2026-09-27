// Opt-in Claude Code integration for the local executor layer (`install --with-exec`).
//
// Writes, and on `uninstall` removes exactly:
//   <claude dir>/agents/stealth-executor.md   one NODE, isolation: worktree, minimal tools
//   <claude dir>/agents/stealth-delegator.md  carries the local `stealthlab-exec` MCP server INLINE
//   <claude dir>/settings.json                SubagentStart / SubagentStop -> `stealthlab-mcp hook ...`
// where <claude dir> is $CLAUDE_CONFIG_DIR, else ~/.claude (STEALTHLAB_TEST_HOME overrides ~ for tests).
//
// This is the package's second write surface in ~/.claude: `install` (without --with-exec) already
// writes the UserPromptSubmit knowledge hook into the same settings.json (lib/clients.mjs,
// upsertClaudeHook). Both follow the same discipline: a file that doesn't parse is left untouched, a
// .bak copy is written before every change, our entries are recognised by the command they run and
// nothing else in the file is touched, and removing ours restores the rest semantically unchanged.
//
// The executor MCP server is deliberately NOT registered with `claude mcp add`: the delegator carries
// it inline (frontmatter `mcpServers`), so the main conversation never loads those tool descriptions.
// That only works for a user-level agent file -- plugin agents ignore `mcpServers` and `hooks`.
//
// Verified against the official docs on 2026-09-27:
//   frontmatter keys (name, description, tools, disallowedTools, model, maxTurns, isolation,
//   mcpServers inline `type: stdio`):  https://code.claude.com/docs/en/sub-agents
//   settings.json hooks schema (EventName -> [{matcher?, hooks: [{type: "command", command, timeout}]}],
//   timeout in seconds), SubagentStart/SubagentStop matchers (agent type; omitted = all):
//                                      https://code.claude.com/docs/en/hooks
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MANAGED_MARK = "stealthlab-mcp:managed";
export const AGENT_NAMES = ["stealth-executor", "stealth-delegator"];
export const EXEC_HOOKS = {
  SubagentStart: "hook subagent-start",
  SubagentStop: "hook subagent-stop",
};
// The hook itself returns in milliseconds (subagent-stop hands the check to a detached worker, see
// lib/subagent_hook.mjs); this is Claude Code's own kill timer, in seconds, as a backstop.
export const HOOK_TIMEOUT_S = 15;

const TEMPLATES = path.join(path.dirname(fileURLToPath(import.meta.url)), "agents");

export function claudeDir(env = process.env) {
  return env.CLAUDE_CONFIG_DIR || path.join(env.STEALTHLAB_TEST_HOME || os.homedir(), ".claude");
}

export function execPaths(env = process.env) {
  const dir = claudeDir(env);
  return {
    settings: path.join(dir, "settings.json"),
    agents: Object.fromEntries(AGENT_NAMES.map((n) => [n, path.join(dir, "agents", `${n}.md`)])),
  };
}

// Same quoting as lib/clients.mjs so a Windows path with spaces survives the shell Claude Code uses.
export function shellJoin(spec) {
  return [spec.command, ...spec.args].map((a) => (/[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a)).join(" ");
}

// JSON strings are valid YAML double-quoted scalars (backslashes in Windows paths stay escaped).
export function renderAgent(name, launch) {
  const text = fs.readFileSync(path.join(TEMPLATES, `${name}.md`), "utf8");
  return text
    .replaceAll("{{MANAGED_MARK}}", MANAGED_MARK)
    .replaceAll("{{EXEC_COMMAND}}", JSON.stringify(String(launch.command)))
    .replaceAll("{{EXEC_ARGS}}", JSON.stringify([...launch.args.map(String), "exec"]));
}

function readJsonOrThrow(file) {
  if (!fs.existsSync(file)) return { doc: {}, existed: false };
  const text = fs.readFileSync(file, "utf8");
  if (!text.trim()) return { doc: {}, existed: true };
  let doc;
  try {
    doc = JSON.parse(text);
  } catch (err) {
    throw new Error(`${file} is not valid JSON (${err.message}); left untouched -- add the hooks by hand`);
  }
  if (typeof doc !== "object" || doc === null || Array.isArray(doc)) throw new Error(`${file}: expected a JSON object`);
  return { doc, existed: true };
}

function writePrivate(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const existed = fs.existsSync(file);
  fs.writeFileSync(file, text, existed ? undefined : { mode: 0o600 });
  if (!existed) try { fs.chmodSync(file, 0o600); } catch { /* best effort on Windows */ }
}

const isOursFor = (mark) => (group) => (group?.hooks || []).some((h) => {
  const cmd = String(h?.command || "").trimEnd();
  return cmd.includes("stealthlab-mcp") && cmd.endsWith(mark);
});

export function upsertExecHooks(file, launch) {
  const { doc, existed } = readJsonOrThrow(file);
  if (existed) fs.copyFileSync(file, `${file}.bak`);
  if (doc.hooks !== undefined && (typeof doc.hooks !== "object" || doc.hooks === null || Array.isArray(doc.hooks))) {
    throw new Error(`${file}: "hooks" is not an object; left untouched`);
  }
  doc.hooks = doc.hooks || {};
  for (const [event, mark] of Object.entries(EXEC_HOOKS)) {
    const groups = (doc.hooks[event] || []).filter((g) => !isOursFor(mark)(g));
    const command = shellJoin({ command: launch.command, args: [...launch.args, ...mark.split(" ")] });
    groups.push({ hooks: [{ type: "command", command, timeout: HOOK_TIMEOUT_S }] });
    doc.hooks[event] = groups;
  }
  writePrivate(file, JSON.stringify(doc, null, 2) + "\n");
}

export function removeExecHooks(file) {
  if (!fs.existsSync(file)) return false;
  const { doc } = readJsonOrThrow(file);
  if (!doc.hooks || typeof doc.hooks !== "object") return false;
  let changed = false;
  const next = { ...doc.hooks };
  for (const [event, mark] of Object.entries(EXEC_HOOKS)) {
    const before = next[event];
    if (!Array.isArray(before)) continue;
    const after = before.filter((g) => !isOursFor(mark)(g));
    if (after.length === before.length) continue;
    changed = true;
    if (after.length) next[event] = after;
    else delete next[event];
  }
  if (!changed) return false;
  fs.copyFileSync(file, `${file}.bak`);
  if (Object.keys(next).length) doc.hooks = next;
  else delete doc.hooks;
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
  return true;
}

const isManaged = (file) => {
  try { return fs.readFileSync(file, "utf8").includes(MANAGED_MARK); } catch { return false; }
};

// An agent file of the same name that we did not write is the user's: never overwrite or delete it.
export function writeAgents(env, launch) {
  const { agents } = execPaths(env);
  for (const file of Object.values(agents)) {
    if (fs.existsSync(file) && !isManaged(file)) {
      throw new Error(`${file} exists and was not written by stealthlab-mcp; left untouched -- rename it and retry`);
    }
  }
  for (const [name, file] of Object.entries(agents)) writePrivate(file, renderAgent(name, launch));
  return Object.values(agents);
}

export function removeAgents(env) {
  const removed = [];
  for (const file of Object.values(execPaths(env).agents)) {
    if (fs.existsSync(file) && isManaged(file)) {
      fs.rmSync(file);
      removed.push(file);
    }
  }
  return removed;
}

// launch = how Claude Code should start this package (lib/clients.mjs launchSpec()).
export function installExec({ env = process.env, launch }) {
  const p = execPaths(env);
  // Validate settings.json before writing anything, so a parse failure leaves no half-install.
  readJsonOrThrow(p.settings);
  const agents = writeAgents(env, launch);
  upsertExecHooks(p.settings, launch);
  return { agents, settings: p.settings };
}

export function uninstallExec({ env = process.env } = {}) {
  const p = execPaths(env);
  const agents = removeAgents(env);
  const hooks = removeExecHooks(p.settings);
  return { agents, hooks, changed: agents.length > 0 || hooks };
}

export function describeExec(env = process.env) {
  const p = execPaths(env);
  return [...Object.values(p.agents), `${p.settings} (SubagentStart, SubagentStop hooks)`];
}
