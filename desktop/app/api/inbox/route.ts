// GET /api/inbox -> { items }: everything waiting on the person, across chats and schedules (lib/inbox.ts). Permission answers go to
// /api/permission as from the chat itself.
import { inbox } from "@/lib/inbox"
import { startSchedules } from "@/lib/schedules"

export const dynamic = "force-dynamic"

export async function GET() {
  startSchedules()
  try { return Response.json({ items: inbox() }) }
  catch (err: any) { return Response.json({ items: [], error: String(err?.message || err) }) }
}
