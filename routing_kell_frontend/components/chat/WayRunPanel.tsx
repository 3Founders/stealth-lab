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
import Markdown from "@/components/chat/Markdown"

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
  /** This buyer's standing on this version: none | paid | used | refunded | nobody (priced, not signed in). */
  purchase?: "none" | "paid" | "used" | "refunded" | "nobody"
  /** Whether a Start would be accepted now. False on a paid Way whose one output was already produced. */
  can_run?: boolean
  /** A finished run is on screen AND another run is available (the re-buyer's case). */
  can_start_again?: boolean
  /** The version's price in paise, or null for a free Way. */
  price_paise?: number | null
}
type Output = { order: number; title: string; output: string; files?: FileOut[] }
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

/**
 * One file, shown in place, fixed and unpannable: an image, a PDF, an HTML page, or text laid out by its own kind.
 *
 * The bytes come from `/v1/goals/runs/<id>/artifacts/<name>?mode=view`, which answers with the file's real media type
 * and `Content-Disposition: inline`. Nothing here is fetched twice: text is fetched by `viewFile` (so it can be shown
 * as markdown or CSV), while images, PDFs and HTML are handed to the browser by URL, which lets it use its own viewer
 * and keeps a paid file out of this component's memory.
 */
function FileViewer({ file, text, error }: { file: FileOut; text: string; error: string | null }) {
  const src = `${file.url}${file.url.includes("?") ? "&" : "?"}mode=view`
  const type = (file.media_type || "").toLowerCase()

  if (error) return <p className="dg-way-viewerr" role="alert">This file could not be read: {error}</p>

  if (type.startsWith("image/")) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img className="dg-way-viewimg" src={src} alt={file.name} />
  }
  if (type === "application/pdf") {
    return <embed className="dg-way-viewpdf" src={src} type="application/pdf" title={file.name} />
  }
  if (type === "text/html") {
    // The same sandbox the room uses for an agent's page: it cannot read this tab, the room or the account.
    return (
      <iframe className="dg-way-viewframe" title={file.name} src={src}
              sandbox="allow-scripts allow-forms allow-modals allow-popups" referrerPolicy="no-referrer" />
    )
  }
  if (!file.text) {
    return (
      <p className="dg-way-note">
        {file.name} cannot be shown in the page. Use Download to open it.
      </p>
    )
  }
  if (!text) return <p className="dg-way-note" aria-busy="true">Reading {file.name}…</p>

  if (type === "text/csv") return <CsvTable text={text} />
  if (type === "application/json") {
    try {
      return <pre className="dg-way-viewsrc"><code>{JSON.stringify(JSON.parse(text), null, 2)}</code></pre>
    } catch {
      return <pre className="dg-way-viewsrc"><code>{text}</code></pre>   // not valid JSON: show it as it came
    }
  }
  if (type === "text/markdown") return <div className="dg-way-slide-out"><Markdown text={text} /></div>
  return <pre className="dg-way-viewsrc"><code>{text}</code></pre>
}

/**
 * A CSV as a table. Deliberately a small reader, not a CSV library: Way outputs are machine-written, so quoted commas
 * and doubled quotes are handled and anything stranger falls back to the raw text rather than guessing.
 */
