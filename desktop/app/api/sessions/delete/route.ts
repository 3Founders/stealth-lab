// POST { id }: delete one chat. The app's own store goes first (messages, attachments, summaries, the row), then the
// engine's session, so the sidebar forgets it even when the engine is down. A running turn is stopped by the page
// before it calls this. The engine's copy is a best-effort second half: a session it no longer has is not an error
// (2026-11-17).
import { NextResponse } from "next/server"
import { clientFor, engineStatus } from "@/lib/engine"
import { deleteSession, getSession } from "@/lib/localdb"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as { id?: unknown }))
  const id = String(body.id || "")
  if (!id) return NextResponse.json({ error: "a chat is needed" }, { status: 400 })
  const sid = getSession(id)?.engine_id || id
  let local = false
  try { local = deleteSession(id) } catch (err) {
    return NextResponse.json({ error: String((err as any)?.message || err) }, { status: 500 })
  }
  const e = engineStatus()
  let engine = false
  if (e) {
    try {
      const client = await clientFor(e)
      await client.session.remove({ sessionID: sid as any })
      engine = true
    } catch { /* the app's store is what the sidebar reads; the engine copy is a nicety */ }
  }
  return NextResponse.json({ ok: true, id, local, engine })
}
