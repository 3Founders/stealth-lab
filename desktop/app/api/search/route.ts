// GET /api/search?q=: one query across every chat in the app's own store, not only the open project. FTS5 first (fast,
// ranked by recency); a plain substring search when FTS cannot parse the query (punctuation, a script it does not
// tokenise). The result names the chat and the folder so a click can open it, and carries the message's time so the
// chat can scroll to that line (2026-11-17).
import { listSessions, search, searchText } from "@/lib/localdb"

export const dynamic = "force-dynamic"

/** A hit's text, with the match in view rather than the start of a long message. */
function clip(text: string, q: string): string {
  const one = text.replace(/\s+/g, " ").trim()
  if (one.length <= 180) return one
  const at = one.toLowerCase().indexOf(q.toLowerCase())
  const start = Math.max(0, at < 0 ? 0 : at - 70)
  return (start ? "…" : "") + one.slice(start, start + 180) + (start + 180 < one.length ? "…" : "")
}

export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get("q") || "").trim()
  if (q.length < 2) return Response.json({ results: [] })
  let rows = search(q, 80)
  if (!rows.length) rows = searchText(q, 80)
  const sessions = new Map(listSessions(500).map((s) => [s.id, s]))
  const results = rows.map((r) => {
    const s = sessions.get(r.session_id)
    return {
      id: r.message_id,
      sessionID: r.session_id,
      title: s?.title || "",
      directory: s?.directory || null,
      at: r.created_at,
      snippet: clip(String(r.text || ""), q),
    }
  })
  return Response.json({ results })
}
