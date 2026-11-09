// POST { sessionIDs }: mark the chosen sessions as kell sessions, so they appear in the app's sidebar and can be
// continued here. The engine's store is shared with the `opencode` CLI; this only adds `metadata.kell = true`.
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "engine is down; open a project first" }, { status: 502 })
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "bad request" }, { status: 400 }) }
  const ids: string[] = Array.isArray(body?.sessionIDs)
    ? body.sessionIDs.filter((x: unknown) => typeof x === "string").slice(0, 200)
    : []
  if (!ids.length) return Response.json({ error: "choose at least one chat" }, { status: 400 })
  try {
    const client = await clientFor(e)
    let imported = 0
    for (const id of ids) {
      const s = await client.session.get({ sessionID: id })
      const metadata = s.metadata && typeof s.metadata === "object" ? { ...s.metadata, kell: true } : { kell: true }
      await client.session.update({ sessionID: id, metadata })
      imported += 1
    }
    return Response.json({ imported })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 502 })
  }
}
