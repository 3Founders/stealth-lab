// POST { sessionID, upTo?: { who, text, at } } -> { sessionID, title }: a copy of a session that continues on its own
// (owner, 2026-11-14).
//
// The engine forks the session (OpenCode's session.fork: the new session has the same conversation and context, and its
// next prompt does not touch the original). `upTo` is a message on screen: the fork keeps every message up to and
// including it, so "Fork from here" on an answer drops what came after. Without it the whole session is copied.
//
// The app's own history (chats.db) is copied too, the same lines, so the fork opens with its conversation on screen. The
// engine's cut is found from the end: the user message right after `upTo` is the r-th user message from the newest, which
// works for a session longer than the 200 messages one engine call returns.
import { clientFor, engineStatus } from "@/lib/engine"
import { getMessages, getSession, importSession } from "@/lib/localdb"
import { localLines } from "@/lib/history"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "engine is down" }, { status: 502 })
  let body: any = {}
  try { body = await req.json() } catch { /* checked below */ }
  const id = String(body.sessionID || "")
  if (!id) return Response.json({ error: "send sessionID" }, { status: 400 })
  try {
    const client = await clientFor(e)
    const session = getSession(id)
    await localLines(id, session?.directory ?? e.directory, session?.title ?? null)   // backfills an engine-only chat
    const messages = getMessages(id, 100_000)
    let upTo = messages.length - 1
    if (body.upTo && typeof body.upTo.text === "string") {
      // the stored message with this role and text; of several, the one nearest the time on screen
      const role = body.upTo.who === "user" ? "user" : "assistant"
      const at = Number(body.upTo.at) || 0
      const hits = messages.map((m, i) => ({ m, i })).filter(({ m }) => m.role === role && m.text.trim() === body.upTo.text.trim())
      if (!hits.length) return Response.json({ error: "that message is not in this session's saved history" }, { status: 404 })
      upTo = hits.reduce((a, b) => (Math.abs(b.m.created_at - at) < Math.abs(a.m.created_at - at) ? b : a)).i
    }
    const kept = messages.slice(0, upTo + 1)
    const after = messages.slice(upTo + 1)

    let before: string | undefined
    const want = after.find((m) => m.role === "user")
    if (want) {
      // Find the first user message AFTER the cut, by its text, walking the engine newest-first until it is found.
      // The old code asked for one page of 200 and then indexed r-1 into it, where r is a count taken from the LOCAL
      // history: in any chat longer than one page that window does not reach back far enough, so the index landed on the
      // wrong message (or off the end) and every fork of a long chat failed with "could not line this point up".
      // Paging to the match makes the answer depend on whether the message is in the session, not on how long it is.
      const target = want.text.trim().slice(0, 200)
      let cursor: string | undefined
      let cut: any = undefined
      for (let page = 0; page < 50 && !cut; page++) {
        const out = await client.message.list({ sessionID: id as any, limit: 200, order: "desc", ...(cursor ? { cursor } : {}) })
        const rows = (out.data as any[]).filter((m) => m.type === "user" || (m.type === undefined && typeof m.text === "string"))
        cut = rows.find((m) => String(m.text || "").trim().slice(0, 200) === target)
        cursor = out.cursor?.next ?? undefined
        if (!cursor) break                                          // the engine has no older messages to give
      }
      if (!cut)
        return Response.json({ error: "that message is no longer in the session; fork the whole session instead" }, { status: 409 })
      before = String(cut.id)
    }

    const forked: any = await client.session.fork({ sessionID: id as any, ...(before ? { before: before as any } : {}) })
    const newID = String(forked?.id || forked?.data?.id || "")
    if (!newID) return Response.json({ error: "the engine did not fork the session" }, { status: 502 })
    const title = `Fork of ${session?.title || "a session"}`.slice(0, 80)
    // metadata.kell: the sidebar lists only the app's own sessions (app/api/sessions isKellSession)
    try { await client.session.update({ sessionID: newID as any, title, metadata: { kell: true } as any }) } catch { /* cosmetic */ }
    importSession({
      id: newID, engineId: newID, source: "kell", title, directory: session?.directory ?? e.directory,
      messages: kept.map((m) => ({ role: m.role as any, text: m.text, at: m.created_at, model: m.model ?? undefined,
        provider: m.provider ?? undefined, summary: m.summary ?? undefined })),
    })
    return Response.json({ sessionID: newID, title })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 502 })
  }
}
