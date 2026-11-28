"use client"
// An execution: a Way run on this computer (lib/executions.ts), shown as the website shows a run in a room
// (routing_kell_frontend/components/chat/WayRunPanel.tsx): one page per step on a horizontal track, the steps along the
// bottom, a page for what the run was given and one for what it produced. Same classes, same motion
// (components/execution-deck.css, docs/ios_animation.md): only transform and opacity move, on the smooth spring, and
// reduced motion turns it off.
//
// The difference is underneath: the AI steps are done by this app's engine in the execution's own folder, and a step
// waiting on the person is answered here.
import { useCallback, useEffect, useRef, useState } from "react"

import Markdown from "@/components/chat/Markdown"
import "./execution-deck.css"

type FileOut = { name: string; size_bytes?: number; media_type?: string; text?: boolean; url?: string }
type Refused = { name: string; why: string }
type Output = { order: number; title: string; output: string; actor?: string; files?: FileOut[]; files_refused?: Refused[] }
type RunStep = {
  n: number; title: string; by?: string; kind?: string; imports?: string[]; produces?: string | string[]
  inputs?: { key: string; label?: string }[]; supplied?: Record<string, string>; description?: string
}
type RunView = {
  status: string; steps: RunStep[]; outputs: Output[]; input?: string; error?: string | null
  artifacts?: FileOut[]; artifacts_refused?: Refused[]; download_all?: string
  waiting_on?: { order?: number; title?: string; description?: string } | null
}
type Execution = {
  id: string; title: string; status: string; step?: number; error?: string; directory: string; runId?: string
  startedAt: number; endedAt?: number
}
type Page = { key: string; kind: "inputs" | "step" | "results"; step?: RunStep }

function kb(n?: number): string {
  if (n == null) return ""
  return n < 1024 ? `${n} B` : `${Math.max(1, Math.round(n / 1024))} KB`
}

const fileUrl = (runId: string, name: string) => `/api/way-artifact?run=${encodeURIComponent(runId)}&name=${encodeURIComponent(name)}`

/** One file, in a fixed frame: an image, a PDF, or text. Bytes come through this app's licence-checked route. */
function FileViewer({ runId, file }: { runId: string; file: FileOut }) {
  const [text, setText] = useState<string | null>(null)
  const [blob, setBlob] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const type = (file.media_type || "").toLowerCase()
  useEffect(() => {
    let url: string | null = null
    let gone = false
    void (async () => {
      try {
        const r = await fetch(fileUrl(runId, file.name))
        if (!r.ok) throw new Error(`the file could not be read (${r.status})`)
        if (type.startsWith("image/") || type === "application/pdf") {
          url = URL.createObjectURL(await r.blob())
          if (!gone) setBlob(url)
        } else {
          const t = await r.text()
          if (!gone) setText(t)
        }
      } catch (e) { if (!gone) setErr(String((e as Error)?.message || e)) }
    })()
    return () => { gone = true; if (url) URL.revokeObjectURL(url) }
  }, [runId, file.name, type])
  if (err) return <p className="dg-way-viewerr">This file could not be read: {err}</p>
  if (type.startsWith("image/")) return blob ? <img className="dg-way-viewimg" src={blob} alt={file.name} /> : <p className="dg-way-note">Loading…</p>
  if (type === "application/pdf") return blob ? <embed className="dg-way-viewpdf" src={blob} type="application/pdf" /> : <p className="dg-way-note">Loading…</p>
  if (text == null) return <p className="dg-way-note">Loading…</p>
  if (type === "text/markdown" || /\.md$/i.test(file.name)) return <div className="dg-way-slide-out"><Markdown text={text} /></div>
  if (type === "application/json") {
    try { return <pre className="dg-way-viewsrc">{JSON.stringify(JSON.parse(text), null, 2)}</pre> } catch { /* shown as text */ }
  }
  return <pre className="dg-way-viewsrc">{text}</pre>
}

