// kell's coding engine runs on its OWN folders, never OpenCode's (owner, 2026-11-18: "we need to be completely independent
// of it").
//
// The engine is a bundled copy of OpenCode. Left alone, it uses OpenCode's default folders: the history database
// (~/.local/share/opencode/opencode.db), saved provider logins, undo snapshots, the global config (~/.config/opencode, with
// its plugins, MCP servers, skills and permission rules) and the cache. A person who also uses OpenCode then shared one
// database with kell (a different OpenCode version could upgrade its format and break the other) and had their own
// plugins and rules loaded into kell. Now:
//   data, state, cache  ~/.stealthlab/engine/{data,state,cache}   through XDG_DATA_HOME / XDG_STATE_HOME / XDG_CACHE_HOME
//                       (the only switches this OpenCode honours for them; `opencode debug paths` confirms). Ordinary tools
//                       barely read these three, so commands the agent runs are unaffected.
//   config              ~/.stealthlab/engine/config      through OPENCODE_CONFIG_DIR, NOT XDG_CONFIG_HOME: that one would
//                       also move gh's, git's and other tools' config for every command the agent runs.
//   OPENCODE_* from the person's environment are dropped, so their own OpenCode settings never reach kell's engine.
// The first start on these folders copies the old shared history in once (a consistent copy, VACUUM INTO, and the undo
// snapshots), so kell's earlier sessions and Undo keep working. Nothing of OpenCode's is changed or removed.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { Worker } from "node:worker_threads"

const kellHome = (env: NodeJS.ProcessEnv = process.env) => env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
export const engineHome = (env: NodeJS.ProcessEnv = process.env) => env.KELL_ENGINE_HOME || path.join(kellHome(env), "engine")

/** Where the engine keeps its database, snapshots and logs (what OpenCode calls its data directory). */
export const engineDataDir = (env: NodeJS.ProcessEnv = process.env) => path.join(engineHome(env), "data", "opencode")
export const engineDb = (env: NodeJS.ProcessEnv = process.env) => path.join(engineDataDir(env), "opencode.db")
export const engineConfigDir = (env: NodeJS.ProcessEnv = process.env) => path.join(engineHome(env), "config")

/** The person's OWN OpenCode history (for "Import OpenCode chats" only): their XDG_DATA_HOME, else OpenCode's default. */
export const theirOpenCodeDb = (env: NodeJS.ProcessEnv = process.env) =>
  path.join(env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "opencode", "opencode.db")

/** The engine's environment: the person's, minus their OpenCode settings, plus kell's own folders. */
export function engineEnv(base: NodeJS.ProcessEnv, env: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const out = {} as NodeJS.ProcessEnv
  for (const [k, v] of Object.entries(base)) if (!/^OPENCODE_/i.test(k)) out[k] = v
  const home = engineHome(env)
  for (const d of ["data", "state", "cache", "config"]) fs.mkdirSync(path.join(home, d), { recursive: true })
  out.XDG_DATA_HOME = path.join(home, "data")
  out.XDG_STATE_HOME = path.join(home, "state")
  out.XDG_CACHE_HOME = path.join(home, "cache")
  out.OPENCODE_CONFIG_DIR = engineConfigDir(env)
  return out
}

const MOVED = ".moved-from-opencode"

/** Once: copy the history the engine kept in OpenCode's folders (before kell had its own) into kell's, so earlier sessions
 *  and their Undo stay. A consistent copy of the live database (VACUUM INTO, ~40 s for a long-used one) and the undo
 *  snapshots, on a worker thread so the app's server keeps answering meanwhile. Concurrent callers share one copy. Never
 *  rejects: a failed copy starts the engine on an empty history instead of not at all. */
