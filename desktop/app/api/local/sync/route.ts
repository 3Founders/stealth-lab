// Backfill the local chat store from the engine once, so a person who used the app before docs/localdb.md keeps their
// session list. Messages are copied lazily when a session is first opened (/api/local/sessions?id=), not here.
import { clientFor, engineStatus } from "@/lib/engine"
import { ensureSession, sessionExists } from "@/lib/localdb"

export const dynamic = "force-dynamic"

export async function POST() {
  const e = engineStatus()
  if (!e) return Response.json({ added: 0, total: 0, reason: "engine is down" })
  try {
    const client = await clientFor(e)
    const out = await client.session.list({ directory: e.directory as any, limit: 100, order: "desc" })
    const rows = (out.data as any[]).filter((s) => !s.parentID)
    let added = 0
    for (const s of rows) {
      // The app's own sessions carry metadata.kell; leave OpenCode CLI sessions out of the app's list, as before.
      if (s.metadata?.kell !== true) continue
      const id = String(s.id)
      if (sessionExists(id)) continue
      ensureSession({
        id, engineId: id, source: "opencode", title: typeof s.title === "string" ? s.title : undefined,
        directory: e.directory, createdAt: Number(s.time?.created) || undefined, updatedAt: Number(s.time?.updated) || undefined,
      })
      added += 1
    }
    return Response.json({ added, total: rows.length })
  } catch (err: any) {
    return Response.json({ added: 0, total: 0, error: String(err?.message || err) }, { status: 502 })
  }
}
