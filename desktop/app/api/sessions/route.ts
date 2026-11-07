// The open project's past sessions, and one session's conversation to show again (owner, 2026-11-07: the sidebar listed projects but
// not sessions). Sessions live in the engine's own store, so they are still there after the app is closed and opened again; this only
// reads them. Sub-agent sessions (a parent session started them) are left out of the list.
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

type Line = { who: "user" | "assistant"; text: string; at?: number }

export async function GET(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ sessions: [], lines: [] })
  const id = new URL(req.url).searchParams.get("id")
  try {
    const client = await clientFor(e)
    if (!id) {
      const out = await client.session.list({ directory: e.directory as any, limit: 50, order: "desc" })
      const sessions = (out.data as any[]).filter((s) => !s.parentID).map((s) => ({
        id: String(s.id), title: typeof s.title === "string" ? s.title : "", updated: Number(s.time?.updated ?? s.time?.created ?? 0),
      }))
      return Response.json({ sessions })
    }
    // The engine gives at most 200 messages a call: the newest 200, put back in order (a long session shows its latest part).
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
        if (last?.who === "assistant") last.text += `\n\n${text}`     // one answer over several steps reads as one message
        else lines.push({ who: "assistant", text, at })
      }
    }
    return Response.json({ lines })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), sessions: [], lines: [] }, { status: 502 })
  }
}