export default function ExecutionDeck({ id, onBack, onOpenChat }: {
  id: string
  onBack?: () => void
  /** Open the engine chat this execution ran in (its full conversation, tool calls included). */
  onOpenChat?: (directory: string) => void
}) {
  const [ex, setEx] = useState<Execution | null>(null)
  const [run, setRun] = useState<RunView | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState("")
  const [openFile, setOpenFile] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/executions?id=${encodeURIComponent(id)}`, { cache: "no-store" })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(String(j?.error || `the app answered ${r.status}`))
      setEx(j.execution || null)
      if (j.run) setRun(j.run)
      setRunErr(j.run_error || null)
    } catch (e) { setError(String((e as Error)?.message || e)) }
  }, [id])

  useEffect(() => { setRun(null); setEx(null); setAt(null); setDriven(false); void load() }, [id, load])
  const live = !!ex && ["starting", "running", "needs_you"].includes(ex.status)
  useEffect(() => {
    if (!live) return
    timer.current = setTimeout(() => void load(), 2500)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [live, ex, run, load])

  async function act(action: string, extra: Record<string, unknown> = {}) {
    setBusy(true); setError(null)
    try {
      const r = await fetch("/api/executions", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, id, ...extra }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(String(j?.error || `the app answered ${r.status}`))
      await load()
    } catch (e) { setError(String((e as Error)?.message || e)) } finally { setBusy(false) }
  }

  const steps = run?.steps || []
  const outputs = run?.outputs || []
  const files = run?.artifacts || []
  const refused = run?.artifacts_refused || []
  const total = steps.length
  const done = outputs.length
  const pct = total ? Math.round((done / total) * 100) : 0
  const waiting = ex?.status === "waiting_human" || run?.status === "waiting_human"
  const finished = ["done", "failed", "cancelled"].includes(ex?.status || "")
  const stateOf = (order: number) => {
    if (outputs.some((o) => o.order === order)) return "done"
    if (waiting && run?.waiting_on?.order === order) return "review"
    if (live && (ex?.step === order || steps[done]?.n === order)) return "running"
    return "waiting"
  }
  const given = steps.flatMap((s) => Object.entries(s.supplied || {}).map(([k, v]) => ({
    step: s.n, label: s.inputs?.find((i) => i.key === k)?.label || k, value: v })))
  const answers = outputs.filter((o) => o.actor === "human")
  const hasInputs = Boolean((run?.input || "").trim()) || given.length > 0
  const hasResults = Boolean(ex?.runId) && (finished || files.length > 0)
  const pages: Page[] = [
    ...(hasInputs ? [{ key: "inputs", kind: "inputs" as const }] : []),
    ...steps.map((s) => ({ key: `s${s.n}`, kind: "step" as const, step: s })),
    ...(hasResults ? [{ key: "results", kind: "results" as const }] : []),
  ]

  // Where the deck stands follows the run until the person moves it (then it is theirs), as on the website.
  const [at, setAt] = useState<number | string | null>(null)
  const [driven, setDriven] = useState(false)
  const settled = waiting ? run?.waiting_on?.order
    : live ? (ex?.step || steps[done]?.n)
    : finished && hasResults ? "results" : outputs[outputs.length - 1]?.order
  useEffect(() => { if (!driven && settled != null) setAt(settled) }, [driven, settled])
  const idx = at == null ? 0 : Math.max(0, pages.findIndex((p) => (typeof at === "number" ? p.step?.n === at : p.key === at)))
  const go = (n: number) => {
    if (!pages.length) return
    setDriven(true)
    const p = pages[Math.min(pages.length - 1, Math.max(0, n))]
    setAt(p.step ? p.step.n : p.key)
  }
  const stripRef = useRef<HTMLOListElement | null>(null)
  // Keep the current tab in view by scrolling the strip itself. scrollIntoView would also scroll every scrollable
  // ancestor, and here that moved the whole middle column sideways.
  useEffect(() => {
    const strip = stripRef.current
    const tab = strip?.querySelector<HTMLElement>('[aria-current="step"]')
    if (!strip || !tab) return
    const left = tab.offsetLeft - strip.offsetLeft
    if (left < strip.scrollLeft) strip.scrollLeft = left - 8
    else if (left + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = left + tab.offsetWidth - strip.clientWidth + 8
  }, [idx])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      if (e.key === "ArrowRight") { go(idx + 1); e.preventDefault() }
      if (e.key === "ArrowLeft") { go(idx - 1); e.preventDefault() }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  const fileRow = (f: FileOut, where: string) => {
    const k = `${where}:${f.name}`
    const open = openFile === k
    return (
      <li key={k} data-open={open ? "true" : "false"}>
        <div className="dg-way-filerow">
          <span className="dg-way-fname">{f.name}</span>
          <span className="dg-way-fsize">{kb(f.size_bytes)}</span>
          {ex?.runId && (
            <span className="dg-way-facts">
              <button type="button" onClick={() => setOpenFile(open ? null : k)} aria-expanded={open}>{open ? "Hide" : "View"}</button>
              <a href={fileUrl(ex.runId, f.name)} download={f.name}>Download</a>
            </span>
          )}
        </div>
        {open && ex?.runId && <FileViewer runId={ex.runId} file={files.find((x) => x.name === f.name) || f} />}
      </li>
    )
  }

  const statusLine = !ex ? "Opening…"
    : ex.status === "starting" ? "Starting on this computer…"
    : ex.status === "needs_you" ? "waiting for your permission (see the Inbox)"
    : ex.status === "running" ? `running on this computer${ex.step ? ` · step ${ex.step}` : ""}`
    : ex.status === "waiting_human" ? "waiting on you"
    : ex.status === "done" ? "finished" : ex.status === "cancelled" ? "cancelled" : "stopped"

  return (
    <section className="dg-way-main dk-exec" aria-label={ex ? `${ex.title}: execution` : "Execution"}>
      <header className="dg-way-head">
        <p className="dg-way-sub">
          {total > 0 ? `${done} of ${total} steps done · ` : ""}{statusLine}
        </p>
        <span style={{ display: "inline-flex", gap: 8 }}>
          {onBack && <button type="button" className="dg-way-swap" onClick={onBack}>Back to chats</button>}
          {ex && onOpenChat && <button type="button" className="dg-way-swap" onClick={() => onOpenChat(ex.directory)}>Open its folder</button>}
          {live && <button type="button" className="dg-way-swap" disabled={busy} onClick={() => void act("cancel")}>Stop</button>}
        </span>
        {total > 0 && (
          <div className="dg-way-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ transform: `scaleX(${pct / 100})` }} />
          </div>
        )}
      </header>

      {runErr && <p className="dg-way-error" role="alert">kell could not be reached for this run: {runErr}</p>}
      {ex?.error && <p className="dg-way-error" role="alert">{ex.error}</p>}
      {!run && !runErr && <p className="dg-way-note">{ex?.status === "starting" ? "Asking kell for the Way's steps…" : "Reading this run…"}</p>}

      {pages.length > 0 && (
        <section className="dg-way-deck" aria-roledescription="carousel" aria-label="The steps">
          <div className="dg-way-track-frame">
            <div className="dg-way-track" style={{ transform: `translate3d(-${idx * 100}%, 0, 0)` }}>
              {pages.map((page, pi) => {
                const current = pi === idx
                if (page.kind === "inputs") return (
                  <article key="inputs" className="dg-way-page" data-current={current ? "true" : undefined}
                    aria-hidden={current ? undefined : true} aria-label="What this run was given">
                    <div className="dg-way-slide-top"><span className="dg-way-kind">inputs</span></div>
                    <h2 className="dg-way-slide-title">What this run was given</h2>
                    {(run?.input || "").trim() && <div className="dg-way-slide-out"><Markdown text={run!.input!} /></div>}
                    {given.length > 0 && (
                      <dl className="dg-way-given">
                        {given.map((g) => (<div key={`${g.step}.${g.label}`}><dt>{g.label}<span className="dg-way-stepof">step {g.step}</span></dt><dd>{g.value}</dd></div>))}
                      </dl>
                    )}
                  </article>
                )
                if (page.kind === "results") return (
                  <article key="results" className="dg-way-page" data-current={current ? "true" : undefined}
                    aria-hidden={current ? undefined : true} aria-label="What this run produced">
                    <div className="dg-way-slide-top">
                      <span className="dg-way-kind">{ex?.status === "done" ? "results" : "so far"}</span>
                      <span className="dg-way-slide-count">ran on this computer</span>
                    </div>
                    <h2 className="dg-way-slide-title">{ex?.status === "done" ? "What this run produced" : "What it produced before it stopped"}</h2>
                    {answers.length > 0 && (
                      <section className="dg-way-used">
                        <h3>Your answers</h3>
                        <dl className="dg-way-given">
                          {answers.map((a) => (<div key={a.order}><dt>{a.title}<span className="dg-way-stepof">step {a.order}</span></dt><dd>{a.output}</dd></div>))}
                        </dl>
                      </section>
                    )}
                    {files.length > 0 ? (
                      <section className="dg-way-used">
                        <h3>Files ({files.length})</h3>
                        <ul className="dg-way-slide-files">{files.map((f) => fileRow(f, "results"))}</ul>
                        {files.length > 1 && ex?.runId && (
                          <p><a className="dg-way-all" href={`/api/way-artifact?run=${encodeURIComponent(ex.runId)}`} download="way-output.zip">Download all ({files.length})</a></p>
                        )}
                      </section>
                    ) : <p className="dg-way-slide-out dg-way-note">No files. Each step&apos;s own text is on its page.</p>}
                    {refused.length > 0 && (
                      <section className="dg-way-used">
                        <h3>Not kept</h3>
                        <ul className="dg-way-refused">{refused.map((r) => (<li key={r.name}><span className="dg-way-fname">{r.name}</span><span className="dg-way-fsize">{r.why}</span></li>))}</ul>
                      </section>
                    )}
                  </article>
                )
                const s = page.step!
                const i = steps.findIndex((x) => x.n === s.n)
                const wrote = outputs.find((o) => o.order === s.n)
                const st = stateOf(s.n)
                // A person's step: the run says so by kind, by who does it, or by who answered it (an older server sends only the latter two).
                const asks = s.kind === "human" || s.by === "you" || wrote?.actor === "human"
                const question = asks ? (s.description || (run?.waiting_on?.order === s.n ? run?.waiting_on?.description : "")) : ""
                const gave = Object.entries(s.supplied || {})
                const reads = (s.imports || []).filter(Boolean)
                return (
                  <article key={s.n} className="dg-way-slide" data-state={st} data-human={asks ? "true" : "false"}
                    data-current={current ? "true" : undefined} aria-hidden={current ? undefined : true}
                    aria-label={`Step ${i + 1} of ${total}: ${s.title}`}>
                    <div className="dg-way-slide-top">
                      <span className="dg-way-n" aria-hidden="true">{st === "done" ? "✓" : s.n}</span>
                      <span className="dg-way-slide-count">{i + 1} / {total}</span>
                      {asks ? <span className="dg-way-kind">you</span>
                        : s.by === "kell" ? <span className="dg-way-kind">kell</span>
                        : <span className="dg-way-kind">this computer</span>}
                      {st === "review" && <span className="dg-way-kind" data-tone="review">waiting</span>}
                    </div>
                    <h2 className="dg-way-slide-title">{s.title}</h2>
                    {(question || gave.length > 0 || reads.length > 0) && (
                      <section className="dg-way-used" aria-label="What this step used">
                        {question && <p className="dg-way-ask"><span className="dg-way-kind" data-tone="review">asks you</span>{question}</p>}
                        {gave.length > 0 && (
                          <dl className="dg-way-given">{gave.map(([k, v]) => (<div key={k}><dt>{s.inputs?.find((d) => d.key === k)?.label || k}</dt><dd>{v}</dd></div>))}</dl>
                        )}
                        {reads.length > 0 && <p className="dg-way-reads">Read {reads.map((r, ri) => (<span key={r}>{ri ? ", " : ""}<span className="dg-way-fname">{r}</span></span>))} from earlier steps</p>}
                      </section>
                    )}
                    {asks && wrote?.output && <p className="dg-way-kind dg-way-answered">your answer</p>}
                    {wrote?.output
                      ? <div className="dg-way-slide-out"><Markdown text={wrote.output} /></div>
                      : <p className="dg-way-slide-out dg-way-note">
                          {st === "running" ? "Working on this step on this computer…" : st === "review" ? "This step waits for you — answer it below." : "No output from this step yet."}
                        </p>}
                    {(wrote?.files?.length ?? 0) > 0 && <ul className="dg-way-slide-files">{wrote!.files!.map((f) => fileRow(f, `s${s.n}`))}</ul>}
                    {(wrote?.files_refused?.length ?? 0) > 0 && (
                      <ul className="dg-way-refused">{wrote!.files_refused!.map((r) => (<li key={r.name}><span className="dg-way-fname">{r.name}</span><span className="dg-way-fsize">{r.why}</span></li>))}</ul>
                    )}
                  </article>
                )
              })}
            </div>
          </div>

          <nav className="dg-way-strip" aria-label="Steps">
            <button type="button" className="dg-way-arrow" onClick={() => go(idx - 1)} disabled={idx === 0} aria-label="Previous step">←</button>
            <ol className="dg-way-strip-list" ref={stripRef}>
              {pages.map((p, pi) => (
                <li key={p.key}>
                  {p.kind === "step" ? (
                    <button type="button" className="dg-way-tab" aria-current={pi === idx ? "step" : undefined}
                      data-state={stateOf(p.step!.n)} onClick={() => go(pi)} title={`Step ${p.step!.n}: ${p.step!.title}`}>
                      <span className="dg-way-tab-n">{stateOf(p.step!.n) === "done" ? "✓" : p.step!.n}</span>
                      <span className="dg-way-tab-t">{p.step!.title}</span>
                    </button>
                  ) : (
                    <button type="button" className="dg-way-xtab" aria-current={pi === idx ? "step" : undefined} onClick={() => go(pi)}>
                      <span className="dg-way-tab-t">{p.kind === "inputs" ? "Inputs" : `Results${files.length ? ` · ${files.length}` : ""}`}</span>
                    </button>
                  )}
                </li>
              ))}
            </ol>
            <button type="button" className="dg-way-arrow" onClick={() => go(idx + 1)} disabled={idx === pages.length - 1} aria-label="Next step">→</button>
          </nav>
        </section>
      )}

      {waiting && run?.waiting_on && (
        <section className="dg-way-pause">
          <h2>Waiting on you — step {run.waiting_on.order}</h2>
          <p className="dg-way-pause-title">{run.waiting_on.title}</p>
          {run.waiting_on.description && <p className="dg-way-note">{run.waiting_on.description}</p>}
          <form className="dg-way-pause-form" onSubmit={(e) => { e.preventDefault(); void act("answer", { note }).then(() => setNote("")) }}>
            <label htmlFor="dk-exec-note">What you did or decided</label>
            <input id="dk-exec-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="A line or two is enough" />
            <button type="submit" className="dg-way-start" disabled={busy}>{busy ? "Continuing…" : "Continue"}</button>
          </form>
        </section>
      )}

      {error && <p className="dg-way-error" role="alert">{error}</p>}
    </section>
  )
}
