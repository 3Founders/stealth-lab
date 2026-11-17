"use client"
// The inbox (one queue of what waits on the person, lib/inbox.ts) and scheduled tasks (lib/schedules.ts), as two panels opened from the
// sidebar's foot (docs/desktop_requirements_2026-11.md, P1 #6 and #7, 2026-11-17).
import { useEffect, useState } from "react"

type Run = { at: number; status: "running" | "needs_you" | "done" | "failed" | "missed"; sessionID?: string; error?: string; summary?: string; attempt?: number }
export type Schedule = { id: string; title: string; prompt: string; directory: string; repeat: "once" | "hourly" | "daily" | "weekdays" | "weekly"
  time: string; day?: number; everyHours?: number; enabled: boolean; nextAt: number | null; runs: Run[] }
export type InboxItem =
  | { kind: "permission"; id: string; sessionID: string; title: string; what: string; at: number }
  | { kind: "schedule"; id: string; scheduleID: string; title: string; status: "needs_you" | "failed" | "missed"; detail: string; sessionID?: string; at: number }

const when = (t: number | null | undefined) => (t ? new Date(t).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—")
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
const repeatText = (s: Pick<Schedule, "repeat" | "time" | "day" | "everyHours">) =>
  s.repeat === "hourly" ? `every ${s.everyHours || 1} hour${(s.everyHours || 1) > 1 ? "s" : ""}`
    : s.repeat === "daily" ? `every day at ${s.time}` : s.repeat === "weekdays" ? `weekdays at ${s.time}`
      : s.repeat === "weekly" ? `every ${DAYS[s.day ?? 1]} at ${s.time}` : `once at ${s.time}`
const STATUS: Record<Run["status"], string> = { running: "running", needs_you: "needs you", done: "done", failed: "failed", missed: "missed" }

function Shell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => { const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k) }, [onClose])
  return (
    <div className="dk-modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="dk-modal" role="dialog" aria-label={title}>
        <header><h2>{title}</h2><button type="button" className="dk-icon dk-icon-sm" aria-label="Close" onClick={onClose}>×</button></header>
        <div className="dk-modal-body">{children}</div>
      </div>
    </div>
  )
}

