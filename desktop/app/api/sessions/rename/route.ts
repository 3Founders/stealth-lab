// POST { id, title }: the person's own name for a chat.
//
// The app's own store is what the sidebar reads (docs/localdb.md), so the name is written there first and is what the
// person sees at once. The engine's session gets the same title too, so its own header and any other client agree; a
// rename that only reaches one of them still shows here, and never fails the click (2026-11-17).
import { NextResponse } from "next/server"
import { clientFor, engineStatus } from "@/lib/engine"
import { getSession, renameSession } from "@/lib/localdb"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({} as { id?: unknown; title?: unknown }))
  const id = String(body.id || "")
  const title = String(body.title || "").replace(/\s+/g, " ").trim().slice(0, 120)
  if (!id || !title) return NextResponse.json({ error: "a chat and a name are needed" }, { status: 400 })
  renameSession(id, title)
  const e = engineStatus()
  let engine = false
  if (e) {
    try {
      const client = await clientFor(e)
      const sid = getSession(id)?.engine_id || id
      await client.session.update({ sessionID: sid as any, title })
      engine = true
    } catch { /* the app's own name stands */ }
  }
  return NextResponse.json({ ok: true, id, title, engine })
}
