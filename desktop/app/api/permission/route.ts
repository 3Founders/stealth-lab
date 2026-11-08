// POST { sessionID, requestID, decision: once | always | reject }: the person's answer to a permission the engine asked for in the chat
// (permission.asked, shown by page.tsx). The engine's turn waits for it.
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "engine is down" }, { status: 502 })
  let body: any = {}
  try { body = await req.json() } catch { /* checked below */ }
  const decision = String(body.decision || "")
  if (!body.sessionID || !body.requestID || !["once", "always", "reject"].includes(decision)) {
    return Response.json({ error: "send sessionID, requestID and decision (once, always or reject)" }, { status: 400 })
  }
  try {
    const client = await clientFor(e)
    await client.permission.reply({ sessionID: String(body.sessionID), requestID: String(body.requestID), decision } as any)
    return Response.json({ ok: true })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 502 })
  }
}