export function InboxPanel({ items, onClose, onOpenChat, onAnswered }: {
  items: InboxItem[]; onClose: () => void; onOpenChat: (sessionID: string) => void; onAnswered: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const answer = async (it: Extract<InboxItem, { kind: "permission" }>, decision: "once" | "always" | "reject") => {
    setBusy(it.id)
    await fetch("/api/permission", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: it.sessionID, requestID: it.id, decision }) }).catch(() => null)
    setBusy(null); onAnswered()
  }
  const sched = async (action: "run" | "seen", it: Extract<InboxItem, { kind: "schedule" }>) => {
    setBusy(it.id)
    await fetch("/api/schedules", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, id: it.scheduleID, at: it.at }) }).catch(() => null)
    setBusy(null); onAnswered()
  }
  return (
    <Shell title="Waiting on you" onClose={onClose}>
      {items.length === 0 ? <p className="dk-note">Nothing is waiting on you. Permission asks from any chat, and scheduled tasks that need you or failed, show here.</p> : (
        <ul className="dk-inbox">
          {items.map((it) => (
            <li key={it.id}>
              {it.kind === "permission" ? (
                <>
                  <div className="dk-inbox-h"><b>Permission</b> · <button type="button" className="dk-linkish" onClick={() => onOpenChat(it.sessionID)}>{it.title}</button><time>{when(it.at)}</time></div>
                  <p className="mono">{it.what}</p>
                  <div className="dk-row">
                    <button type="button" className="btn-ink dg-mini" disabled={busy === it.id} onClick={() => void answer(it, "once")}>Allow once</button>
                    <button type="button" className="btn-line dg-mini" disabled={busy === it.id} onClick={() => void answer(it, "always")}>Always allow</button>
                    <button type="button" className="btn-line dg-mini" disabled={busy === it.id} onClick={() => void answer(it, "reject")}>Deny</button>
                  </div>
                </>
              ) : (
                <>
                  <div className="dk-inbox-h"><b>Scheduled · {STATUS[it.status]}</b> · {it.title}<time>{when(it.at)}</time></div>
                  {it.detail && <p>{it.detail}</p>}
                  <div className="dk-row">
                    {it.sessionID && <button type="button" className="btn-line dg-mini" onClick={() => onOpenChat(it.sessionID!)}>Open the run</button>}
                    {it.status !== "needs_you" && <button type="button" className="btn-ink dg-mini" disabled={busy === it.id} onClick={() => void sched("run", it)}>Run again</button>}
                    {it.status !== "needs_you" && <button type="button" className="btn-line dg-mini" disabled={busy === it.id} onClick={() => void sched("seen", it)}>Dismiss</button>}
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </Shell>
  )
}

export function SchedulesPanel({ onClose, onOpenChat, homeDir, projectDir }: {
  onClose: () => void; onOpenChat: (sessionID: string) => void; homeDir: string; projectDir: string
}) {
  const [list, setList] = useState<Schedule[]>([])
  const [running, setRunning] = useState<string | null>(null)
  const [edit, setEdit] = useState<Partial<Schedule> | null>(null)
  const [err, setErr] = useState("")
  const load = () => fetch("/api/schedules").then((r) => r.json()).then((j) => { setList(j.schedules || []); setRunning(j.running || null) }).catch(() => undefined)
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 5000); return () => window.clearInterval(t) }, [])
  const act = async (action: string, id: string) => {
    await fetch("/api/schedules", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, id }) }).catch(() => null)
    void load()
  }
  const save = async () => {
    setErr("")
    const r = await fetch("/api/schedules", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "save", ...edit }) })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) { setErr(j.error || "Couldn't save it"); return }
    setEdit(null); void load()
  }
  const folders = [{ v: homeDir, label: "Chats (no project)" }, ...(projectDir && projectDir !== homeDir ? [{ v: projectDir, label: projectDir.split(/[\\/]/).pop() || projectDir }] : [])]
  return (
    <Shell title="Scheduled tasks" onClose={onClose}>
      {edit ? (
        <form className="dk-sched-form" onSubmit={(e) => { e.preventDefault(); void save() }}>
          <label>Name<input value={edit.title || ""} onChange={(e) => setEdit({ ...edit, title: e.target.value })} placeholder="Morning news digest" /></label>
          <label>What kell should do<textarea rows={4} value={edit.prompt || ""} onChange={(e) => setEdit({ ...edit, prompt: e.target.value })}
            placeholder="Every morning: find the top 5 AI news stories from the last 24 hours and summarise each in two lines with links." /></label>
          <div className="dk-sched-when">
            <label>Repeat
              <select value={edit.repeat || "daily"} onChange={(e) => setEdit({ ...edit, repeat: e.target.value as Schedule["repeat"] })}>
                <option value="daily">Every day</option><option value="weekdays">Weekdays</option><option value="weekly">Every week</option>
                <option value="hourly">Every few hours</option><option value="once">Once</option>
              </select>
            </label>
            {edit.repeat === "hourly" ? (
              <label>Every<select value={edit.everyHours || 1} onChange={(e) => setEdit({ ...edit, everyHours: Number(e.target.value) })}>
                {[1, 2, 3, 4, 6, 8, 12].map((h) => <option key={h} value={h}>{h} hour{h > 1 ? "s" : ""}</option>)}</select></label>
            ) : (
              <label>At<input type="time" value={edit.time || "09:00"} onChange={(e) => setEdit({ ...edit, time: e.target.value })} /></label>
            )}
            {edit.repeat === "weekly" && (
              <label>On<select value={edit.day ?? 1} onChange={(e) => setEdit({ ...edit, day: Number(e.target.value) })}>
                {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label>
            )}
            <label>In<select value={edit.directory || homeDir} onChange={(e) => setEdit({ ...edit, directory: e.target.value })}>
              {folders.map((f) => <option key={f.v} value={f.v}>{f.label}</option>)}</select></label>
          </div>
          {err && <p className="dk-err" role="alert">{err}</p>}
          <p className="dk-note">Runs while kell is open, including when its window is closed to the tray. Each run is its own chat; you're told when it finishes or needs you.</p>
          <div className="dk-row">
            <button type="submit" className="btn-ink" disabled={!String(edit.prompt || "").trim()}>Save</button>
            <button type="button" className="btn-line" onClick={() => setEdit(null)}>Cancel</button>
          </div>
        </form>
      ) : (
        <>
          <div className="dk-row" style={{ marginTop: 0 }}>
            <button type="button" className="btn-ink" onClick={() => setEdit({ repeat: "daily", time: "09:00", directory: homeDir })}>New scheduled task</button>
          </div>
          {list.length === 0 ? <p className="dk-note">Nothing scheduled yet. A daily digest, a weekly report or a page to check every few hours are good first ones.</p> : (
            <ul className="dk-sched">
              {list.map((s) => {
                const last = s.runs[0]
                return (
                  <li key={s.id} data-off={!s.enabled || undefined}>
                    <div className="dk-inbox-h"><b>{s.title}</b><span className="dk-note" style={{ margin: 0 }}>{repeatText(s)}</span></div>
                    <p className="dk-sched-p">{s.prompt}</p>
                    <p className="dk-note" style={{ margin: "2px 0" }}>
                      {running === s.id ? "Running now…" : s.enabled ? `Next: ${when(s.nextAt)}` : "Paused"}
                      {last && <> · Last: {STATUS[last.status]} {when(last.at)}{last.error ? ` — ${last.error}` : ""}</>}
                    </p>
                    <div className="dk-row">
                      <button type="button" className="btn-line dg-mini" disabled={running === s.id} onClick={() => void act("run", s.id)}>Run now</button>
                      {last?.sessionID && <button type="button" className="btn-line dg-mini" onClick={() => onOpenChat(last.sessionID!)}>Open last run</button>}
                      <button type="button" className="btn-line dg-mini" onClick={() => setEdit(s)}>Edit</button>
                      <button type="button" className="btn-line dg-mini" onClick={() => void act(s.enabled ? "pause" : "resume", s.id)}>{s.enabled ? "Pause" : "Resume"}</button>
                      <button type="button" className="btn-line dg-mini dk-danger" onClick={() => void act("delete", s.id)}>Delete</button>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
    </Shell>
  )
}

type Conn = { id: string; name: string; url: string; status: string; error?: string; integrationID?: string }
type CatalogItem = { id: string; name: string; url: string; what: string }
const CONN_STATUS: Record<string, string> = { connected: "Connected", needs_auth: "Sign in needed", pending: "Connecting…", failed: "Failed", disabled: "Off" }

/** The person's work apps, added to the engine in one click (lib/connectors.ts). */
export function ConnectorsPanel({ onClose }: { onClose: () => void }) {
  const [catalog, setCatalog] = useState<CatalogItem[]>([])
  const [list, setList] = useState<Conn[]>([])
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [custom, setCustom] = useState({ name: "", url: "" })
  const load = () => fetch("/api/connectors").then((r) => r.json()).then((j) => { setCatalog(j.catalog || []); setList(j.connectors || []); if (j.error) setNote(j.error) }).catch(() => undefined)
  useEffect(() => { void load() }, [])
  const post = async (body: Record<string, unknown>) => {
    const r = await fetch("/api/connectors", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(j.error || "that did not work")
    return j
  }
  const add = async (id: string, extra?: { name: string; url: string }) => {
    setBusy(id); setNote("")
    try { await post({ action: "add", id, ...(extra || {}) }); await new Promise((r) => setTimeout(r, 1500)); await load() }
    catch (e: any) { setNote(String(e?.message || e)) }
    setBusy(null)
  }
  const signin = async (c: Conn) => {
    setBusy(c.id); setNote("")
    try {
      const j = await post({ action: "signin", id: c.id })
      window.open(j.url, "_blank")
      setNote(`Finish signing in to ${c.name} in your browser…`)
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 2000))
        const s = await post({ action: "status", integrationID: j.integrationID, attemptID: j.attemptID })
        if (s.status === "complete") { setNote(`${c.name} is connected.`); break }
        if (s.status === "failed") { setNote(`${c.name}: ${s.message || "the sign-in failed"}`); break }
      }
    } catch (e: any) { setNote(`${c.name}: ${String(e?.message || e)}`) }
    setBusy(null); void load()
  }
  const have = new Set(list.map((x) => x.id))
  return (
    <Shell title="Connectors" onClose={onClose}>
      <p className="dk-note" style={{ marginTop: 0 }}>Let kell use your work apps. Each signs in on the app&rsquo;s own page in your browser; kell never sees your password. Some apps (Google&rsquo;s, for now) only accept sign-ins from apps they approved, so they may not connect yet.</p>
      {note && <p className="dk-note" role="status">{note}</p>}
      {list.length > 0 && (
        <ul className="dk-inbox">
          {list.map((c) => (
            <li key={c.id}>
              <div className="dk-inbox-h"><b>{c.name}</b><span className="dk-note" style={{ margin: 0 }}>{CONN_STATUS[c.status] || c.status}</span></div>
              {c.error && c.status !== "connected" && <p className="dk-note">{c.error}</p>}
              <div className="dk-row">
                {c.status !== "connected" && c.integrationID && <button type="button" className="btn-ink dg-mini" disabled={busy === c.id} onClick={() => void signin(c)}>Sign in</button>}
                <button type="button" className="btn-line dg-mini" disabled={busy === c.id} onClick={() => void (async () => { await post({ action: "remove", id: c.id }).catch(() => null); void load() })()}>Remove</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <h3 className="dk-sess-group" style={{ margin: "14px 0 6px" }}>Add</h3>
      <div className="dk-conn-grid">
        {catalog.filter((c) => !have.has(c.id)).map((c) => (
          <button key={c.id} type="button" className="dk-conn" disabled={busy === c.id} onClick={() => void add(c.id)} title={c.url}>
            <b>{c.name}</b><span>{c.what}</span>
          </button>
        ))}
      </div>
      <form className="dk-sess-folder" style={{ padding: "10px 0 0" }} onSubmit={(e) => { e.preventDefault(); void add("", custom) }}>
        <input value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} placeholder="Other: name" aria-label="Connector name" />
        <input value={custom.url} onChange={(e) => setCustom({ ...custom, url: e.target.value })} placeholder="https://… MCP address" aria-label="Connector address" style={{ flex: 2 }} />
        <button type="submit" className="btn-line dg-mini" disabled={!custom.name.trim() || !/^https:\/\//.test(custom.url)}>Add</button>
      </form>
    </Shell>
  )
}
