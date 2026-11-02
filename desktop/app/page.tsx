"use client"
// The first screens, in the website's and console's look: the project picker, the chat with streaming and tool chips,
// the model strip (which model did each step and its cost), and an Undo button (`kell undo`). Settings/Account and the
// tool-suggestion cards come after the engine contract settles (docs/desktop_app_plan.md).
import { useEffect, useRef, useState } from "react"

type Line = { who: "user" | "assistant"; text: string }
type Step = { kind: "tool" | "model"; label: string; note?: string }

export default function Home() {
  const [directory, setDirectory] = useState("")
  const [engine, setEngine] = useState<{ up: boolean; error?: string } | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [sessionID, setSessionID] = useState<string | null>(null)
  const [spend, setSpend] = useState(0)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setDirectory(window.localStorage.getItem("kell.directory") || "")
    fetch("/api/engine").then((r) => r.json()).then((j) => setEngine({ up: !!j.up }))
  }, [])
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [lines, steps])

  const openProject = async () => {
    setEngine(null)
    window.localStorage.setItem("kell.directory", directory)
    const r = await fetch("/api/engine", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory }) })
    const j = await r.json()
    setEngine(j.up ? { up: true } : { up: false, error: j.error })
    if (j.up) { setLines([]); setSteps([]); setSessionID(null) }
  }

  const appendAssistant = (delta: string) => setLines((l) => {
    const last = l.at(-1)
    if (last?.who === "assistant") { const c = l.slice(0, -1); c.push({ who: "assistant", text: last.text + delta }); return c }
    return [...l, { who: "assistant", text: delta }]
  })

  const send = async () => {
    if (!text.trim() || busy || !directory) return
    const sent = text
    setLines((l) => [...l, { who: "user", text: sent }])
    setText(""); setBusy(true)
    const r = await fetch("/api/prompt", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID, directory, text: sent }) })
    if (!r.ok || !r.body) {
      const err = await r.text()
      setLines((l) => [...l, { who: "assistant", text: `kell could not reach the engine: ${err.slice(0, 200)}` }])
      setBusy(false); return
    }
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
        if (t === "session") setSessionID(ev.sessionID)
        else if (t === "session.text.delta") appendAssistant(d.delta ?? d.text ?? "")
        else if (t === "session.tool.called" || t === "session.tool.success" || t === "session.tool.failed")
          setSteps((s) => [...s, { kind: "tool", label: d.tool || d.name || "tool", note: t.endsWith("failed") ? "failed" : undefined }])
        else if (t === "session.model.selected" || t === "session.model.updated")
          setSteps((s) => [...s, { kind: "model", label: `${d.providerID ?? "?"}/${d.modelID ?? "?"}` }])
        else if (t === "session.step.ended") {
          if (d.model?.id) setSteps((s) => [...s, { kind: "model", label: `${d.model.providerID}/${d.model.id}`, note: d.cost != null ? `$${Number(d.cost).toFixed(5)}` : undefined }])
          if (d.cost != null) setSpend((c) => c + Number(d.cost))
        }
        else if (t === "error") { appendAssistant(`\n(error: ${ev.error})`); setBusy(false) }
      }
    }
    setBusy(false)
  }

  const undo = async () => {
    const r = await fetch("/api/undo", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory }) })
    const j = await r.json()
    setLines((l) => [...l, { who: "assistant", text: `kell undo:\n${j.output || j.error || "(nothing to undo)"}` }])
  }

  const models = [...new Set(steps.filter((s) => s.kind === "model").map((s) => s.label))]
  const readEngine = engine?.up && !!directory

  return (
    <main className="frame">
      <h1 style={{ padding: "18px 0 2px" }}>kell</h1>
      <div className="modelstrip">
        <span>{sessionID ? `session ${sessionID.slice(0, 12)}…` : "no session"}</span>
        <span className="spend">{spend > 0 ? `spent $${spend.toFixed(4)}` : "no spend yet"}</span>
      </div>
      <div className="shell">
        <aside className="side">
          <h2>Project</h2>
          <p style={{ margin: "10px 0 4px" }}>
            <input value={directory} onChange={(e) => setDirectory(e.target.value)} disabled={!!sessionID}
              placeholder="a folder path to work in" className="mono" style={{ width: "100%", padding: 10, borderRadius: 10, border: "1px solid var(--rule-strong)", background: "#fff" }} />
          </p>
          <button className="btn-ink" onClick={openProject} disabled={!!sessionID || !directory || busy}>open project</button>
          {sessionID && <p style={{ fontSize: 13, marginTop: 8 }}><button className="copy" onClick={() => { setSessionID(null); setLines([]); setSteps([]) }}>reset session</button></p>}
          <h2 style={{ marginTop: 22 }}>Changes</h2>
          <p style={{ fontSize: 14 }}>
            <button className="copy" onClick={undo} disabled={!sessionID}>kell undo</button> — rolls back the last change a cheap model applied.
          </p>
          {engine && !engine.up && <p className="banner" style={{ marginTop: 14 }}>Engine problem{engine.error ? `: ${engine.error}` : ""}</p>}
        </aside>
        <section className="main" style={{ padding: "0 0 0 var(--gap)" }}>
          <h2>Chat</h2>
          <div className="chat">
            <div className="log" ref={logRef}>
              {lines.length === 0 && (
                <p className="banner">Open a project, then say what to do. The cheap model runs the turn and switches to the strong model when the tests say so; tool calls show as chips below.</p>
              )}
              {lines.map((l, i) => (
                <div className={`line ${l.who}`} key={i}>
                  <div className="who">{l.who === "user" ? "you" : "kell"}</div>
                  <div className="bubble">{l.text}</div>
                </div>
              ))}
            </div>
            {steps.length > 0 && (
              <div className="steps">
                {steps.slice(-14).map((s, i) => <span className="step" key={i}><b>{s.kind === "tool" ? "tool" : "model"}</b> {s.label}{s.note ? ` — ${s.note}` : ""}</span>)}
              </div>
            )}
            <div className="composer">
              <form onSubmit={(e) => { e.preventDefault(); send() }}>
                <textarea value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={directory ? "tell kell what to do…" : "open a project first"}
                  disabled={!!engine && !engine.up}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} />
                <button className="btn-ink" disabled={busy || !directory || (engine != null && !engine.up)}>{busy ? "working…" : "send"}</button>
              </form>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}
