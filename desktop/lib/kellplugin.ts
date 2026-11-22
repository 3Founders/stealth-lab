// kell inside the desktop engine, the way Claude Code gets it (owner, 2026-11-08: "use .kell exactly as intended on the Claude Code
// MCP thing", and nothing for a new user to run after installing). The installer carries kell's OpenCode plugin (vendor/kell-opencode,
// built by scripts/bundle-cli.mjs), and every engine start writes the two files it reads:
//
//   ~/.stealthlab/desktop/hooks.json   kell's hooks in Claude Code's settings format: `kell hook-prompt` (find_ways with this repo's
//                                      .kell/ claims and library, the survey that builds .kell/ on a repo's first prompt, the tool
//                                      note), `hook capture-tool` and `hook capture-stop` (the outcome per prompt, the library entry)
//   ~/.stealthlab/desktop/plugin.json  the plugin's settings: session mode (no dispatch: the session's model already is the cheap one),
//                                      the cheap, strong and fallback models from MODEL_LADDER, and the hooks file
//
// Each hook runs the CLI the installer ships (lib/cli.ts), so nothing depends on a global kell install.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { kellCommand } from "./cli"

const home = (env: NodeJS.ProcessEnv) => env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")

/** The bundled plugin's folder (or a developer's `kell setup --client opencode` copy), forward slashes; "" when there is none. */
export function pluginDir(env: NodeJS.ProcessEnv = process.env): string {
  const cwd = env.KELL_ROOT || process.cwd()
  const candidates = [
    env.KELL_OC_PLUGIN || "",
    path.join(cwd.replace(/app\.asar$/, "app.asar.unpacked"), "vendor", "kell-opencode"),
    path.join(cwd, "vendor", "kell-opencode"),
    path.join(home(env), "opencode", "plugin"),
  ].filter(Boolean)
  const found = candidates.find((d) => fs.existsSync(path.join(d, "index.mjs")))
  return found ? found.replace(/\\/g, "/") : ""
}

const quote = (s: string) => (/[\s"&|<>^]/.test(s) ? `"${s.replace(/"/g, '\\"')}"` : s)
const command = (args: string[]) => { const c = kellCommand(args); return [c.cmd, ...c.args].map(quote).join(" ") }

/** The hooks file: what Claude Code's install writes (packaging/npm/lib/clients.mjs, capture_hook.mjs CAPTURE_HOOKS), minus the
 *  local-server start, which the hosted server does not need. */
export function hooksDoc(): any {
  return {
    hooks: {
      UserPromptSubmit: [{ hooks: [{ type: "command", command: command(["hook-prompt"]), timeout: 120 }] }],
      PostToolUse: [{ matcher: "Bash|PowerShell|mcp__.*|WebSearch|WebFetch",
                      hooks: [{ type: "command", command: command(["hook", "capture-tool"]), timeout: 30 }] }],
      Stop: [{ hooks: [{ type: "command", command: command(["hook", "capture-stop"]), timeout: 60 }] }],
    },
  }
}

/** The models the plugin runs, from the gateway's list in MODEL_LADDER order: cheap is the ladder's head; strong (a task the triage cannot
 *  read, or two failed test runs) is the ladder's SECOND rung, not the most expensive one — an escalation should cost a little more, not
 *  several times more (owner, 2026-11-19: the main agent was landing on DeepSeek V4 Pro whenever the triage could not read a prompt).
 *  fallback (a provider that does not answer) is the third, so a dead provider moves to a model that has not been tried yet.
 *  The Settings pane shows the same (app/api/settings); a person who wants V4 Pro picks it in the composer's model menu. */
export function modelPlan(models: string[]): { cheap: string; strong: string; fallback: string } {
  const cheap = models[0]
  const strong = models[1] || cheap
  const fallback = models[2] || cheap
  return { cheap, strong, fallback }
}

/** Writes the hooks file and the plugin's settings (only when they changed); -> the environment the engine needs to find them ({} with
 *  no plugin). */
export function preparePlugin(models: string[], env: NodeJS.ProcessEnv = process.env): Record<string, string> {
  if (!pluginDir(env) || !models.length) return {}
  const dir = path.join(home(env), "desktop")
  fs.mkdirSync(dir, { recursive: true })
  const hooksFile = path.join(dir, "hooks.json")
  const settingsFile = path.join(dir, "plugin.json")
  const write = (file: string, obj: unknown) => {
    const text = JSON.stringify(obj, null, 2) + "\n"
    try { if (fs.readFileSync(file, "utf8") === text) return } catch { /* new */ }
    fs.writeFileSync(`${file}.tmp`, text)
    fs.renameSync(`${file}.tmp`, file)
  }
  write(hooksFile, hooksDoc())
  const plan = modelPlan(models)
  // `fallbacks` is the whole ladder the plugin walks when a model is denied or a provider fails; `fallback` stays for an
  // older plugin that reads only one rung.
  write(settingsFile, { mode: "session", cheap: `kell/${plan.cheap}`, strong: `kell/${plan.strong}`, fallback: `kell/${plan.fallback}`,
                        fallbacks: models.map((m) => `kell/${m}`), settings: [hooksFile], pins: pinsFile(env) })
  return { KELL_OC_CONFIG: settingsFile, KELL_OC_SETTINGS: hooksFile }
}

/** The models people chose in the composer, per session: `{ [sessionID]: "provider/model" }`. The plugin reads it on every prompt
 *  and runs a pinned session on that model instead of re-picking by triage (2026-11-18: the picker was overridden every turn). */
export const pinsFile = (env: NodeJS.ProcessEnv = process.env) => path.join(home(env), "desktop", "model-pins.json")

/** Pins a session to the model the person chose, or (ref null) lets kell pick again. Never throws: a turn still runs without it. */
export function pinModel(sessionID: string, ref: string | null, env: NodeJS.ProcessEnv = process.env): void {
  if (!sessionID) return
  try {
    const file = pinsFile(env)
    let pins: Record<string, string> = {}
    try { pins = JSON.parse(fs.readFileSync(file, "utf8")) || {} } catch { /* none yet */ }
    if ((pins[sessionID] ?? null) === ref) return
    if (ref) pins[sessionID] = ref; else delete pins[sessionID]
    const keys = Object.keys(pins)
    for (const k of keys.slice(0, Math.max(0, keys.length - 500))) delete pins[k]      // the oldest go first; a few hundred chats is plenty
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(`${file}.tmp`, JSON.stringify(pins, null, 2) + "\n")
    fs.renameSync(`${file}.tmp`, file)
  } catch { /* the turn runs on the model it was sent with */ }
}
