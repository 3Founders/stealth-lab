// Tool suggestions as cards: POST { need } asks kell's tool router (POST <server>/tools) which tools fit a request --
// the same call the plugin's native layer makes (experiments/opencode_kell/plugin/native.mjs), same config
// (~/.stealthlab/config.json { url, token }), so the cards show what the session would be offered.
//
// The router answers { tools: [{ name, source, url?, requires_auth?, oauth?, needs_key?, ... }] }. The card shows the
// name, where it comes from, and whether it can be added without a sign-in; "use through kell" is the gateway path --
// the tools run on kell's side, nothing is installed here.
import { NextResponse } from "next/server"
import { askRouter } from "@/lib/tools"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  let need = ""
  try { need = String((await req.json()).need || "").slice(0, 4000) } catch { /* empty */ }
  try {
    const tools = (await askRouter(need)).slice(0, 5).map((t) => ({
      name: String(t.name || ""), source: t.source === "installed" ? "installed" : "registry",
      url: typeof t.url === "string" ? t.url : null,
      needsSignIn: !!(t.requires_auth || t.oauth || t.needs_key),
    })).filter((t) => t.name)
    return NextResponse.json({ tools })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: err?.status || 502 })
  }
}
