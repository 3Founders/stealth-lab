// Settings / Account, as data: GET answers what the app can honestly say about this machine's kell.
//
//   signedIn   the hosted server's address is saved and a token is present (~/.stealthlab/config.json, the file kell login
//              writes). The token itself is never returned -- only that one exists.
//   models     cheap / strong / fallback and mode from ~/.stealthlab/opencode/config.json (what `kell setup --client
//              opencode` writes and what the engine's plugin reads).
//   engine     the opencode binary the app would boot, and the project it is on.
//   console    "POST" { action: "open" } runs `kell console open` (starts the helper if needed, opens its tokened page).
//
// Read-only on files: nothing here writes a key or flips a flag.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawn } from "node:child_process"
import { NextResponse } from "next/server"
import { engineStatus, findEngineExe } from "@/lib/engine"
import { pooledEngineConfig, ownKeyLane, personalModelIds, personalEngineConfig, gatewayOrigin } from "@/lib/pooled"
import { kellCommand } from "@/lib/cli"

export const dynamic = "force-dynamic"

let lastAsk = 0   // when this server last asked the gateway for the personal model list

const kellHome = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
const readJson = (file: string) => { try { return JSON.parse(fs.readFileSync(file, "utf8")) } catch { return null } }

export async function GET() {
  const cfg = readJson(path.join(kellHome(), "config.json")) || {}
  const oc = readJson(path.join(kellHome(), "opencode", "config.json")) || {}
  const e = engineStatus()
  let server: string | null = null
  if (typeof cfg.url === "string") { try { server = new URL(cfg.url).host } catch { server = "saved" } }

  // Which lane the engine will actually run on: the organization gateway (pooled keys, no provider key on this
  // machine) when `kell join` wrote profiles, else the developer-held key lane from `kell setup --client opencode`.
  const pooled = pooledEngineConfig()
  let lane: "organization" | "personal" | "own-key" | "none" = "none"
  let gateway: string | null = null
  let models = { cheap: oc.cheap || null, strong: oc.strong || null, fallback: oc.fallback || null, mode: oc.mode || null }
  if (pooled) {
    try {
      const c = JSON.parse(pooled.content)
      const k = c?.provider?.kell
      lane = "organization"
      gateway = k?.options?.baseURL || null
      const ids = Object.keys(k?.models || {})
      models = { cheap: ids[0] || null, strong: String(c?.model || "").replace(/^kell\//, "") || null, fallback: null, mode: "session" }
    } catch { lane = "none" }
  } else if (ownKeyLane()) {
    lane = "own-key"
  } else if (cfg.token) {
    // Signed in, in no team, no developer key: the person's own plan pays through kell's gateway. The model list is the
    // gateway's, known once the engine has started.
    lane = "personal"
    gateway = gatewayOrigin(cfg)
    // The model list is the gateway's. Ask for it here too (at most once a minute) so the sidebar names the models before the
    // engine has started, instead of "not set".
    if (!personalModelIds().length && Date.now() - lastAsk > 60_000) { lastAsk = Date.now(); await personalEngineConfig().catch(() => undefined) }
    const ids = personalModelIds()
    if (ids.length) models = { cheap: ids[0], strong: ids[ids.length - 1], fallback: null, mode: "session" }
  }

  // Who the saved token belongs to, read from its payload for display and for the app's same-account check with the
  // rooms only (the token is not verified here and never leaves this route; the server verifies it on every call).
  let who: { sub?: string; email?: string } = {}
  try { who = JSON.parse(Buffer.from(String(cfg.token).split(".")[1], "base64url").toString("utf8")) || {} } catch { /* not a JWT */ }

  return NextResponse.json({
    signedIn: !!cfg.token,                               // no saved url is fine: the CLI falls back to its default server
    account: who.email || cfg.email || cfg.account || null,
    subject: who.sub || null,
    server,
    lane,
    gateway,
    models,
    engine: { exe: findEngineExe() || null, directory: e?.directory || null },
  })
}

export async function POST(req: Request) {
  try {
    const { action } = await req.json()
    if (action !== "open") return NextResponse.json({ error: `unknown action ${action}` }, { status: 400 })
    const r = await new Promise<{ output: string; code: number | null }>((ok) => {
      const c = kellCommand(["console", "open"])
      const child = spawn(c.cmd, c.args, { shell: c.shell, windowsHide: true, timeout: 30_000, env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" } })
      let out = ""
      child.stdout?.on("data", (c) => { out += String(c) })
      child.stderr?.on("data", (c) => { out += String(c) })
      child.on("close", (code) => ok({ output: out.trim(), code }))
      child.on("error", (err) => ok({ output: String(err.message || err), code: -1 }))
    })
    return NextResponse.json(r)
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 500 })
  }
}
