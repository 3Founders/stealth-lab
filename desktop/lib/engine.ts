// The engine: a local `opencode serve` child with kell's plugin on its config, found the way `kell setup --client opencode`
// installs it. The server routes talk to it through the tokened HTTP API (@opencode/client), required lazily: its package is
// ESM-only with no CJS "." export, so a top-level require fails from a CJS context (the Electron main process does not load
// this module: the engine is owned by the Next child and dies with its tree). The client's types live in oc-types.d.ts, which
// keeps the type reference out of webpack's resolution.
import { spawn, type ChildProcess } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import type { OC, Client } from "./oc-types"
import { pooledEngineConfig, personalEngineConfig, ownKeyLane } from "./pooled"
import { freshSignIn } from "./signin"

export type { Client }

export type Engine = { baseUrl: string; password: string; directory: string }

// The platform package inside the app (electron-builder unpacks these binaries beside app.asar) and the binary's name.
const platformBin = () => (process.platform === "win32" ? "opencode.exe" : "opencode")
const platformPkg = () =>
  process.platform === "win32" ? "cli-windows-x64"
    : process.platform === "darwin" ? (process.arch === "arm64" ? "cli-darwin-arm64" : "cli-darwin-x64")
      : (process.arch === "arm64" ? "cli-linux-arm64" : "cli-linux-x64")
// Next runs with cwd = the app root; in the packaged app that is .../resources/app.asar, whose unpacked sibling holds the
// binaries (a file inside an asar cannot be spawned).
// KELL_ROOT: the app's own folder. The packaged app runs Next's standalone server, which changes its working directory to
// .next/standalone, so the working directory no longer says where the app is.
const appRoot = () => process.env.KELL_ROOT || process.cwd()
const unpackedRoot = () => {
  const cwd = appRoot()
  return cwd.endsWith("app.asar") ? `${cwd}.unpacked` : path.join(cwd, "..", "app.asar.unpacked")
}

const exes = () => [
  process.env.KELL_OC_ENGINE || "",
  // the copy this app ships (preferred): the platform binary beside the asar when packaged, the launcher package in the
  // dev tree. Pinning the bundled copy keeps the engine on the version the app tested and stops a global OpenCode
  // install (with its own plugins, LSPs and MCPs) from being loaded into this app.
  path.join(unpackedRoot(), "node_modules", "@opencode", platformPkg(), "bin", platformBin()),
  path.join(unpackedRoot(), "node_modules", "@opencode", "cli", "bin", platformBin()),
  path.join(appRoot(), "node_modules", "@opencode", platformPkg(), "bin", platformBin()),
  path.join(appRoot(), "node_modules", "@opencode", "cli", "bin", platformBin()),
  // `kell setup --client opencode` (isolated profile) and the global install, both Windows and unix layouts
  path.join(process.env.KELL_OC_PROFILE || path.join(process.env.HOME || process.env.USERPROFILE || "", ".stealthlab", "opencode"), "cli", "node_modules", "@opencode", "cli", "bin", platformBin()),
  path.join(process.env.APPDATA || "", "npm", "node_modules", "@opencode", "cli", "bin", platformBin()),
].filter(Boolean)

export const findEngineExe = () => exes().find((e) => { try { return fs.statSync(e).isFile() } catch { return false } }) || ""

// Next.js bundles each route with its own module instance, so the engine singleton lives on globalThis —
// that is what keeps one `opencode serve` child per server across all routes.
type State = { child?: ChildProcess; engine?: Engine }
const g = globalThis as typeof globalThis & { __kellEngineState?: State }
const state: State = g.__kellEngineState || (g.__kellEngineState = {})

let OpenCodeCtor: OC["OpenCode"] | undefined

export const engineStatus = () => (state.engine && state.child && !state.child.killed ? state.engine : undefined)

// Starts (or reuses) the engine for a project directory. `opencode serve` prints a "server listening on <url>" line
// and a "server password <x>" line on stdout; the token protects its loopback API.
export async function engineStart(directory: string): Promise<Engine> {
  const exe = findEngineExe()
  if (!exe) throw new Error("opencode.exe not found; set KELL_OC_ENGINE or run `kell setup --client opencode`")
  if (state.engine && state.child && !state.child.killed && state.engine.directory === directory) return state.engine
  engineStop()
  // Which lane pays: a joined organization first; else a developer's own key when `kell setup --client opencode` set one up;
  // else the signed-in person's own plan through kell's gateway (the models come from the gateway, so this waits on it).
  await freshSignIn()                                  // an expired sign-in made every gateway call answer 401
  let pooled = pooledEngineConfig()
  if (!pooled && !ownKeyLane()) {
    const personal = await personalEngineConfig()
    if (!personal.ok) throw new Error(personal.message)
    pooled = personal.engine
  }
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
    // The organization lane: when this machine ran `kell join`, the child gets an inline provider on the company's
    // gateway, authenticated with the person's kell sign-in. Nothing provider-shaped is on the machine.
    state.child = spawn(exe, ["serve", "--hostname", "127.0.0.1", "--port", "0"], {
      cwd: directory, stdio: [null, "pipe", "pipe"],
      windowsHide: true,        // a console program started from a GUI app opens a visible black window on Windows without this
      env: {
        ...process.env,
        ...(pooled ? { ...pooled.env, OPENCODE_CONFIG_CONTENT: pooled.content, OPEN_MODEL_API_KEY: pooled.key } : {}),
        // RAM: a desktop coding session does not need a file watcher, a language server, update checks or sharing. The
        // watcher alone can keep one watch per indexed directory; LSPs and per-session MCPs multiply across sessions.
        OPENCODE_DISABLE_FILEWATCHER: "1",
        OPENCODE_FILEWATCHER_DISABLE: "1",
        OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER: "1",
        OPENCODE_DISABLE_LSP_DOWNLOAD: "1",
        OPENCODE_DISABLE_AUTOUPDATE: "1",
        OPENCODE_DISABLE_SHARE: "1",
        OPENCODE_DISABLE_PRUNE: "1",
        OPENCODE_DISABLE_TERMINAL_TITLE: "1",
        OPENCODE_EXPERIMENTAL_INSTANCE_IDLE_TIMEOUT_MS: "3600000",
        SHELLCHECK_EXTERNAL_SOURCES: "false",
      },
    })
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

// The client loads through `import()`, not `require`: inside Next's bundle a `require("@opencode/client")` bare is marked
// weakly and webpack must resolve it, which it cannot (the package's "." export is import-only). On the Electron path
// (plain CJS, no webpack) `import()` compiles to a require anyway.
export const clientFor = async (e: Engine): Promise<Client> => {
  if (!OpenCodeCtor) OpenCodeCtor = (await import("@opencode/client")).OpenCode
  return OpenCodeCtor.make({ baseUrl: e.baseUrl, headers: { authorization: authHeader(e) } })
}
