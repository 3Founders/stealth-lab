// How hard a model thinks before it answers (owner, 2026-11-24: people should be able to change the thinking level).
//
// Providers disagree on the dial, so the app keeps one level per model and adds it to each model call at the gateway
// (app/api/gateway/[...path]/route.ts). That keeps the engine untouched and applies the choice to every rung a turn
// runs, including the ones kell's router picks.
//
//   auto  the provider's own default (the field is not sent at all)
//   off   reasoning_effort "none" + thinking disabled — Z.ai's GLM-5.3 refuses this and forces thinking on, so the
//         gateway drops it for GLM instead of failing the call
//   low / high / max   the levels Z.ai and DeepSeek both document (DeepSeek maps low->low, high->high, max->max)
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export type ThinkingLevel = "auto" | "off" | "low" | "high" | "max"
export const THINKING_LEVELS: ThinkingLevel[] = ["auto", "off", "low", "high", "max"]

const home = (env: NodeJS.ProcessEnv) => env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
export const thinkingFile = (env: NodeJS.ProcessEnv = process.env) => path.join(home(env), "desktop", "thinking.json")

/** `{ default?: level, [modelId]: level }`; "auto" is stored as absence, so a provider default stays a default. */
export function readThinking(env: NodeJS.ProcessEnv = process.env): Record<string, ThinkingLevel> {
  try {
    const raw = JSON.parse(fs.readFileSync(thinkingFile(env), "utf8"))
    const out: Record<string, ThinkingLevel> = {}
    for (const [key, value] of Object.entries(raw || {})) {
      if (value !== "auto" && THINKING_LEVELS.includes(value as ThinkingLevel)) out[key] = value as ThinkingLevel
    }
    return out
  } catch { return {} }
}

export function writeThinking(level: ThinkingLevel, model?: string, env: NodeJS.ProcessEnv = process.env): Record<string, ThinkingLevel> {
  const map = readThinking(env)
  const key = model || "default"
  if (level === "auto") delete map[key]
  else map[key] = level
  const file = thinkingFile(env)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(map, null, 2) + "\n")
  fs.renameSync(`${file}.tmp`, file)
  return map
}

/** The fields one model call gets for a level. Empty when the level is auto or the model cannot take it. */
export function effortBody(model: string, level: ThinkingLevel | undefined): Record<string, unknown> {
  if (!level || level === "auto") return {}
  const id = String(model || "").toLowerCase()
  // GLM-5.3 forces thinking on and errors if it is disabled (Z.ai's reference), so "off" means "leave it alone".
  if (level === "off") return /glm/.test(id) ? {} : { reasoning_effort: "none", thinking: { type: "disabled" } }
  return { reasoning_effort: level }
}
