// POST { sessionID } -> { ok }: /compact. The engine summarises the chat's older turns and continues from that summary, so a long chat
// costs less and stays inside the model's window. (Automatic compaction is on too: lib/pooled.ts config.compaction.)
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "the engine is not running" }, { status: 502 })
  const b = await req.json().catch(() => ({} as any))
  if (!b.sessionID) return Response.json({ error: "no chat to compact yet" }, { status: 400 })
  try {
    const client: any = await clientFor(e)
    await client.session.compact({ sessionID: String(b.sessionID), delivery: "queue" })
    return Response.json({ ok: true })
  } catch (err: any) { return Response.json({ error: String(err?.message || err).slice(0, 300) }, { status: 502 }) }
}