function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text)
  if (!rows.length) return <pre className="dg-way-viewsrc"><code>{text}</code></pre>
  const [head, ...body] = rows
  return (
    <div className="dg-way-viewtable">
      <table>
        <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>
          {body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  )
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ""
  let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i += 1 }
      else if (ch === '"') quoted = false
      else cell += ch
      continue
    }
    if (ch === '"') { quoted = true; continue }
    if (ch === ",") { row.push(cell); cell = ""; continue }
    if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; continue }
    if (ch !== "\r") cell += ch
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row) }
  return rows.filter((r) => r.some((c) => c.trim().length > 0))
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
  /** "Run again" on a finished room: reveal the inputs (and Start) even though the last run is done. */
  const [showAgain, setShowAgain] = useState(false)
  /** Why the run's outputs could not be read, when they could not. Kept apart from `error` so it is not lost. */
  const [outputsError, setOutputsError] = useState<string | null>(null)
  /** The file open for reading: its name, and its text once it has arrived. */
  const [openFile, setOpenFile] = useState<string | null>(null)
  const [openText, setOpenText] = useState<string>("")
  const [openErr, setOpenErr] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wayForRun = view?.submission_id || wayId || ""

  const load = useCallback(async () => {
    // No server id yet (sync is a few seconds behind a cold load): wait for the next pass rather than drawing a run
    // panel that can never load. The main pane treats a missing id as "not a Way" after a moment, so this cannot
    // leave the person staring at "Reading this Way…" forever.
    if (!roomId) { setView(null); return }
    try {
      const r = await call<RunState>(`/v1/rooms/${encodeURIComponent(roomId)}/run`)
      setView(r)
      if (r.run?.run_id) {
        // Loaded separately from the state, so a failure here cannot hide the run's state too: before, one throw
        // left `outputs` empty and every slide said "No output from this step yet" over a run that had all of them.
        try {
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
          setOutputsError(null)
        } catch (e) {
          setOutputsError(String((e as Error)?.message || e))
        }
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

  // A room the server calls "execution" but whose id has not arrived cannot be read. Give sync a few seconds, then say
  // so rather than hanging: the caller lets go and the conversation returns, which is better than a blank pane.
  const [gaveUp, setGaveUp] = useState(false)
  useEffect(() => {
    if (roomId) { setGaveUp(false); return }
    const t = setTimeout(() => setGaveUp(true), 8000)
    return () => clearTimeout(t)
  }, [roomId])
  useEffect(() => { if (gaveUp) onNothing?.() }, [gaveUp, onNothing])

  useEffect(() => {
    if (view?.state !== "running") return
    timer.current = setTimeout(() => void load(), 3000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [view, load])

  const noWay = view?.state === "no_way"
  useEffect(() => { if (noWay) onNothing?.() }, [noWay, onNothing])
  /** Whether a Start would be accepted. Defaults to true so an older server, or a free Way, keeps working. */
  const canRun = view?.can_run !== false

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
    // Images, PDFs and HTML are handed to the browser by URL, so only text-like files are read into memory here.
    const type = (f.media_type || "").toLowerCase()
    const byUrl = type.startsWith("image/") || type === "application/pdf" || type === "text/html" || !f.text
    if (byUrl) return
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

  /* ---- One step per slide -------------------------------------------------------------------------------
   *
   * The steps were a single vertical list: sixteen rows you scrolled through, each a title and a truncated
   * output. The owner asked for a slide per step (2026-11-26: "for each step, there should be slide"), so the
   * surface is now a deck you move through -- one step filling the pane, with its own text and files on it.
   *
   * `at` is the step ORDER, not an array index, so it survives the step list arriving after the run (cost is
   * fetched separately) and stays right if an order is ever skipped. It follows the run while nobody has driven
   * it: the step working, else the one waiting on a person, else the last one done. Once the person uses the
   * arrows it is theirs until they leave, so a poll every 3s cannot yank the slide out from under them.
   */
  const [at, setAt] = useState<number | null>(null)
  const [driven, setDriven] = useState(false)
  // Only a position the RUN decided counts. `steps[0]` is a placeholder for "nothing has loaded yet"; latching it
  // would freeze the deck on step 1 and never move to the step the run is actually on once the list arrives
  // (found by tests/way-slides.test.tsx -- the slide opened on step 1 of a finished run).
  //
  // A finished run opens on its LAST SUBSTANTIVE step, not simply its last: step 16 of the academic-talk Way is a
  // 158-character "Rehearsed once. Approved." while step 15 holds 5.9k characters of the gate report. Landing on the
  // sign-off made the deck look empty and hid the work behind arrows nobody knew to press (owner, 2026-11-26:
  // "I'm not able to see previous output, it is just saying you've got an output"). A "substantive" step is one whose
  // text is more than a line or two; if none qualify, the last one is used as before.
  const SUBSTANTIVE_CHARS = 400
  const lastWithWork = (() => {
    for (let i = outputs.length - 1; i >= 0; i -= 1) {
      if ((outputs[i].output || "").trim().length >= SUBSTANTIVE_CHARS) return outputs[i].order
    }
    return outputs.length ? outputs[outputs.length - 1].order : undefined
  })()
  const settled = paused ? view?.waiting?.order
    : running ? steps[done]?.order
    : lastWithWork
  useEffect(() => {
    if (!driven && settled != null) setAt(settled)
  }, [driven, settled])

  const idx = at != null ? Math.max(0, steps.findIndex((s) => s.order === at)) : 0
  const slide = steps[idx]
  // Keep the current step's tab visible in the bottom strip as the deck moves: with sixteen steps the strip scrolls
  // sideways, and the one you are on must not be the one you cannot see.
  const stripRef = useRef<HTMLOListElement | null>(null)
  useEffect(() => {
    const el = stripRef.current?.querySelector<HTMLElement>('[aria-current="step"]')
    // Optional call: `scrollIntoView` is absent in some embedded webviews and in jsdom, and a missing scroll helper
    // must never take the pane down with it.
    el?.scrollIntoView?.({ block: "nearest", inline: "nearest" })
  }, [idx])
  const go = (n: number) => {
    if (!steps.length) return
    setDriven(true)
    setAt(steps[Math.min(steps.length - 1, Math.max(0, n))].order)
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!steps.length) return
      // Never steal an arrow from the message box or an input, and never from a dialog.
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      if (e.key === "ArrowRight") { go(idx + 1); e.preventDefault() }
      if (e.key === "ArrowLeft") { go(idx - 1); e.preventDefault() }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [idx, steps.length])

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

      {(view?.state === "needs_inputs" || (showAgain && view?.inputs?.length)) && (
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

      {total > 0 && slide && (
        <section className="dg-way-deck" aria-roledescription="carousel" aria-label="The steps">
          {/* A horizontal track: every step is laid out side by side and the track slides, so moving between steps is
              one transform, not a re-render of different content. Nothing here pans or zooms -- the frame is fixed and
              a step's output is VIEWED inside it (owner, 2026-11-26: "unmovable and unpannable, we should just have a
              viewer for each step's outputs"). Only transform and opacity animate (docs/ios_animation.md §6). */}
          <div className="dg-way-track-frame">
            <div className="dg-way-track" style={{ transform: `translate3d(-${idx * 100}%, 0, 0)` }}>
              {steps.map((s, i) => {
                const wrote = outputs.find((o) => o.order === s.order)
                const current = i === idx
                return (
                  <article
                    key={s.order}
                    className="dg-way-slide"
                    data-state={stateOf(s.order)}
                    data-human={s.is_human ? "true" : "false"}
                    data-current={current ? "true" : undefined}
                    // A step off-screen is hidden from the reader and from tab order, but stays mounted so the track
                    // can slide: unmounting would make the move a jump.
                    aria-hidden={current ? undefined : true}
                    aria-label={`Step ${i + 1} of ${total}: ${s.title}`}
                  >
                    <div className="dg-way-slide-top">
                      <span className="dg-way-n" aria-hidden="true">{stateOf(s.order) === "done" ? "✓" : s.order}</span>
                      <span className="dg-way-slide-count">{i + 1} / {total}</span>
                      {s.is_human && <span className="dg-way-kind">you</span>}
                      {stateOf(s.order) === "review" && <span className="dg-way-kind" data-tone="review">waiting</span>}
                    </div>
                    <h2 className="dg-way-slide-title">{s.title}</h2>
                    {wrote?.output
                      ? <div className="dg-way-slide-out"><Markdown text={wrote.output} /></div>
                      : outputs.length > 0 || outputsError
                        ? <p className="dg-way-slide-out dg-way-note">
                            {outputsError
                              ? `This run's text could not be read: ${outputsError}`
                              : "This step's text did not load. The run has it — press Run again, or reload the page."}
                          </p>
                        : <p className="dg-way-slide-out dg-way-note">
                            {stateOf(s.order) === "running" ? "Working on this step now…"
                              : stateOf(s.order) === "review" ? "This step waits for you." : "No output from this step yet."}
                          </p>}
                    {(wrote?.files?.length ?? 0) > 0 && (
                      // The step's files are listed here, by name; opening one happens in Files below, so there is
                      // exactly one viewer per file and the slide stays a slide.
                      <ul className="dg-way-slide-files">
                        {wrote!.files!.map((f) => (
                          <li key={f.name}><span className="dg-way-fname">{f.name}</span><span className="dg-way-fsize">{kb(f.size_bytes)}</span></li>
                        ))}
                      </ul>
                    )}
                  </article>
                )
              })}
            </div>
          </div>

          {/* The steps, along the bottom: numbered, named, and one click from any step. This is where the Way's shape
              lives -- the slide is always one step, so the strip is what tells you how far through you are. */}
          <nav className="dg-way-strip" aria-label="Steps">
            <button type="button" className="dg-way-arrow" onClick={() => go(idx - 1)} disabled={idx === 0} aria-label="Previous step">←</button>
            <ol className="dg-way-strip-list" ref={stripRef}>
              {steps.map((s, i) => (
                <li key={s.order}>
                  <button
                    type="button"
                    className="dg-way-tab"
                    aria-current={i === idx ? "step" : undefined}
                    data-state={stateOf(s.order)}
                    onClick={() => go(i)}
                    title={`Step ${s.order}: ${s.title}`}
                  >
                    <span className="dg-way-tab-n">{stateOf(s.order) === "done" ? "✓" : s.order}</span>
                    <span className="dg-way-tab-t">{s.title}</span>
                  </button>
                </li>
              ))}
            </ol>
            <button type="button" className="dg-way-arrow" onClick={() => go(idx + 1)} disabled={idx === steps.length - 1} aria-label="Next step">→</button>
          </nav>
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

      {(showAgain || (!running && !paused && !finished)) && canRun && (
        <div className="dg-way-act">
          <button type="button" className="dg-way-start" disabled={busy || !view} onClick={() => void start()}>
            {busy ? "Starting…" : view?.state === "needs_inputs" || showAgain ? "Start with these answers" : "Start"}
          </button>
          <p className="dg-way-note">
            {view ? "A human step pauses the run and waits for you." : "Reading this Way — Start opens when it has loaded."}
          </p>
        </div>
      )}

      {finished && (
        <div className="dg-way-act">
          <p className="dg-way-note">
            {view?.state === "done"
              ? view?.can_start_again
                /* The person bought another output and came back to this same room (one room per owner and Way):
                   the files below are the previous output, and a new run is ready. */
                ? "Finished. This is the output you already have — the run below will produce a new one."
                : "Finished."
              : "Stopped with a failure — the steps above show how far it got."}
          </p>
          {canRun ? (
            /* A finished room could never run again from the UI, and its declared inputs were never shown: the form
               is rendered for needs_inputs only, while readiness returns them whatever the state. */
            <button type="button" className="dg-way-start" disabled={busy} onClick={() => { setShowAgain(true); }}>
              Run again
            </button>
          ) : (
            /* A one-run Way produces one output per purchase (db/217). Once it is spent, "Run again" would call the
               licence gate and be refused, so the honest next step is another output, said where the button was. */
            <p className="dg-way-note">
              This Way is paid per output, and this output was already produced. Buy another output on the Way&apos;s
              page to run it again — the files above stay here.
            </p>
          )}
        </div>
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
                  {open && <FileViewer file={f} text={openText} error={openErr} />}
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
