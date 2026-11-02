// `kell setup --client opencode`: kell's own coding agent on OpenCode (docs/opencode_agent.md).
//
// What it writes, and nothing else:
//   ~/.stealthlab/opencode/plugin/      kell's OpenCode plugin (copied from lib/opencode/) with @opencode/plugin pinned, installed
//                                       with npm there -- kell-mcp itself keeps no dependencies
//   ~/.stealthlab/opencode/hooks.json   kell's hooks in Claude Code's settings format, which the plugin runs (and only these:
//                                       a user's Claude Code hooks are not run twice)
//   ~/.stealthlab/opencode/config.json  the plugin's settings: mode (session), cheap and strong model, the hooks file
//   OpenCode's global opencode.json     a "gc" provider (General Compute, key from the environment), the plugin, kell's MCP
//                                       server, and the default model -- the keys kell added are listed in config.json's
//                                       owned, so uninstall removes exactly those (OpenCode's own file gets no extra key)
// The provider and plugin keys are the ones the OpenCode 2.2.5 spike ran with; mcp.servers is the 2.2.5 schema.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { configDir } from "./config.mjs";

export const OPENCODE_VERSION = "2.2.5";
export const CHEAP_MODEL = "gc/minimax-m2.7";
export const STRONG_MODEL = "gc/deepseek-v3.2";
const PROVIDER = "gc";
const MCP_NAME = "kell";
const HERE = path.dirname(fileURLToPath(import.meta.url));

// General Compute's models (list prices per million tokens; OpenCode computes each message's cost from them)
const MODELS = {
  "minimax-m2.7": { name: "minimax-m2.7", cost: { input: 0.21, output: 0.84 } },
  // General Compute refuses deepseek-v3.2 requests asking for 8k+ output tokens (OpenCode asks 32k by default); 4k passes
  "deepseek-v3.2": { name: "deepseek-v3.2", cost: { input: 0.28, output: 0.42 }, limit: { context: 128000, output: 4000 } },
  "gemma-4-31B-it": { name: "gemma-4-31B-it", cost: { input: 0.09, output: 0.34 } },
};

// A second provider for the strong rung (setup option --strong-key-env, default key variable DEEPINFRA_KEY). DeepInfra's
// OpenAI-compatible endpoint; its DeepSeek-V3.2 is the same model General Compute serves. Output limit checked on
// 2026-11-02 (api.deepinfra.com/models/deepseek-ai/DeepSeek-V3.2): max_output_tokens 163840, so OpenCode's 32k default
// output request passes and no limit cap is needed (the cap is General Compute's quirk). Prices that day: $0.26/M in, $0.38/M out.
export const DI_PROVIDER = "di";
export const DI_MODEL = "deepseek-ai/DeepSeek-V3.2";
export const DI_BASE_URL = "https://api.deepinfra.com/v1/openai";
const DI_MODELS = { [DI_MODEL]: { name: "DeepSeek V3.2", cost: { input: 0.26, output: 0.38 } } };

/**
 * The strong model and the provider-failure fallback (the plugin's retry hook reads config.json's fallback): with a second
 * provider's key the strong rung moves there and gc/deepseek-v3.2 becomes its fallback; otherwise today's setup -- the strong
 * model on General Compute, the cheap model as the fallback.
 */
export function modelPlan({ strongKeyEnv } = {}) {
  return strongKeyEnv
    ? { strong: `${DI_PROVIDER}/${DI_MODEL}`, fallback: STRONG_MODEL }
    : { strong: STRONG_MODEL, fallback: CHEAP_MODEL };
}

export function opencodeDir(env = process.env) {
  return path.join(configDir(env), "opencode");
}

// OpenCode's global config directory: OPENCODE_CONFIG_DIR, else $XDG_CONFIG_HOME/opencode, else ~/.config/opencode
export function opencodeConfigFile(env = process.env) {
  const home = env.STEALTHLAB_TEST_HOME || os.homedir();
  const dir = env.OPENCODE_CONFIG_DIR || path.join(env.XDG_CONFIG_HOME || path.join(home, ".config"), "opencode");
  return path.join(dir, "opencode.json");
}

