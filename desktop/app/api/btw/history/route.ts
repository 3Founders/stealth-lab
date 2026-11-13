// The /btw side conversations, kept in the app's own store (meta, not the session) so they outlive a restart without
// ever becoming part of the chat. One JSON array under "btw.history":
//
//   GET    /api/btw/history              every stored thread, newest first
//   POST   /api/btw/history { thread }   insert or replace one thread (keyed by id)
//   DELETE /api/btw/history?id=<id>      forget one thread
//   DELETE /api/btw/history?all=1        forget all of them
//
// The client filters the list to the open chat; the store keeps the session id on each thread for that.
import { getMeta, setMeta } from "@/lib/localdb"

export const dynamic = "force-dynamic"

const KEY = "btw.history"
const MAX_THREADS = 100
const MAX_TURNS = 40
const MAX_QUESTION = 4_000
const MAX_ANSWER = 24_000
const MAX_SESSION = 128
const MAX_ID = 64

type StoredTurn = { q: string; a: string; at: number }
type StoredThread = { id: string; sessionID: string | null; at: number; updated: number; turns: StoredTurn[] }

const text = (v: unknown, max: number) => String(v ?? "").slice(0, max)
const time = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : Date.now())
const id = (v: unknown) => text(v, MAX_ID).replace(/[^\w.-]/g, "")

function read(): StoredThread[] {
  try {
    const rows = JSON.parse(getMeta(KEY) || "[]")
    if (!Array.isArray(rows)) return []
    return rows.flatMap((r: any) => {
      if (!r || typeof r.id !== "string" || !Array.isArray(r.turns)) return []
      const turns = r.turns.slice(-MAX_TURNS).flatMap((t: any) => {
        const q = text(t?.q, MAX_QUESTION).trim(), a = text(t?.a, MAX_ANSWER).trim()
        return q && a ? [{ q, a, at: time(t?.at) }] : []
      })
      if (!turns.length) return []
      return [{
        id: id(r.id), sessionID: typeof r.sessionID === "string" ? text(r.sessionID, MAX_SESSION) : null,
        at: time(r.at), updated: time(r.updated), turns,
      }]
    })
  } catch { return [] }
}

function write(rows: StoredThread[]) {
  setMeta(KEY, JSON.stringify(rows.slice(0, MAX_THREADS)))
}

export async function GET() {
  return Response.json({ threads: read() }, { headers: { "cache-control": "no-store" } })
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "Bad request." }, { status: 400 }) }
  const t = body?.thread
  const threadID = id(t?.id)
  if (!threadID) return Response.json({ error: "A thread id is required." }, { status: 400 })
  const turns: StoredTurn[] = Array.isArray(t?.turns)
    ? t.turns.slice(-MAX_TURNS).flatMap((turn: any) => {
        const q = text(turn?.q, MAX_QUESTION).trim(), a = text(turn?.a, MAX_ANSWER).trim()
        return q && a ? [{ q, a, at: time(turn?.at) }] : []
      })
    : []
  if (!turns.length) return Response.json({ error: "A thread needs at least one answered question." }, { status: 400 })
  const thread: StoredThread = {
    id: threadID,
    sessionID: typeof t?.sessionID === "string" ? text(t.sessionID, MAX_SESSION) : null,
    at: time(t?.at), updated: time(t?.updated), turns,
  }
  write([thread, ...read().filter((row) => row.id !== thread.id)])
  return Response.json({ ok: true, thread })
}

export async function DELETE(req: Request) {
  const q = new URL(req.url).searchParams
  if (q.get("all")) { write([]); return Response.json({ ok: true }) }
  const threadID = id(q.get("id"))
  if (!threadID) return Response.json({ error: "A thread id is required." }, { status: 400 })
  write(read().filter((row) => row.id !== threadID))
  return Response.json({ ok: true })
}
