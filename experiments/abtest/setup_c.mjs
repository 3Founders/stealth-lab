// Arm C's StealthLab install, scoped to ONE workspace: the same hooks and agents `stealthlab-mcp install --with-exec`
// writes, but into <workspace>/.claude/ (project level) instead of the user's ~/.claude, so the person's own Claude
// Code is never changed. The runner starts `claude -p --setting-sources project`, so only these settings load.
//
//   node experiments/abtest/setup_c.mjs <workspace>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NPM = path.resolve(HERE, "..", "..", "packaging", "npm");
const lib = (p) => import(pathToFileURL(path.join(NPM, "lib", p)).href);

const ws = path.resolve(process.argv[2] || "");
if (!process.argv[2] || !fs.existsSync(ws)) {
  console.error("usage: node setup_c.mjs <existing workspace dir>");
  process.exit(2);
}
const { upsertClaudeHook } = await lib("clients.mjs");
const { installExec } = await lib("claude_exec.mjs");

const bin = path.join(NPM, "bin", "stealthlab-mcp.mjs");
const launch = { command: process.execPath, args: [bin] };
const claudeDir = path.join(ws, ".claude");
fs.mkdirSync(claudeDir, { recursive: true });
// the knowledge hook (UserPromptSubmit) + capture hooks + model guard, then the executor agents and their hooks
// 90 s: the first lookup after the test server starts loads the prior and warms the judge; the runner also warms it
// 1200 s: dispatch runs the plan's cheap rungs inside the prompt hook (lib/dispatch.mjs)
upsertClaudeHook(path.join(claudeDir, "settings.json"), { command: launch.command, args: [...launch.args, "hook-prompt"] }, 1200);
installExec({ env: { ...process.env, CLAUDE_CONFIG_DIR: claudeDir }, launch });
for (const f of fs.readdirSync(claudeDir)) if (f.endsWith(".bak")) fs.rmSync(path.join(claudeDir, f));
console.log(JSON.stringify({ workspace: ws, settings: path.join(claudeDir, "settings.json"),
                             agents: fs.readdirSync(path.join(claudeDir, "agents")) }));
