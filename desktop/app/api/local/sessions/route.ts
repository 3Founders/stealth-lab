// The app's own session history (docs/localdb.md), independent of OpenCode's opencode.db.
//
//   GET /api/local/sessions            the local session list
//   GET /api/local/sessions?id=<id>    one session's messages; a session with no local messages is backfilled from the
//                                      engine once, then served from the local store
import { clientFor, engineStatus } from "@/lib/engine"
import { appendMessage, ensureSession, getMessages, getSession, listSessions, messageCount } from "@/lib/localdb"

export const dynamic = "force-dynamic"

type Line = { who: "user" | "assistant"; text: string; at?: number }

async function engineLines(client: any, id: string): Promise<Line[]> {
  const out = await client.message.list({ sessionID: id as any, limit: 200, order: "desc" })
  const lines: Line[] = []
  for (const m of [...(out.data as any[])].reverse()) {
    const at = Number(m.time?.created) || undefined
    if (m.type === "user" || (m.type === undefined && typeof m.text === "string")) {
      if (String(m.text || "").trim()) lines.push({ who: "user", text: String(m.text), at })
    } else if (m.type === "assistant") {
      const text = (Array.isArray(m.content) ? m.content : []).filter((c: any) => c?.type === "text").map((c: any) => String(c.text || "")).join("")
      if (!text.trim()) continue
      const last = lines.at(-1)
      if (last?.who === "assistant") last.text += `\n\n${text}`
      else lines.push({ who: "assistant", text, at })
    }
  }
  return lines
}

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams
  const id = q.get("id")
  try {
    if (!id) {
      return Response.json({ sessions: listSessions().map((s) => ({ id: s.id, title: s.title || "", updated: s.updated_at })) })
    }
    const session = getSession(id)
    let messages = getMessages(id)
    // A session that predates the local store (or an OpenCode CLI session) is copied once, lazily, so the first open
    // is the only slow one. A session that already has local messages is never re-read from the engine.
    if (!messages.length) {
      const e = engineStatus()
      if (e) {
        try {
          const client = await clientFor(e)
          const lines = await engineLines(client, id)
          if (lines.length) {
            ensureSession({ id, engineId: id, source: "opencode", title: session?.title || undefined, directory: e.directory })
            for (const l of lines) appendMessage(id, { role: l.who, text: l.text, at: l.at })
            messages = getMessages(id)
          }
        } catch { /* the local copy is still valid; the engine was only a backfill source */ }
      }
    }
    return Response.json({
      session: session ? { id: session.id, title: session.title || "", updated: session.updated_at } : null,
      lines: messages.map((m) => ({ who: m.role === "assistant" ? "assistant" : "user", text: m.text, at: m.created_at })),
      local: true,
      messages: messageCount(id),
    })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), sessions: [], lines: [] }, { status: 500 })
  }
}
