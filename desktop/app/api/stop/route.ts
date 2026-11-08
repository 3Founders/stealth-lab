// POST { sessionID }: stop the turn that is running (reqs §5, "always-reachable stop/panic control").
//
// The engine's interrupt ends the turn at its next safe point. Work that already ran is NOT undone -- the UI says so
// when the person presses it. A permission the turn is waiting on is not an answer either way.
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "engine is down" }, { status: 502 })
  let body: any = {}
  try { body = await req.json() } catch { /* checked below */ }
  if (!body.sessionID) return Response.json({ error: "send sessionID" }, { status: 400 })
  try {
    const client = await clientFor(e)
    const r = await client.session.interrupt({ sessionID: String(body.sessionID) })
    return Response.json({ ok: true, interrupted: !!r?.interrupted })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 502 })
  }
}
