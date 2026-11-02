// Tool suggestions as cards: POST { need } asks kell's tool router (POST <server>/tools) which tools fit a request --
// the same call the plugin's native layer makes (experiments/opencode_kell/plugin/native.mjs), same config
// (~/.stealthlab/config.json { url, token }), so the cards show what the session would be offered.
//
// The router answers { tools: [{ name, source, url?, requires_auth?, oauth?, needs_key?, ... }] }. The card shows the
// name, where it comes from, and whether it can be added without a sign-in; "use through kell" is the gateway path --
// the tools run on kell's side, nothing is installed here.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { NextResponse } from "next/server"

export const dynamic = "force-dynamic"

const config = () => {
  const home = process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
  let c: any = {}
  try { c = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf8")) } catch { /* not signed in */ }
  return { url: process.env.STEALTHLAB_MCP_URL || process.env.STEALTHLAB_URL || c.url, token: c.token || process.env.STEALTHLAB_TOKEN }
}

const sibling = (mcpUrl: string, name: string) => {
  try {
    const u = new URL(mcpUrl); u.pathname = u.pathname.replace(/\/mcp\/?$/, "").replace(/\/$/, "") + name; u.search = ""; u.hash = ""
    return u.toString()
  } catch { return null }
}

export async function POST(req: Request) {
  const { url, token } = config()
  const target = url && sibling(url, "/tools")
  if (!target) return NextResponse.json({ error: "kell is not signed in on this machine (no server in ~/.stealthlab/config.json)" }, { status: 401 })
  let need = ""
  try { need = String((await req.json()).need || "").slice(0, 4000) } catch { /* empty */ }
  try {
    const r = await fetch(target, {
      method: "POST", signal: AbortSignal.timeout(25_000), body: JSON.stringify({ need, installed: [], k: 5 }),
      headers: { accept: "application/json", "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    })
    if (!r.ok) return NextResponse.json({ error: `the tool router answered ${r.status}` }, { status: 502 })
    const reply = await r.json()
    const tools = (Array.isArray(reply?.tools) ? reply.tools : []).slice(0, 5).map((t: any) => ({
      name: String(t?.name || ""), source: t?.source === "installed" ? "installed" : "registry",
      url: typeof t?.url === "string" ? t.url : null,
      needsSignIn: !!(t?.requires_auth || t?.oauth || t?.needs_key),
    })).filter((t: any) => t.name)
    return NextResponse.json({ tools })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 502 })
  }
}
