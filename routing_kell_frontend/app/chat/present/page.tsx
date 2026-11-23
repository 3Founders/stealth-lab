"use client"
/**
 * A Way running in a room, as a presentation (docs/blinkit/presentation_rooms.md).
 *
 * Layout, as asked for:
 *
 *   left   the room list, MINIMISED by default — a narrow rail you can open. The main pane is the procedure.
 *   middle the run: inputs first, then step N of M. One thing at a time.
 *   right  other Ways, and a labelled slot for advertising. Never a fake ad: a placeholder that says what it is.
 */
import Link from "next/link"
import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"

import PresentRun from "@/components/chat/PresentRun"
import { MCP_ORIGIN } from "@/lib/connector"
import { getAccessToken } from "@/lib/session"
import "./present.css"

type Room = { id: string; title: string; display_name?: string | null }
type Related = { id: string; goal_name: string; price_paise?: number | null }

export default function PresentPage() {
  const params = useSearchParams()
  const roomId = String(params.get("room") || "")
  const wayId = String(params.get("way") || "")
  const [rail, setRail] = useState(false)
  const [rooms, setRooms] = useState<Room[]>([])
  const [related, setRelated] = useState<Related[]>([])

  useEffect(() => {
    void (async () => {
      try {
        const token = await getAccessToken()
        const res = await fetch(`${MCP_ORIGIN}/v1/rooms`, {
          headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          cache: "no-store",
        })
        if (!res.ok) return
        const body = await res.json()
        setRooms((body?.rooms || body || []) as Room[])
      } catch { /* the rail is navigation, not the run: its absence must not block the procedure */ }
    })()
  }, [])

  useEffect(() => {
    if (!wayId) return
    void (async () => {
      try {
        const token = await getAccessToken()
        const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(wayId)}/related`, {
          headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          cache: "no-store",
        })
        if (!res.ok) return
        const body = await res.json()
        setRelated([...(body?.similar || []), ...(body?.follows || [])])
      } catch { /* an empty rail, not a broken page */ }
    })()
  }, [wayId])

  if (!roomId || !wayId) {
    return (
      <main className="present-shell">
        <p className="pr-muted">This page needs a room and a Way. Open one from the Way you bought.</p>
      </main>
    )
  }

  return (
    <div className="present-shell" data-rail={rail ? "open" : "closed"}>
      {/* Left: minimised by default, because the procedure is what the person came for. */}
      <aside className="present-left" aria-label="Rooms">
        <button
          type="button"
          className="present-toggle"
          onClick={() => setRail((r) => !r)}
          aria-expanded={rail}
          aria-label={rail ? "Minimise rooms" : "Open rooms"}
        >
          {rail ? "⟨" : "⟩"}
        </button>
        {rail && (
          <nav className="present-rooms">
            <p className="present-rail-h">Rooms</p>
            <ul>
              {rooms.map((r) => (
                <li key={r.id}>
                  <Link href={`/chat?room=${encodeURIComponent(r.id)}`} data-current={r.id === roomId || undefined}>
                    {r.display_name || r.title}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </aside>

      <main className="present-main">
        <PresentRun roomId={roomId} wayId={wayId} />
      </main>

      {/* Right: other Ways, and an honest advertising slot rather than an invented ad. */}
      <aside className="present-right" aria-label="More Ways">
        <section className="present-card">
          <h2>More like this</h2>
          {related.length ? (
            <ul>
              {related.slice(0, 5).map((r) => (
                <li key={r.id}>
                  <Link href={`/way/${encodeURIComponent(r.id)}`}>{r.goal_name}</Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pr-muted">No other Ways for this goal yet.</p>
          )}
        </section>

        <section className="present-card present-ad">
          <p className="present-ad-h">Advertise your product or procedure</p>
          <p className="pr-muted">
            This space is for a sponsored slot. It is not for sale yet, so it says so instead of showing something.
          </p>
          <Link className="present-ad-link" href="/submit">Submit a Way</Link>
        </section>
      </aside>
    </div>
  )
}
