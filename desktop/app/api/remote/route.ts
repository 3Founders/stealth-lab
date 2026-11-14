// GET -> { on, started, busy, last, device }: whether the rooms can call this desktop's sessions (lib/remote.ts).
// POST { on }: turn it on or off. Off: no sessions are reported and no ask is taken; the rooms show the desktop offline.
import { remoteOn, remoteStatus, setRemoteOn } from "@/lib/remote"

export const dynamic = "force-dynamic"

export async function GET() {
  return Response.json({ on: remoteOn(), ...remoteStatus() })
}

export async function POST(req: Request) {
  let body: any = {}
  try { body = await req.json() } catch { /* checked below */ }
  if (typeof body.on !== "boolean") return Response.json({ error: "send on: true or false" }, { status: 400 })
  setRemoteOn(body.on)
  return Response.json({ on: remoteOn(), ...remoteStatus() })
}