const readJson = (file) => { try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; } };
const writeJson = (file, obj) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(obj, null, 2) + "\n"); };

/** Adds kell's keys to an OpenCode config object (pure; for tests). -> { config, owned } */
export function withKell(cfg, { pluginDir, url, token, keyEnv = "GENERAL_COMPUTE_API_KEY", strongKeyEnv, owned: before = [] }) {
  const c = { ...(cfg || {}) };
  const owned = new Set(before);
  if (!c.$schema) { c.$schema = "https://opencode.ai/v2/config.json"; owned.add("schema"); }
  c.provider = { ...(c.provider || {}) };
  if (!c.provider[PROVIDER]) {
    c.provider[PROVIDER] = { npm: "@ai-sdk/openai-compatible", name: "General Compute",
                             options: { baseURL: "https://api.generalcompute.com/v1", apiKey: `{env:${keyEnv}}` }, models: MODELS };
    owned.add("provider.gc");
  }
  // The second provider for the strong rung is added when setup was given its key variable and the variable is set;
  // uninstall removes it by the same "provider.di" owned flag.
  if (strongKeyEnv) {
    c.provider[DI_PROVIDER] = { npm: "@ai-sdk/openai-compatible", name: "DeepInfra",
                                options: { baseURL: DI_BASE_URL, apiKey: `{env:${strongKeyEnv}}` }, models: DI_MODELS };
    owned.add("provider.di");
  }
  const plug = pluginDir.replace(/\\/g, "/");
  c.plugin = [...(Array.isArray(c.plugin) ? c.plugin : []).filter((p) => p !== plug), plug];
  owned.add("plugin");
  c.mcp = { ...(c.mcp || {}) };
  c.mcp.servers = { ...(c.mcp.servers || {}),
    [MCP_NAME]: { type: "remote", url, codemode: false, ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}) } };
  owned.add("mcp.kell");
  if (!c.model) { c.model = STRONG_MODEL; owned.add("model"); }
  return { config: c, owned: [...owned] };
}

/** Removes exactly what withKell added (pure; for tests). */
export function withoutKell(cfg, { pluginDir, owned: list = [] }) {
  if (!cfg) return cfg;
  const c = { ...cfg };
  const owned = new Set(list);
  const plug = pluginDir.replace(/\\/g, "/");
  if (Array.isArray(c.plugin)) { c.plugin = c.plugin.filter((p) => p !== plug); if (!c.plugin.length) delete c.plugin; }
  if (c.mcp?.servers?.[MCP_NAME]) {
    const servers = { ...c.mcp.servers }; delete servers[MCP_NAME];
    c.mcp = { ...c.mcp, servers }; if (!Object.keys(servers).length) delete c.mcp.servers;
    if (!Object.keys(c.mcp).length) delete c.mcp;
  }
  if (owned.has("provider.gc") && c.provider) { const p = { ...c.provider }; delete p[PROVIDER]; c.provider = p; if (!Object.keys(p).length) delete c.provider; }
  if (owned.has("provider.di") && c.provider) { const p = { ...c.provider }; delete p[DI_PROVIDER]; c.provider = p; if (!Object.keys(p).length) delete c.provider; }
  if (owned.has("model") && c.model === STRONG_MODEL) delete c.model;
  if (owned.has("schema")) delete c.$schema;
  return c;
}

function copyDir(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const e of fs.readdirSync(from, { withFileTypes: true })) {
    const a = path.join(from, e.name), b = path.join(to, e.name);
    if (e.isDirectory()) copyDir(a, b); else fs.copyFileSync(a, b);
  }
}