let moving: Promise<{ moved: boolean; note?: string }> | null = null
export function moveHistoryOnce(env: NodeJS.ProcessEnv = process.env): Promise<{ moved: boolean; note?: string }> {
  const dest = engineDataDir(env)
  const marker = path.join(dest, MOVED)
  if (fs.existsSync(marker) || fs.existsSync(engineDb(env))) return Promise.resolve({ moved: false })
  if (moving) return moving
  const fromDir = path.dirname(theirOpenCodeDb(env))      // where the engine kept its history before (OpenCode's own)
  fs.mkdirSync(dest, { recursive: true })
  const done = (note: string) => { try { fs.writeFileSync(marker, `${new Date().toISOString()} ${note}
`) } catch { /* best effort */ } }
  if (!fs.existsSync(path.join(fromDir, "opencode.db"))) { done("nothing to move"); return Promise.resolve({ moved: false }) }
  // The worker's code is inline (no separate file for the bundler to lose). It gets only the two folders.
  const code = `
    const { workerData, parentPort } = require("node:worker_threads")
    const fs = require("node:fs"), path = require("node:path")
    const { DatabaseSync } = require("node:sqlite")
    const { fromDir, dest } = workerData
    const tmp = path.join(dest, "opencode.db.moving")
    ;(() => {
    try {
      fs.rmSync(tmp, { force: true })
      const db = new DatabaseSync(path.join(fromDir, "opencode.db"), { readOnly: true, timeout: 15000 })
      try { db.exec("VACUUM INTO '" + tmp.replace(/'/g, "''") + "'") } finally { db.close() }
      fs.renameSync(tmp, path.join(dest, "opencode.db"))
    } catch (e) { try { fs.rmSync(tmp, { force: true }) } catch {} ; parentPort.postMessage({ moved: false, note: String(e && e.message || e) }); return }
    try {
      const snap = path.join(fromDir, "snapshot")
      if (fs.existsSync(snap)) fs.cpSync(snap, path.join(dest, "snapshot"), { recursive: true, force: false })
    } catch {}
    parentPort.postMessage({ moved: true })
    })()`
  moving = new Promise((resolve) => {
    let w: Worker
    try { w = new Worker(code, { eval: true, workerData: { fromDir, dest } }) }
    catch (e: any) { done(`history not moved: ${String(e?.message || e).slice(0, 200)}`); resolve({ moved: false, note: String(e?.message || e) }); return }
    let settled = false
    const finish = (r: { moved: boolean; note?: string }) => {
      if (settled) return
      settled = true
      done(r.moved ? "moved" : `history not moved: ${String(r.note || "").slice(0, 200)}`)
      resolve(r)
    }
    w.once("message", (m: any) => finish({ moved: !!m?.moved, note: m?.note }))
    w.once("error", (e: Error) => finish({ moved: false, note: String(e?.message || e) }))
    w.once("exit", (c) => finish({ moved: false, note: `the copy stopped (exit ${c})` }))
  }).finally(() => { moving = null }) as Promise<{ moved: boolean; note?: string }>
  return moving
}

/** Own-key setups (`kell setup --client opencode` wrote kell's provider, plugin and MCP entry into OpenCode's global config):
 *  just kell's own entries, as inline config for the engine. Nothing else of the person's OpenCode config is carried over.
 *  Null when there is nothing of kell's there. */
export function kellEntriesFromOpenCode(env: NodeJS.ProcessEnv = process.env): string | null {
  const home = os.homedir()
  const dir = env.OPENCODE_CONFIG_DIR || path.join(env.XDG_CONFIG_HOME || path.join(home, ".config"), "opencode")
  let cfg: any = null
  for (const f of ["opencode.json", "opencode.jsonc"]) {
    try {
      const raw = fs.readFileSync(path.join(dir, f), "utf8")
      cfg = JSON.parse(raw.replace(/^\s*\/\/.*$/gm, "").replace(/,(\s*[}\]])/g, "$1"))
      break
    } catch { /* next */ }
  }
  if (!cfg || typeof cfg !== "object") return null
  const out: any = {}
  const prov: any = {}
  for (const k of ["kell", "gc", "di"]) if (cfg.provider?.[k]) prov[k] = cfg.provider[k]
  if (Object.keys(prov).length) out.provider = prov
  const plugins = (Array.isArray(cfg.plugin) ? cfg.plugin : []).filter((p: unknown) => typeof p === "string" && /stealthlab|kell/i.test(p))
  if (plugins.length) out.plugin = plugins
  if (cfg.mcp?.servers?.kell) out.mcp = { servers: { kell: cfg.mcp.servers.kell } }
  if (typeof cfg.model === "string" && /^(kell|gc|di)\//.test(cfg.model)) out.model = cfg.model
  if (cfg.experimental?.image_generate !== undefined) out.experimental = { image_generate: cfg.experimental.image_generate }
  return Object.keys(out).length ? JSON.stringify(out) : null
}
