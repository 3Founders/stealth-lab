// The app's own session history (docs/localdb.md), independent of OpenCode's opencode.db.
//
//   GET /api/local/sessions[?directory=<abs>]   one project's local chats
//   GET /api/local/sessions?id=<id>             one session's messages; a session with no local messages is backfilled
//                                               from the engine once, then served from the local store
import path from "node:path"
import { getSession, listSessions, messageCount } from "@/lib/localdb"
import { localLines } from "@/lib/history"

export const dynamic = "force-dynamic"

/** Windows gives `C:\x`, the engine stores `C:/x`: compare them the same way everywhere. */
const norm = (p: string) => path.resolve(p).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams
  const id = q.get("id")
  try {
    if (!id) {
      // One project's chats, not every folder's: the sidebar shows each project's own list.
      const dir = q.get("directory") || ""
      const rows = listSessions().filter((s) => !dir || (s.directory && norm(s.directory) === norm(dir)))
      return Response.json({ sessions: rows.map((s) => ({ id: s.id, title: s.title || "", updated: s.updated_at, directory: s.directory })) })
    }
    const session = getSession(id)
    const lines = await localLines(id, session?.directory ?? null, session?.title ?? null)
    return Response.json({
      session: session ? { id: session.id, title: session.title || "", updated: session.updated_at } : null,
      lines,
      local: true,
      messages: messageCount(id),
    })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), sessions: [], lines: [] }, { status: 500 })
  }
}
