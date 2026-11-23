"use client"
/**
 * A Way's run, as the room's MAIN pane.
 *
 * The room renders this instead of the conversation when the room belongs to a Way. It follows the site's own pages
 * rather than inventing a look: the same font, the same tokens (--ink, --grey, --rule-strong, --surface), the same
 * measured left-aligned column as /way (app/way/way.css). An earlier version centred everything and set its own
 * sizes, which read as a different product (owner, 2026-11-23: "this is bad UI, take a look at other pages we've
 * built ... use the same font").
 *
 * What it shows, in order: what the Way is, where it has got to, the steps (bold titles, state per step), what it
 * needs from a person when it pauses, and the files it produced -- each of which can be read in place or downloaded.
 */
import { useCallback, useEffect, useRef, useState } from "react"

import { MCP_ORIGIN } from "@/lib/connector"
import { getAccessToken } from "@/lib/session"
import { downloadArtifact } from "@/lib/api"

type InputDef = { step: number; step_title: string; key: string; label: string; required: boolean; help: string }
type StepDef = { order: number; title: string; price_low_paise: number; price_high_paise: number; is_human?: boolean }
type RunState = {
  state:
    | "needs_inputs"
    | "ready"
    | "running"
    | "waiting_human"   // a step needs a person: the run is paused on it, not finished
    | "waiting_session"
    | "done"
    | "failed"
    | "no_way"
    | "cancelled"
  run: { run_id: string; status: string } | null
  inputs: InputDef[]
  submission_id?: string
  waiting?: { kind?: string; order?: number; title?: string; description?: string } | null
}
type Output = { order: number; title: string; output: string }
type FileOut = { name: string; size_bytes?: number; media_type?: string; text?: boolean; url: string }
type Refused = { name: string; why: string }

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const token = await getAccessToken()
  const headers: Record<string, string> = { Accept: "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
  if (init?.body !== undefined) headers["Content-Type"] = "application/json"
  const res = await fetch(`${MCP_ORIGIN}${path}`, {
    method: init?.method || "GET",
    headers,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.error?.message || body?.error || `the server answered ${res.status}`))
  return body as T
}

/** A file's text, fetched through the same licence-checked route the download uses. */
async function fetchText(url: string): Promise<string> {
  const token = await getAccessToken()
  const target = url.startsWith("http") ? url : `${MCP_ORIGIN}${url}`
  const res = await fetch(target, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    redirect: "follow",
    cache: "no-store",
  })
  if (!res.ok) throw new Error(`the file could not be read (${res.status})`)
  return await res.text()
}

function kb(n?: number): string {
  if (n == null) return ""
  return n < 1024 ? `${n} B` : `${Math.max(1, Math.round(n / 1024))} KB`
}

