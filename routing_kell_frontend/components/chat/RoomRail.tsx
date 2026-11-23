"use client"
/**
 * The room's right rail: the run's output, other Ways for this goal, and an honest advertising slot.
 *
 * Lifted from the deleted `/chat/present` page (owner, 2026-11-23: "this feels like a totally new page; add the
 * right-panel components in the right sidebar in chats, not another endpoint"). The room is the one place a Way
 * runs, so its rail lives with it.
 *
 * Both lists are discovery only. "More like this" is the same-goal / same-author approximation the API returns
 * (`submissions/api.py::related_ways`); there is no succession edge in the schema, so nothing here claims to be the
 * next step. The ad slot is a labelled placeholder, not a fake sponsor: a page whose claim is evidence cannot afford
 * an invented picture.
 */
import Link from "next/link"
import { useEffect, useState } from "react"

import { MCP_ORIGIN } from "@/lib/connector"
import { getAccessToken } from "@/lib/session"
import { downloadArtifact } from "@/lib/api"

type Related = { id: string; goal_name: string; price_paise?: number | null }
type FileOut = { name: string; size_bytes?: number; url: string }

function rupees(paise?: number | null): string {
  if (paise == null || !Number.isFinite(paise)) return ""
  const r = paise / 100
  return `₹${Number.isInteger(r) ? r.toLocaleString("en-IN") : r.toFixed(2)}`
}

export default function RoomRail({ wayId, roomId }: { wayId?: string; roomId?: string }) {
  const [related, setRelated] = useState<Related[]>([])
  const [loaded, setLoaded] = useState(false)
  const [files, setFiles] = useState<FileOut[]>([])

  // The room's current run, and the files it produced. Fetched here because RoomView tracks room *names*, not the
  // run projection; one read of the room's own endpoint is the least surprising source.
  useEffect(() => {
    if (!roomId) return
    let live = true
    void (async () => {
      try {
        const token = await getAccessToken()
        const headers = { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) }
        const r = await fetch(`${MCP_ORIGIN}/v1/rooms/${encodeURIComponent(roomId)}/run`, { headers, cache: "no-store" })
        if (!r.ok) return
        const body = await r.json()
        const runId = body?.run?.run_id
        if (!runId) { if (live) setFiles([]); return }
        const full = await fetch(`${MCP_ORIGIN}/v1/goals/runs/${encodeURIComponent(runId)}`, { headers, cache: "no-store" })
        if (full.ok) {
          const b = await full.json()
          if (live) setFiles(Array.isArray(b?.artifacts) ? b.artifacts : [])
        }
      } catch { /* no run, no files: not an error */ }
    })()
    return () => { live = false }
  }, [roomId])

  useEffect(() => {
    if (!wayId) { setLoaded(true); return }
    let live = true
    void (async () => {
      try {
        const token = await getAccessToken()
        const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/${encodeURIComponent(wayId)}/related`, {
          headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          cache: "no-store",
        })
        if (res.ok) {
          const body = await res.json()
          if (live) setRelated([...(body?.similar || []), ...(body?.follows || [])])
        }
      } catch {
        /* a rail that cannot read is empty, not broken */
      } finally {
        if (live) setLoaded(true)
      }
    })()
    return () => { live = false }
  }, [wayId])

  return (
    <aside className="dg-rail" aria-label="More Ways">
      {files.length > 0 && (
        <section className="dg-rail-card">
          <h2>Output</h2>
          <ul>
            {files.map((f) => (
              <li key={f.name}>
                <button type="button" className="dg-rail-file" onClick={() => void downloadArtifact(f.url, f.name)}>
                  {f.name}
                </button>
                {f.size_bytes != null && <span className="dg-rail-price">{Math.round(f.size_bytes / 1024)} KB</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="dg-rail-card">
        <h2>More like this</h2>
        {!loaded ? (
          <p className="dg-rail-muted" role="status">Loading…</p>
        ) : related.length ? (
          <ul>
            {related.slice(0, 6).map((r) => (
              <li key={r.id}>
                <Link href={`/way/${encodeURIComponent(r.id)}`}>{r.goal_name}</Link>
                {r.price_paise != null && <span className="dg-rail-price">{rupees(r.price_paise)}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="dg-rail-muted">
            {wayId ? "No other Ways for this goal yet." : "Open a Way to see related ones."}
          </p>
        )}
      </section>

      <section className="dg-rail-card dg-rail-ad">
        <p className="dg-rail-ad-h">Advertise your product or procedure</p>
        <p className="dg-rail-muted">
          This space is for a sponsored slot. It is not for sale yet, so it says so instead of showing something.
        </p>
        <Link className="dg-rail-ad-link" href="/submit">Submit a Way</Link>
      </section>
    </aside>
  )
}
