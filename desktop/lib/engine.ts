// The engine: a local `opencode serve` child with kell's plugin on its config, found the way `kell setup --client opencode`
// installs it. The prototype talks to it through the tokened HTTP API (@opencode/client); the desktop shells (Tauri or
// Electron, docs/desktop_app_plan.md) will call this same module for their main process.
import { spawn, type ChildProcess } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { OpenCode } from "@opencode/client"

export type Engine = { baseUrl: string; password: string; directory: string }
export type Client = ReturnType<typeof OpenCode.make>

const exes = () => [
  process.env.KELL_OC_ENGINE || "",
  // `kell setup --client opencode` (isolated profile) and the global install, both Windows and unix layouts
  path.join(process.env.KELL_OC_PROFILE || path.join(process.env.HOME || process.env.USERPROFILE || "", ".stealthlab", "opencode"), "cli", "node_modules", "@opencode", "cli", "bin", "opencode.exe"),
  path.join(process.env.APPDATA || "", "npm", "node_modules", "@opencode", "cli", "bin", "opencode.exe"),
].filter(Boolean)

export const findEngineExe = () => exes().find((e) => { try { return fs.statSync(e).isFile() } catch { return false } }) || ""

// Next.js bundles each route with its own module instance, so the engine singleton lives on globalThis —
// that is what keeps one `opencode serve` child per server across all routes.
type State = { child?: ChildProcess; engine?: Engine }
const g = globalThis as typeof globalThis & { __kellEngineState?: State }
const state: State = g.__kellEngineState || (g.__kellEngineState = {})

export const engineStatus = () => (state.engine && state.child && !state.child.killed ? state.engine : undefined)

// Starts (or reuses) the engine for a project directory. `opencode serve` prints a "server listening on <url>" line
// and a "server password <x>" line on stdout; the token protects its loopback API.
export async function engineStart(directory: string): Promise<Engine> {
  const exe = findEngineExe()
  if (!exe) throw new Error("opencode.exe not found; set KELL_OC_ENGINE or run `kell setup --client opencode`")
  if (state.engine && state.child && !state.child.killed && state.engine.directory === directory) return state.engine
  engineStop()
  let engine: Engine | undefined
  engine = await new Promise<Engine>((ok, no) => {
    const read = (chunk: any, fail: boolean) => {
      const text = String(chunk)
      const url = text.match(/server listening on (http\S+)/)?.[1]
      const pass = text.match(/server password (\S+)/)?.[1]
      if (url && pass && engine === undefined) {
        clearTimeout(timer)
        ok(engine = { baseUrl: url, password: pass, directory })
      }
      if (fail && !engine) no(new Error("engine failed to start"))
    }
    state.child = spawn(exe, ["serve", "--hostname", "127.0.0.1", "--port", "0"], { cwd: directory, stdio: [null, "pipe", "pipe"] })
    const timer = setTimeout(() => { if (!engine) no(new Error("engine did not report a port in 30 s")) }, 30_000)
    state.child.stdout?.on("data", (c: Buffer) => read(c.toString(), false))
    state.child.stderr?.on("data", (c: Buffer) => read(c.toString(), true))
    state.child.on("exit", () => { state.engine = undefined; state.child = undefined })
  })
  state.engine = engine
  return engine
}

export function engineStop() {
  if (state.child) { try { state.child.kill() } catch { /* best effort */ } }
  state.engine = undefined
  state.child = undefined
}

// OpenCode serve's /api/* surface (the generated client's paths) wants HTTP Basic auth, uid "opencode", the printed
// password (it answers WWW-Authenticate: Basic on that surface; the bearer token is for other routes).
export const authHeader = (e: Engine): string =>
  "Basic " + Buffer.from(`opencode:${e.password}`, "utf8").toString("base64")

export const clientFor = (e: Engine): Client =>
  OpenCode.make({ baseUrl: e.baseUrl, headers: { authorization: authHeader(e) } })