export default function WayRunPanel({
  roomId,
  wayId,
  title,
  onNothing,
  onShowChat,
}: {
  roomId?: string
  wayId?: string
  title?: string
  onNothing?: () => void
  /** Back to the conversation in this same room. A Way room hides the chat, so it needs a way back. */
  onShowChat?: () => void
}) {
  const [view, setView] = useState<RunState | null>(null)
  const [steps, setSteps] = useState<StepDef[]>([])
  const [outputs, setOutputs] = useState<Output[]>([])
  const [files, setFiles] = useState<FileOut[]>([])
  const [refused, setRefused] = useState<Refused[]>([])
  const [downloadAllUrl, setDownloadAllUrl] = useState("")
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState("")
  /** The file open for reading: its name, and its text once it has arrived. */
  const [openFile, setOpenFile] = useState<string | null>(null)
  const [openText, setOpenText] = useState<string>("")
  const [openErr, setOpenErr] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wayForRun = view?.submission_id || wayId || ""

  const load = useCallback(async () => {
    if (!roomId) return
    try {
      const r = await call<RunState>(`/v1/rooms/${encodeURIComponent(roomId)}/run`)
      setView(r)
      if (r.run?.run_id) {
        const full = await call<{
          outputs?: Output[]
          artifacts?: FileOut[]
          artifacts_refused?: Refused[]
          download_all?: string
        }>(`/v1/goals/runs/${encodeURIComponent(r.run.run_id)}`)
        setOutputs(full.outputs || [])
        setFiles(full.artifacts || [])
        setRefused(full.artifacts_refused || [])
        setDownloadAllUrl(full.download_all || "")
      }
      if (r.submission_id) {
        void call<{ steps: StepDef[] }>(`/v1/goals/submissions/${encodeURIComponent(r.submission_id)}/steps/cost`)
          .then((c) => setSteps(c.steps || []))
          .catch(() => { /* information only */ })
      }
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }, [roomId])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (view?.state !== "running") return
    timer.current = setTimeout(() => void load(), 3000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [view, load])

  const noWay = view?.state === "no_way"
  useEffect(() => { if (noWay) onNothing?.() }, [noWay, onNothing])

  async function start() {
    if (!roomId) return
    if (!wayForRun) { setError("this room does not name the Way it should run — open it from the Way's page"); return }
    setBusy(true); setError(null)
    try {
      await call(`/v1/rooms/${encodeURIComponent(roomId)}/run/start`, {
        method: "POST", body: { submission_id: wayForRun, values },
      })
      await load()
    } catch (e) { setError(String((e as Error)?.message || e)) } finally { setBusy(false) }
  }

  async function resume() {
    if (!roomId) return
    setBusy(true); setError(null)
    try {
      await call(`/v1/rooms/${encodeURIComponent(roomId)}/run/resume`, { method: "POST", body: { note } })
      setNote("")
      await load()
    } catch (e) { setError(String((e as Error)?.message || e)) } finally { setBusy(false) }
  }

  async function viewFile(f: FileOut) {
    if (openFile === f.name) { setOpenFile(null); return }
    setOpenFile(f.name); setOpenText(""); setOpenErr(null)
    try {
      // `?mode=view` answers with the bytes inline. The download route redirects to a presigned storage URL, which
      // fetch() cannot read cross-origin (no CORS headers from the bucket), so View would fail without this.
      setOpenText(await fetchText(`${f.url}${f.url.includes("?") ? "&" : "?"}mode=view`))
    } catch (e) {
      setOpenErr(String((e as Error)?.message || e))
    }
  }

  async function downloadOne(f: FileOut) {
    setError(null)
    try { await downloadArtifact(f.url, f.name) } catch (e) { setError(String((e as Error)?.message || e)) }
  }

  async function downloadZip() {
    if (!downloadAllUrl) return
    setBusy(true); setError(null)
    try { await downloadArtifact(downloadAllUrl, "way-output.zip") } catch (e) { setError(String((e as Error)?.message || e)) }
    finally { setBusy(false) }
  }

  const done = outputs.length
  const total = steps.length || 0
  const pct = total ? Math.round((done / total) * 100) : 0
  const running = view?.state === "running"
  const paused = view?.state === "waiting_human" || view?.state === "waiting_session"
  const finished = view?.state === "done" || view?.state === "failed"

  const stateOf = (order: number) => {
    if (outputs.some((o) => o.order === order)) return "done"
    if (running && steps[done]?.order === order) return "running"
    if (paused && view?.waiting?.order === order) return "review"
    return "waiting"
  }

  return (
    <section className="dg-way-main" aria-label={title ? `${title}: steps and run` : "Way run"}>
      {/* No headline: the room's own header already names it (owner, 2026-11-23). The progress line stays, because
          it is the one thing the header does not say. */}
      <header className="dg-way-head">
        <p className="dg-way-sub">
          {total > 0 ? `${done} of ${total} steps done` : "Reading this Way…"}
          {running ? " · running" : paused ? " · waiting on you" : finished ? (view?.state === "done" ? " · finished" : " · stopped") : ""}
        </p>
        {/* A Way room replaces the conversation, so there has to be an obvious way back to chatting in it. */}
        <button type="button" className="dg-way-swap" onClick={() => onShowChat?.()}>
          Open the chat in this room
        </button>
        {total > 0 && (
          <div className="dg-way-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ transform: `scaleX(${pct / 100})` }} />
          </div>
        )}
      </header>

      {view?.state === "needs_inputs" && (
        <section className="dg-way-block">
          <h2>What it needs from you</h2>
          <ul className="dg-way-inputs">
            {view.inputs.map((i) => (
              <li key={`${i.step}.${i.key}`}>
                <label htmlFor={`wi-${i.step}-${i.key}`}>
                  {i.label}<span className="dg-way-stepof">step {i.step}</span>
                </label>
                <input
                  id={`wi-${i.step}-${i.key}`}
                  value={values[`${i.step}.${i.key}`] || ""}
                  onChange={(e) => setValues((v) => ({ ...v, [`${i.step}.${i.key}`]: e.target.value }))}
                  required={i.required}
                />
                {i.help && <span className="dg-way-note">{i.help}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {total > 0 && (
        <section className="dg-way-block">
          <h2>Steps</h2>
          <ol className="dg-way-steps">
            {steps.map((s) => {
              const st = stateOf(s.order)
              const wrote = outputs.find((o) => o.order === s.order)
              return (
                <li key={s.order} data-state={st} data-human={s.is_human ? "true" : "false"}>
                  <span className="dg-way-n" aria-hidden="true">{st === "done" ? "✓" : s.order}</span>
                  <span className="dg-way-col">
                    <span className="dg-way-title">{s.title}</span>
                    {wrote?.output && <span className="dg-way-out-step">{wrote.output}</span>}
                  </span>
                  {s.is_human && <span className="dg-way-kind">you</span>}
                  {st === "review" && <span className="dg-way-kind" data-tone="review">waiting</span>}
                </li>
              )
            })}
          </ol>
        </section>
      )}

      {paused && view?.waiting && (
        <section className="dg-way-pause">
          <h2>Waiting on you — step {view.waiting.order}</h2>
          <p className="dg-way-pause-title">{view.waiting.title}</p>
          {view.waiting.description && <p className="dg-way-note">{view.waiting.description}</p>}
          <form className="dg-way-pause-form" onSubmit={(e) => { e.preventDefault(); void resume() }}>
            <label htmlFor="dg-way-note">What you did or found</label>
            <input id="dg-way-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="A line or two is enough" />
            <button type="submit" className="dg-way-start" disabled={busy}>{busy ? "Continuing…" : "Continue"}</button>
          </form>
        </section>
      )}

      {!running && !paused && !finished && (
        <div className="dg-way-act">
          <button type="button" className="dg-way-start" disabled={busy || !view} onClick={() => void start()}>
            {busy ? "Starting…" : view?.state === "needs_inputs" ? "Start with these answers" : "Start"}
          </button>
          <p className="dg-way-note">
            {view ? "A human step pauses the run and waits for you." : "Reading this Way — Start opens when it has loaded."}
          </p>
        </div>
      )}

      {finished && (
        <p className="dg-way-note">
          {view?.state === "done" ? "Finished." : "Stopped with a failure — the steps above show how far it got."}
        </p>
      )}

      {error && <p className="dg-way-error" role="alert">{error}</p>}

      {files.length > 0 && (
        <section className="dg-way-block">
          <h2>Files</h2>
          <ul className="dg-way-files">
            {files.map((f) => {
              const open = openFile === f.name
              return (
                <li key={f.name} data-open={open ? "true" : "false"}>
                  <div className="dg-way-filerow">
                    <span className="dg-way-fname">{f.name}</span>
                    <span className="dg-way-fsize">{kb(f.size_bytes)}</span>
                    <span className="dg-way-facts">
                      <button type="button" onClick={() => void viewFile(f)} aria-expanded={open}>
                        {open ? "Hide" : "View"}
                      </button>
                      <button type="button" onClick={() => void downloadOne(f)}>Download</button>
                    </span>
                  </div>
                  {open && (
                    <div className="dg-way-fileview">
                      {openErr ? <p className="dg-way-error">{openErr}</p>
                        : openText ? <pre>{openText}</pre>
                        : <p className="dg-way-note">Reading…</p>}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
          {files.length > 1 && (
            <p><button type="button" className="dg-way-all" disabled={busy} onClick={() => void downloadZip()}>
              Download all ({files.length})
            </button></p>
          )}
        </section>
      )}

      {refused.length > 0 && (
        <section className="dg-way-block">
          <h2>Not kept</h2>
          <ul className="dg-way-refused">
            {refused.map((r) => (
              <li key={r.name}><span className="dg-way-fname">{r.name}</span><span className="dg-way-fsize">{r.why}</span></li>
            ))}
          </ul>
        </section>
      )}
    </section>
  )
}
