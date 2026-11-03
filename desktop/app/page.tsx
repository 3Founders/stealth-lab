"use client"
// The screens, in the website's and console's look (docs/desktop_app_plan.md): project picker; chat with streaming and
// tool calls; the changes panel (per-session diff, Undo via `kell undo`); the model strip (which model did each step, its
// tokens and cost, and whether that model is the configured cheap one); tool suggestion cards (kell's tool router, the same
// call the session's plugin makes); and settings (sign-in, models, engine, "Open console").
import { useCallback, useEffect, useRef, useState } from "react"
import Markdown from "@/components/chat/Markdown"
import SmoothText, { Thinking } from "@/components/chat/SmoothText"
import MessageActions from "@/components/chat/MessageActions"
import { useSpringScroll } from "@/components/chat/useSpringScroll"

type Line = { who: "user" | "assistant"; text: string; at?: number }
type Step = { kind: "tool" | "model"; label: string; note?: string }
// One row per step (one model round trip). The engine splits it across events: session.step.started names the model (keyed
// by assistantMessageID), session.step.ended carries tokens and cost for the same message. Neither event carries a rung.
type LedgerRow = { id: string; model?: string; providerID?: string; tokens?: string; cost?: number }
type FileDiff = { file: string; patch: string; additions: number; deletions: number; status: "added" | "deleted" | "modified" }
type ToolCard = { name: string; source: "installed" | "registry"; url: string | null; needsSignIn: boolean }
type Settings = {
  signedIn: boolean; account: string | null; server: string | null
  lane?: "organization" | "own-key" | "none"; gateway?: string | null
  models: { cheap: string | null; strong: string | null; fallback: string | null; mode: string | null }
  engine: { exe: string | null; directory: string | null }
}

// A unified patch's lines, classified for colour: the file headers and @@ hunks stay plain, + and - get the muted colours.
const patchLines = (patch: string) =>
  String(patch || "").split("\n").filter((l, i, all) => !(i === all.length - 1 && l === "")).map((l) => {
    const cls = l.startsWith("+++") || l.startsWith("---") ? "" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""
    return { cls, text: l }
  })

const tokensOf = (d: any) => {
  const t = d?.tokens
  if (!t || typeof t !== "object") return undefined
  const inn = Number(t.input) || 0, out = (Number(t.output) || 0) + (Number(t.reasoning) || 0), cached = Number(t.cache?.read) || 0
  return `${inn} in / ${out} out${cached ? ` / ${cached} cached` : ""}`
}

