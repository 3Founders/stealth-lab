// One queue for everything waiting on the person (docs/desktop_requirements_2026-11.md, P1 #7, 2026-11-17): permission asks from any
// chat (not only the one on screen), scheduled runs that need them or failed, and chats that stopped with a question. A single
// subscription to the engine's events keeps the open permission asks; it is restarted when the engine changes (another folder).
import { clientFor, engineStatus } from "./engine"
import { getSession } from "./localdb"
import { listSchedules } from "./schedules"

export type Ask = { id: string; sessionID: string; what: string; at: number; title: string }
type InboxState = { base: string | null; asks: Map<string, Ask>; stop?: () => void }
type G = typeof globalThis & { __kellInbox?: InboxState }
const g = globalThis as G
const state: InboxState = g.__kellInbox || (g.__kellInbox = { base: null, asks: new Map() })

function watch() {
  const e = engineStatus()
  if (!e) { state.base = null; state.asks.clear(); return }
  if (state.base === e.baseUrl) return
  state.stop?.()
  state.base = e.baseUrl
  state.asks.clear()
  let stopped = false
  state.stop = () => { stopped = true }
  void (async () => {
    try {
      const client: any = await clientFor(e)
      for await (const ev of client.event.subscribe()) {
        if (stopped) break
        const k = (ev as any).type as string
        const d = (ev as any).data ?? {}
        if (k === "permission.asked" && d.id) {
          const what = String(d.message || [d.action, ...(Array.isArray(d.resources) ? d.resources.slice(0, 3) : [])].filter(Boolean).join(" ") || "a tool call")
          state.asks.set(String(d.id), { id: String(d.id), sessionID: String(d.sessionID || ""), what: what.slice(0, 400), at: Date.now(),
            title: getSession(String(d.sessionID || ""))?.title || "a chat" })
        } else if (k === "permission.replied") {
          state.asks.delete(String(d.requestID ?? d.id ?? ""))
        } else if ((k === "session.execution.succeeded" || k === "session.execution.failed" || k === "session.execution.interrupted") && d.sessionID) {
          for (const [id, a] of state.asks) if (a.sessionID === d.sessionID) state.asks.delete(id)
        }
      }
    } catch { /* the next read restarts it */ }
    if (!stopped && state.base === e.baseUrl) state.base = null
  })()
}

export type InboxItem =
  | { kind: "permission"; id: string; sessionID: string; title: string; what: string; at: number }
  | { kind: "schedule"; id: string; scheduleID: string; title: string; status: "needs_you" | "failed" | "missed"; detail: string; sessionID?: string; at: number }

export function inbox(): InboxItem[] {
  watch()
  const items: InboxItem[] = [...state.asks.values()].map((a) => ({ kind: "permission", id: a.id, sessionID: a.sessionID, title: a.title, what: a.what, at: a.at }))
  for (const s of listSchedules()) {
    const r = s.runs[0]
    if (!r || r.seen || !["needs_you", "failed", "missed"].includes(r.status)) continue
    if (r.status === "failed" && s.enabled && s.nextAt && s.nextAt - Date.now() < 6 * 60_000) continue   // a retry is coming
    items.push({ kind: "schedule", id: `${s.id}:${r.at}`, scheduleID: s.id, title: s.title, status: r.status as "needs_you" | "failed" | "missed",
      detail: r.error || (r.status === "needs_you" ? "waiting for your permission" : ""), sessionID: r.sessionID, at: r.at })
  }
  return items.sort((a, b) => b.at - a.at)
}
