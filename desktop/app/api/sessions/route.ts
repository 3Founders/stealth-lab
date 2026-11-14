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
const KELL_SESSIONS_MAX = 500
function rememberKellSession(id: string, ok: boolean): boolean {
  kellSessions.set(id, ok)
  if (kellSessions.size > KELL_SESSIONS_MAX) {
    const oldest = kellSessions.keys().next().value
    if (oldest !== undefined) kellSessions.delete(oldest)
  }
  return ok
}
async function isKellSession(client: any, id: string, metadata: any): Promise<boolean> {
  if (metadata?.kell === true) return true
  const known = kellSessions.get(id)
  if (known !== undefined) return known
  try {
    const entries = await client.experimental.session.instructions.entry.list({ sessionID: id })
    const ok = Array.isArray(entries) && entries.some((e: any) => e?.key === "kell-tools")
    return rememberKellSession(id, ok)
  } catch {
    return rememberKellSession(id, false)
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
      const wantOpenCode = q.get("opencode") === "1"
      const out = await client.session.list({ directory: e.directory as any, limit: wantOpenCode ? 100 : 50, order: "desc" })
      const rows = (out.data as any[]).filter((s) => !s.parentID)
      const flags = await Promise.all(rows.map((s) => isKellSession(client, String(s.id), s.metadata)))
      const sessions = rows.filter((_, i) => (wantOpenCode ? !flags[i] : flags[i])).map((s) => ({
        id: String(s.id), title: typeof s.title === "string" ? s.title : "", updated: Number(s.time?.updated ?? s.time?.created ?? 0),
      }))
      return Response.json({ sessions })
    }
    // The engine gives at most 200 messages a call: the newest 200, put back in order (a long session shows its latest part).
    const out = await client.message.list({ sessionID: id as any, limit: 200, order: "desc" })
    const lines: Line[] = []
    // The subagents this session ran (the engine's own `subagent` tool). The chat history keeps text, so the agents
    // panel would be empty after a restart without this: the tool calls are in the engine's own record (2026-11-14).
    const subagents: { id: string; title: string; state: "running" | "done" | "failed"; brief?: string; result?: string; at?: number }[] = []
    for (const m of [...(out.data as any[])].reverse()) {
      const at = Number(m.time?.created) || undefined
      if (m.type === "user" || (m.type === undefined && typeof m.text === "string")) {
        if (String(m.text || "").trim()) lines.push({ who: "user", text: String(m.text), at })
      } else if (m.type === "assistant") {
        const content = Array.isArray(m.content) ? m.content : []
        // The subagent tool calls first: a step that only called one has no text, and it still belongs in the panel.
        for (const c of content) {
          if (c?.type !== "tool" || !/^(subagent|task)$/i.test(String(c.name || ""))) continue
          const st = c.state && typeof c.state === "object" ? c.state : {}
          const input = st.input && typeof st.input === "object" ? st.input : {}
          const answered = (Array.isArray(st.content) ? st.content : [])
            .filter((x: any) => x?.type === "text").map((x: any) => String(x.text || "")).join("\n\n").trim()
          // The engine wraps a subagent's answer in <subagent sessionID=... state=...>: show the answer, not the tag.
          const result = answered.replace(/^<subagent[^>]*>\s*/i, "").trim()
          const status = String(st.status || "")
          subagents.push({
            id: String(c.id || ""),
            title: String(input.description || input.subagent_type || input.agent || "Subagent").slice(0, 120),
            state: status === "completed" ? "done" : status === "error" ? "failed" : "running",
            ...(typeof input.prompt === "string" ? { brief: input.prompt } : {}),
            ...(result ? { result } : {}),
            ...(at ? { at } : {}),
          })
        }
        const text = content.filter((c: any) => c?.type === "text").map((c: any) => String(c.text || "")).join("")
        if (!text.trim()) continue
        const last = lines.at(-1)
        if (last?.who === "assistant") last.text += `\n\n${text}`     // one answer over several steps reads as one message
        else lines.push({ who: "assistant", text, at })
      }
    }
    return Response.json({ lines, subagents })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), sessions: [], lines: [] }, { status: 502 })
  }
}
