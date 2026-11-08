// Tool suggestions as cards: POST { need } -> the tools kell would offer for this request (lib/tools.ts askTools: the triage, then the
// router for the tool goal it names), so the cards show what the session is offered. No goal: no outside tool is needed, and no cards.
//
// A card shows the name, where it comes from, and whether it needs a sign-in first; "use through kell" is the gateway path -- the tools
// run on kell's side, nothing is installed here.
import { NextResponse } from "next/server"
import { askTools, needsSignIn } from "@/lib/tools"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  let need = ""
  try { need = String((await req.json()).need || "").slice(0, 4000) } catch { /* empty */ }
  if (!need.trim()) return NextResponse.json({ tools: [] })
  try {
    const { goal, tools } = await askTools(need)
    return NextResponse.json({ goal, tools: tools.slice(0, 5).map((t) => ({
      name: String(t.name || t.server || ""), source: t.source === "installed" ? "installed" : "registry",
      url: typeof t.url === "string" ? t.url : null, needsSignIn: needsSignIn(t),
    })).filter((t) => t.name) })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: err?.status || 502 })
  }
}
