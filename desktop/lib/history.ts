// The one place that turns the engine's messages into the app's lines and copies them into chats.db. Used when a chat is
// opened (app/api/local/sessions) and when a project is exported (app/api/projects/export), so a chat that was imported
// before the local store existed — or that the engine still holds but chats.db has not seen — exports its text too.
import { clientFor, engineStatus } from "./engine"
import { appendMessage, ensureSession, getMessages } from "./localdb"

export type Line = { who: "user" | "assistant"; text: string; at?: number }

/** The engine's conversation, folded the way the app shows it: consecutive assistant steps become one answer. */
export async function engineLines(client: any, id: string): Promise<Line[]> {
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
  return lines
}

/**
 * A chat's lines from the app's own store, backfilled from the engine once when the store has none for it. A session
 * that already has local messages is never re-read from the engine. Never throws: a backfill that fails leaves the
 * (possibly empty) local copy, which is still the durable one.
 */
export async function localLines(id: string, directory: string | null, title?: string | null): Promise<Line[]> {
  let messages = getMessages(id)
  if (!messages.length) {
    const e = engineStatus()
    if (e) {
      try {
        const client = await clientFor(e)
        const lines = await engineLines(client, id)
        if (lines.length) {
          ensureSession({ id, engineId: id, source: "kell", title: title ?? undefined, directory })
          for (const l of lines) appendMessage(id, { role: l.who, text: l.text, at: l.at })
          messages = getMessages(id)
        }
      } catch { /* the local copy is still valid; the engine was only a backfill source */ }
    }
  }
  return messages.map((m) => ({ who: m.role === "assistant" ? "assistant" as const : "user" as const, text: m.text, at: m.created_at }))
}
