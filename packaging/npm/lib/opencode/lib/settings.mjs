// Reads the `hooks` of Claude Code settings files and picks the ones that apply to an event.
// Same files, same merge rule as Claude Code: arrays from every file are added together, none replaces another.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export function defaultSettingsFiles(cwd, env = process.env) {
  const claudeDir = env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude")
  return [
    path.join(claudeDir, "settings.json"),
    path.join(cwd, ".claude", "settings.json"),
    path.join(cwd, ".claude", "settings.local.json"),
  ]
}

/** KELL_OC_SETTINGS (a path list) overrides the default files, so a test or a clean profile can name its own. */
export function settingsFiles(cwd, env = process.env) {
  const list = String(env.KELL_OC_SETTINGS || "").split(path.delimiter).map((s) => s.trim()).filter(Boolean)
  return list.length ? list : defaultSettingsFiles(cwd, env)
}

/** KELL_OC_MIN_TIMEOUT_S raises a hook's own timeout to at least this many seconds (kell's answer path can take 20 s or more). */
export function loadHooks(files, env = process.env) {
  const floorMs = Math.max(0, Number(env.KELL_OC_MIN_TIMEOUT_S) || 0) * 1000
  const merged = {}
  for (const file of files) {
    let doc
    try { doc = JSON.parse(fs.readFileSync(file, "utf8")) } catch { continue }       // a missing or broken file adds nothing
    const hooks = doc && typeof doc === "object" ? doc.hooks : null
    if (!hooks || typeof hooks !== "object") continue
    for (const [event, groups] of Object.entries(hooks)) {
      if (!Array.isArray(groups)) continue
      for (const group of groups) {
        if (!group || !Array.isArray(group.hooks)) continue
        const commands = group.hooks
          .filter((h) => h && h.type === "command" && typeof h.command === "string" && h.command.trim())
          .map((h) => ({ command: h.command, timeoutMs: Math.max(floorMs, Number.isFinite(h.timeout) ? h.timeout * 1000 : 60_000) }))
        if (commands.length) (merged[event] ||= []).push({ matcher: typeof group.matcher === "string" ? group.matcher : "", commands })
      }
    }
  }
  return merged
}

function matches(matcher, subject) {
  if (!matcher || matcher === "*") return true
  try { return new RegExp(`^(?:${matcher})$`).test(subject) } catch { return matcher === subject }
}

/** Every command hook for `event` whose matcher selects `subject` (a tool name; "" for events without one). */
export function selectHooks(hooks, event, subject = "") {
  const out = []
  for (const group of hooks[event] || []) if (matches(group.matcher, subject)) out.push(...group.commands)
  return out
}
