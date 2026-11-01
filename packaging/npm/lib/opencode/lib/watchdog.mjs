// A guard for the cold-start hang (docs/handoff_opencode_agent_and_app_2026-11.md, part 1 step 5): the first run after a
// config change sometimes shows nothing for minutes. When no progress event (a prompt, the model call, a finished tool)
// arrives for 60 s, this says so once in the plugin's debug log. It only logs; it never kills anything.
import path from "node:path"

// OpenCode's own log directory — what "no progress" points the user at. XDG_DATA_HOME/opencode/log when the crystal ball
// says so, otherwise OpenCode's platform fallbacks.
export const opencodeLogDir = (env = process.env, platform = process.platform) => {
  if (env.XDG_DATA_HOME) return path.join(env.XDG_DATA_HOME, "opencode", "log")
  if (platform === "win32" && env.LOCALAPPDATA) return path.join(env.LOCALAPPDATA, "opencode", "log")
  return path.join(env.HOME || "", ".local", "share", "opencode", "log")
}

// One timer per session, keyed by session id; every arm restarts that one timer from now.
export function createWatchdog({ log = () => {}, armMs = 60000 } = {}) {
  const timers = new Map()
  const clear = (id) => {
    const t = timers.get(id)
    if (t) { clearTimeout(t); timers.delete(id) }
  }
  const arm = (id) => {
    clear(id)
    const t = setTimeout(() => {
      timers.delete(id)                       // say it once, not at every further 60 s
      log(`no progress for ${Math.round(armMs / 1000)} s in session ${id}; see OpenCode's logs at ${opencodeLogDir()}`)
    }, armMs)
    if (t && typeof t.unref === "function") t.unref()   // otherwise `opencode run` waits 60 s before exiting
    timers.set(id, t)
  }
  return { arm, clear }
}
