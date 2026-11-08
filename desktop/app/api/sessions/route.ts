// The open project's past sessions, and one session's conversation to show again (owner, 2026-11-07: the sidebar listed projects but
// not sessions). Sessions live in the engine's own store, so they are still there after the app is closed and opened again; this only
// reads them. Sub-agent sessions (a parent session started them) are left out of the list.
import fs from "node:fs"
import os from "node:os"
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

type Line = { who: "user" | "assistant"; text: string; at?: number }

// Sessions the app started, not the ones the person started with the `opencode` CLI in the same folder. Every app
// prompt writes a `kell-tools` instruction entry into its session; that is the marker. The answer is cached, so a
// refresh only asks about ids the app has not seen before.
const kellSessions = new Map<string, boolean>()
async function isKellSession(client: any, id: string, metadata: any): Promise<boolean> {
  if (metadata?.kell === true) return true
  const known = kellSessions.get(id)
  if (known !== undefined) return known
  try {
    const entries = await client.experimental.session.instructions.entry.list({ sessionID: id })
    const ok = Array.isArray(entries) && entries.some((e: any) => e?.key === "kell-tools")
    kellSessions.set(id, ok)
    return ok
  } catch {
    kellSessions.set(id, false)
    return false
  }
}

export async function GET(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ sessions: [], lines: [] })
  const q = new URL(req.url).searchParams
  const id = q.get("id")
  try {
    const client = await clientFor(e)
    if (q.get("projects")) {
      // Every folder the engine has worked in, last active first, for the sidebar's recent projects: real folders only (not "/" or the
      // home folder, which the engine records when it was started outside a project), and only ones that still exist.
      const home = os.homedir().toLowerCase()
      const projects = (await client.project.list() as any[])
        .map((p) => ({ dir: String(p.canonical || ""), active: Number(p.time?.active ?? p.time?.updated ?? 0) }))
        .filter((p) => p.dir && p.dir !== "/" && p.dir.toLowerCase() !== home && fs.existsSync(p.dir))
        .sort((a, b) => b.active - a.active)
      return Response.json({ projects })
    }
    if (!id) {
      const out = await client.session.list({ directory: e.directory as any, limit: 50, order: "desc" })
      const rows = (out.data as any[]).filter((s) => !s.parentID)
      const flags = await Promise.all(rows.map((s) => isKellSession(client, String(s.id), s.metadata)))
      const sessions = rows.filter((_, i) => flags[i]).map((s) => ({
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