export default function Home() {
  const [directory, setDirectory] = useState("")
  const [engine, setEngine] = useState<{ up: boolean; error?: string } | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [sessionID, setSessionID] = useState<string | null>(null)
  const [spend, setSpend] = useState(0)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const [files, setFiles] = useState<FileDiff[]>([])
  const [filesNote, setFilesNote] = useState("")
  const [confirm, setConfirm] = useState("")
  const [pending, setPending] = useState(0)
  const [cards, setCards] = useState<ToolCard[]>([])
  const [cardsNote, setCardsNote] = useState("")
  const [settings, setSettings] = useState<Settings | null>(null)
  const [consoleNote, setConsoleNote] = useState("")
  const [signinNote, setSigninNote] = useState("")
  const logRef = useRef<HTMLDivElement>(null)
  const logContentRef = useRef<HTMLDivElement>(null)
  // The same spring scroll as the website chat: follows new text only while you are at the bottom, rubber-bands at the edges.
  const { follow, scrollToEnd } = useSpringScroll(logRef, logContentRef, sessionID ?? "")
  const busyRef = useRef(false)                       // a late poll must not overwrite a turn that started after it

  useEffect(() => {
    setDirectory(window.localStorage.getItem("kell.directory") || "")
    fetch("/api/engine").then((r) => r.json()).then((j) => setEngine({ up: !!j.up }))
    fetch("/api/settings").then((r) => r.json()).then(setSettings).catch(() => undefined)
  }, [])
  useEffect(() => { scrollToEnd({ instant: true }) }, [scrollToEnd, sessionID])
  useEffect(() => { follow() }, [lines, steps, busy, follow])

  const loadChanges = useCallback(async (sid: string | null) => {
    // With a session: its turn's snapshot pair (OpenCode's shadow git) gives both the diff and the undo target, so the panel
    // and the Undo button can never disagree. Without one: the working tree's diff.
    const r = await fetch(sid ? `/api/diff?sessionID=${encodeURIComponent(sid)}` : "/api/diff")
    const j = await r.json()
    if (!r.ok) { setFiles([]); setPending(0); setFilesNote(j.error || "could not read the changes"); return }
    setFiles(Array.isArray(j.files) ? j.files : [])
    setPending(typeof j.pending === "number" ? j.pending : (j.files || []).length)
    setFilesNote(Array.isArray(j.files) && j.files.length === 0
      ? (sid ? (j.snapshot ? "this session's last turn changed nothing" : "this session has no turn to show yet") : "the working tree is clean")
      : (typeof j.pending === "number" && j.pending === 0 ? "undone — nothing left to put back" : ""))
  }, [])

  const loadTools = async (need: string) => {
    setCardsNote("asking kell's tool router…")
    try {
      const r = await fetch("/api/tools", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ need }) })
      const j = await r.json()
      if (!r.ok) { setCards([]); setCardsNote(j.error || "the tool router did not answer"); return }
      setCards(j.tools || [])
      setCardsNote((j.tools || []).length ? "" : "no outside tool fits this request")
    } catch (err: any) { setCards([]); setCardsNote(String(err?.message || err)) }
  }

  const openProject = async () => {
    setEngine(null)
    window.localStorage.setItem("kell.directory", directory)
    const r = await fetch("/api/engine", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory }) })
    const j = await r.json()
    setEngine(j.up ? { up: true } : { up: false, error: j.error })
    if (j.up) { setLines([]); setSteps([]); setLedger([]); setSessionID(null); setCards([]); setCardsNote(""); setSpend(0); loadChanges(null) }
  }

  const appendAssistant = (delta: string) => setLines((l) => {
    const last = l.at(-1)
    if (last?.who === "assistant") { const c = l.slice(0, -1); c.push({ who: "assistant", text: last.text + delta, at: last.at }); return c }
    return [...l, { who: "assistant", text: delta, at: Date.now() }]
  })

  // The prompt an answer came from, for "Ask again".
  const lastUserBefore = (i: number) => { for (let k = i - 1; k >= 0; k--) if (lines[k].who === "user") return lines[k].text; return undefined }
  const copyText = async (t: string) => { try { await navigator.clipboard.writeText(t); return true } catch { return false } }

  const send = async (again?: string) => {
    const body = again ?? text
    if (!body.trim() || busy || !directory) return
    const sent = body
    setLines((l) => [...l, { who: "user", text: sent, at: Date.now() }])
    if (again === undefined) setText("")                     // sending it again keeps what you were typing
    setBusy(true); busyRef.current = true
    // The cards are for this request: ask while the turn runs (the tool router is fast; the model is not).
    setCards([]); setCardsNote(""); loadTools(sent)
    const r = await fetch("/api/prompt", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID, directory, text: sent }) })
    if (!r.ok || !r.body) {
      const err = await r.text()
      setLines((l) => [...l, { who: "assistant", text: `kell could not reach the engine: ${err.slice(0, 200)}`, at: Date.now() }])
      setBusy(false); return
    }
    let sid = sessionID
    let buffer = ""
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += dec.decode(value, { stream: true })
      const parts = buffer.split("\n\n")
      buffer = parts.pop() || ""
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue
        let ev: any
        try { ev = JSON.parse(part.slice(6)) } catch { continue }
        const t = ev.type as string
        const d = ev.data || {}
        if (t === "session") { sid = ev.sessionID; setSessionID(ev.sessionID) }
        else if (t === "session.text.delta") appendAssistant(d.delta ?? d.text ?? "")
        else if (t === "session.tool.called" || t === "session.tool.success" || t === "session.tool.failed")
          setSteps((s) => [...s, { kind: "tool", label: d.tool || d.name || "tool", note: t.endsWith("failed") ? "failed" : undefined }])
        else if (t === "session.step.started") {
          const id = String(d.assistantMessageID || "")
          if (id && d.model?.id) setLedger((rows) => [...rows.filter((r) => r.id !== id), { id, model: d.model.id, providerID: d.model.providerID }])
        }
        else if (t === "session.step.ended") {
          const id = String(d.assistantMessageID || "")
          const patch = { tokens: tokensOf(d), cost: d.cost != null ? Number(d.cost) : undefined }
          if (id) setLedger((rows) => rows.some((r) => r.id === id) ? rows.map((r) => (r.id === id ? { ...r, ...patch } : r)) : [...rows, { id, ...patch }])
          if (d.cost != null) setSpend((c) => c + Number(d.cost))
        }
        else if (t === "error") { appendAssistant(`\n(error: ${ev.error})`); setBusy(false) }
      }
    }
    setBusy(false)
    busyRef.current = false
    // The session's snapshot record lands shortly after the last stream event, and how long is not fixed: check now, then
    // poll a few times, so the panel fills itself instead of waiting for a Refresh click.
    loadChanges(sid)
    for (const ms of [600, 1200, 2500, 5000]) setTimeout(() => { if (!busyRef.current) loadChanges(sid) }, ms)
  }

  const appendNote = (text: string) => setLines((l) => [...l, { who: "assistant", text, at: Date.now() }])

  // Undo: put the session's changed files back as they were before its last turn (OpenCode's snapshot of that turn), or
  // `kell undo` for a dispatched answer. A second click only when a file was edited since -- that edit would be dropped.
  const undo = async (force = false) => {
    const r = await fetch("/api/undo", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory, sessionID, force }) })
    const j = await r.json()
    if (!r.ok) { appendNote(`undo: ${j.error || "did not work"}`); return }
    if (j.needsConfirm) { setConfirm(j.warn); return }
    setConfirm("")
    appendNote(j.restored ? `undo: put back ${j.restored.join(", ")}.` : `kell undo:\n${j.output || "(nothing to undo)"}`)
    loadChanges(sessionID)
  }

  const openConsole = async () => {
    setConsoleNote("starting the console…")
    const r = await fetch("/api/settings", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "open" }) })
    const j = await r.json()
    setConsoleNote(j.output || j.error || "no answer")
  }

  // kell login writes ~/.stealthlab/config.json; the app only polls settings until the token is there.
  const signIn = async () => {
    setSigninNote("A browser window will open. Approve there, then come back here.")
    const started = await fetch("/api/signin", { method: "POST" }).then((r) => r.json()).catch(() => ({ error: "could not start kell login" }))
    if (started?.error) { setSigninNote(String(started.error)); return }
    if (started?.already) { setSigninNote("Already signed in."); return }
    const until = Date.now() + 120_000
    const tick = async () => {
      const s = await fetch("/api/settings").then((r) => r.json()).catch(() => null)
      if (s) setSettings(s)
      if (s?.signedIn) { setSigninNote("Signed in."); return }
      if (Date.now() > until) { setSigninNote("Still not signed in. Try again, or run `kell login` in a terminal."); return }
      setTimeout(tick, 2000)
    }
    setTimeout(tick, 2000)
  }

  const isCheap = (r: LedgerRow) => !!settings?.models.cheap && `${r.providerID}/${r.model}` === settings.models.cheap
  const readEngine = engine?.up && !!directory

  return (
    <main className="frame">
      <h1 style={{ padding: "18px 0 2px" }}>kell</h1>
      <div className="modelstrip">
        <span title={sessionID || undefined}>{sessionID ? `session ${sessionID.slice(0, 12)}…` : "no session"}</span>
        <span>{readEngine ? "engine up" : "engine down"}</span>
        <span className="spend">{spend > 0 ? `spent $${spend.toFixed(4)}` : "no spend yet"}</span>
      </div>
      <div className="shell">
        <aside className="side">
          <h2>Project</h2>
          <p style={{ margin: "10px 0 4px" }}>
            <input value={directory} onChange={(e) => setDirectory(e.target.value)} disabled={!!sessionID}
              placeholder="a folder path to work in" className="mono" style={{ width: "100%", padding: 10, borderRadius: 10, border: "1px solid var(--rule-strong)", background: "var(--surface)" }} />
          </p>
          <button className="btn-ink" onClick={openProject} disabled={!!sessionID || !directory || busy}>open project</button>
          {sessionID && <p style={{ fontSize: 13, marginTop: 8 }}><button className="copy" onClick={() => { setSessionID(null); setLines([]); setSteps([]); setLedger([]); loadChanges(null) }}>reset session</button></p>}
          {engine && !engine.up && (engine.error || sessionID) && <p className="banner" style={{ marginTop: 14 }}>Engine problem{engine.error ? `: ${engine.error}` : ""}</p>}

          <h2 style={{ marginTop: 22 }}>Changes</h2>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button className="copy" onClick={() => loadChanges(sessionID)} disabled={!readEngine}>refresh</button>
            <button className="copy" onClick={() => undo()} disabled={!directory || busy || (!!sessionID && pending === 0)}>undo</button>
          </div>
          {confirm ? (
            <div className="banner" style={{ marginTop: 8 }}>
              <p style={{ margin: "0 0 6px" }}>{confirm}. Undo would drop that.</p>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn-ink" onClick={() => undo(true)}>undo anyway</button>
                <button className="copy" onClick={() => setConfirm("")}>keep it</button>
              </div>
            </div>
          ) : (
            <p style={{ fontSize: 12, color: "var(--grey)", margin: "6px 0 0" }}>
              undo puts back the files the session&apos;s last turn changed (from OpenCode&apos;s snapshot of that turn); with no
              session, it runs `kell undo` for a dispatched answer.
            </p>
          )}
          <div className="changes">
            {filesNote && !files.length && <p style={{ fontSize: 13, color: "var(--grey)" }}>{filesNote}</p>}
            {files.map((f) => (
              <details className="filecard" key={f.file} open={files.length === 1}>
                <summary>
                  <span className="path">{f.file}</span>
                  <span className="stat"><span className="add">+{f.additions}</span> <span className="del">−{f.deletions}</span></span>
                </summary>
                <div className="diff">{patchLines(f.patch).map((l, i) => <div className={l.cls} key={i}>{l.text || " "}</div>)}</div>
              </details>
            ))}
          </div>

          <h2 style={{ marginTop: 22 }}>Tools</h2>
          {cardsNote && <p style={{ fontSize: 13, color: "var(--grey)", margin: "8px 0 0" }}>{cardsNote}</p>}
          <div className="cards">
            {cards.map((c) => (
              <div className="toolcard" key={c.name}>
                <div className="body">
                  <div className="name" title={`${c.name}${c.url ? ` — ${c.url}` : ""}`}>{c.name}</div>
                  <div className="meta">{c.source === "installed" ? "installed here" : "from the registry"}{c.needsSignIn ? " · needs a sign-in" : ""}</div>
                </div>
                <div className="pillrow">
                  <span className={`pill ${c.source === "installed" ? "installed" : "outline"}`}>{c.source === "installed" ? "installed" : "available"}</span>
                </div>
              </div>
            ))}
          </div>
          {cards.length > 0 && <p style={{ fontSize: 12, color: "var(--grey)", marginTop: 6 }}>through kell: the session's plugin adds the fitting ones itself (KELL_OC_ADD_REMOTE=on).</p>}

          <h2 style={{ marginTop: 22 }}>Settings</h2>
          {!settings ? <p style={{ fontSize: 13, color: "var(--grey)" }}>reading…</p> : (
            <dl className="kv">
              <dt>account</dt>
              <dd>{settings.signedIn ? <>signed in <span className="mono">{settings.account || settings.server || ""}</span></> : (
                <span>not signed in <button type="button" className="copy" onClick={() => void signIn()}>sign in</button></span>
              )}</dd>
              <dt>models</dt>
              <dd>{settings.lane === "organization"
                ? <>through your organization <span className="mono">{settings.gateway || ""}</span> (kell&apos;s keys, nothing to add here)</>
                : settings.lane === "own-key" ? "your own provider key (from kell setup)"
                  : "not set up — run `kell join` for your organization's pooled keys"}</dd>
              <dt>cheap</dt><dd className="mono">{settings.models.cheap || "—"}</dd>
              <dt>strong</dt><dd className="mono">{settings.models.strong || "—"}</dd>
              <dt>fallback</dt><dd className="mono">{settings.models.fallback || "—"}</dd>
              <dt>mode</dt><dd>{settings.models.mode || "—"}</dd>
              <dt>engine</dt><dd className="mono">{settings.engine.exe ? "found" : "not found — run `kell setup --client opencode`"}</dd>
            </dl>
          )}
          <p style={{ marginTop: 10 }}><button className="copy" onClick={openConsole}>open console</button></p>
          {consoleNote && <p style={{ fontSize: 12, color: "var(--grey)", marginTop: 4 }}>{consoleNote}</p>}
          {signinNote && <p style={{ fontSize: 12, color: "var(--grey)", marginTop: 4 }} role="status">{signinNote}</p>}
        </aside>
        <section className="main" style={{ padding: "0 0 0 var(--gap)" }}>
          <h2>Chat</h2>
          <div className="chat">
            <div className="log" ref={logRef}>
              {lines.length === 0 && (
                <p className="banner">Open a project, then say what to do. The cheap model runs the turn and switches to the strong model when the tests say so; tool calls show as chips below.</p>
              )}
              <div className="logc" ref={logContentRef}>
                {lines.map((l, i) => {
                  const live = busy && l.who === "assistant" && i === lines.length - 1   // the reply still arriving
                  return (
                    <div className={`line ${l.who}`} key={i}>
                      <div className="who dg-meta"><b>{l.who === "user" ? "you" : "kell"}</b>{l.who === "assistant" && <span className="dg-badge">agent</span>}{l.at ? <time>{new Date(l.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time> : null}</div>
                      <div className="dg-msg">
                        <div className="bubble">
                          {l.who === "user" ? l.text : live ? <SmoothText text={l.text} markdown /> : <Markdown text={l.text} />}
                        </div>
                        {l.text && !live ? (
                          <MessageActions copyText={l.text} retryText={l.who === "user" ? l.text : lastUserBefore(i)}
                            onCopy={copyText} onRetry={(t) => void send(t)} retryLabel={l.who === "user" ? "Send this again" : "Ask again"} />
                        ) : null}
                      </div>
                    </div>
                  )
                })}
                {busy && lines.at(-1)?.who !== "assistant" && (
                  <div className="line assistant"><div className="who">kell</div><div className="bubble"><Thinking /></div></div>
                )}
              </div>
            </div>
            {steps.length > 0 && (
              <div className="steps">
                {steps.slice(-14).map((s, i) => <span className="step" key={i}><b>{s.kind === "tool" ? "tool" : "model"}</b> {s.label}{s.note ? ` — ${s.note}` : ""}</span>)}
              </div>
            )}
            {ledger.length > 0 && (
              <div className="ledger">
                <div className="row" style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                  <span>step</span><span>tokens</span><span style={{ textAlign: "right" }}>cost</span>
                </div>
                {ledger.map((r) => (
                  <div className="row" key={r.id}>
                    <span><span className={`rung ${isCheap(r) ? "cheap" : "strong"}`}>{isCheap(r) ? "cheap" : "strong"}</span> <b>{r.model || (r.id ? "picking model…" : "")}</b></span>
                    <span>{r.tokens || "—"}</span>
                    <span style={{ textAlign: "right" }}>{r.cost != null ? `$${r.cost.toFixed(5)}` : "—"}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="composer">
              <form className="dg-composer" onSubmit={(e) => { e.preventDefault(); send() }}>
                <textarea value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={directory ? "tell kell what to do…" : "open a project first"}
                  disabled={!!engine && !engine.up}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} />
                <button type="submit" disabled={busy || !text.trim() || !directory || (engine != null && !engine.up)}>{busy ? "Working…" : "Send"}</button>
              </form>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}