export function opencodeVersion() {
  const r = spawnSync("opencode", ["--version"], { encoding: "utf8", shell: process.platform === "win32", timeout: 20000 });
  return r.status === 0 ? String(r.stdout || "").trim().split(/\s+/).pop() : null;
}

/** The whole install. launch: how hooks start kell (clients.mjs launchSpec). strongKeyEnv: the environment variable that
 * holds a second provider's key (default DEEPINFRA_KEY); when it is set, the strong rung moves to DeepInfra's DeepSeek
 * (modelPlan) and gc/deepseek-v3.2 becomes its fallback. -> a one-line description. */
export async function installOpenCode({ url, token, launch, env = process.env, npm = "npm", installDeps = true, strongKeyEnv }) {
  const dir = opencodeDir(env);
  const pluginDir = path.join(dir, "plugin");
  copyDir(path.join(HERE, "opencode"), pluginDir);
  writeJson(path.join(pluginDir, "package.json"), { name: "kell-opencode-plugin", private: true, type: "module", main: "index.mjs",
                                                    dependencies: { "@opencode/plugin": OPENCODE_VERSION } });
  if (installDeps) {
    const r = spawnSync(npm, ["install", "--no-audit", "--no-fund", "--omit=dev"], { cwd: pluginDir, encoding: "utf8",
                                                                                     shell: process.platform === "win32", timeout: 300000 });
    if (r.status !== 0) throw new Error(`npm install in ${pluginDir} failed: ${String(r.stderr || r.stdout).slice(-300)}`);
  }
  const hooksFile = path.join(dir, "hooks.json");
  const { installClaudeDispatch } = await import("./clients.mjs");
  installClaudeDispatch({ settingsFile: hooksFile, launch, env });
  const strongEnv = strongKeyEnv || "DEEPINFRA_KEY";
  const useDi = Boolean(env[strongEnv]);
  const file = opencodeConfigFile(env);
  const before = readJson(path.join(dir, "config.json"))?.owned || [];
  const { config, owned } = withKell(readJson(file), { pluginDir, url, token, strongKeyEnv: useDi ? strongEnv : undefined, owned: before });
  writeJson(file, config);
  const plan = modelPlan({ strongKeyEnv: useDi ? strongEnv : undefined });
  writeJson(path.join(dir, "config.json"), { mode: "session", cheap: CHEAP_MODEL, strong: plan.strong, fallback: plan.fallback, settings: [hooksFile], owned });
  return `plugin + hooks in ${dir}; provider${useDi ? "s (General Compute + DeepInfra for the strong model)" : ""}, plugin and MCP server in ${file}`;
}

export function uninstallOpenCode({ env = process.env } = {}) {
  const dir = opencodeDir(env);
  const file = opencodeConfigFile(env);
  const cfg = readJson(file);
  const owned = readJson(path.join(dir, "config.json"))?.owned || [];
  let changed = false;
  if (cfg) {
    const next = withoutKell(cfg, { pluginDir: path.join(dir, "plugin"), owned });
    if (JSON.stringify(next) !== JSON.stringify(cfg)) { writeJson(file, next); changed = true; }   // a file kell never touched stays as it is
  }
  if (fs.existsSync(dir)) { fs.rmSync(dir, { recursive: true, force: true }); changed = true; }
  return changed;
}

/** Checklist items for `kell setup` (same shape as setup.mjs checklist). */
export function opencodeChecks({ env = process.env, version = opencodeVersion() } = {}) {
  const items = [];
  items.push({ name: "opencode", ok: Boolean(version), detail: version ? `version ${version}` : "not found",
               fix: version ? undefined : `npm install -g @opencode/cli@${OPENCODE_VERSION}` });
  const plugin = fs.existsSync(path.join(opencodeDir(env), "plugin", "node_modules", "@opencode", "plugin"));
  items.push({ name: "opencode plugin", ok: plugin, detail: plugin ? opencodeDir(env) : "not installed",
               fix: plugin ? undefined : "kell setup --client opencode" });
  return items;
}
