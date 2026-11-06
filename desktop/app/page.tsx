"use client"
// The screens, in the website's and console's look (docs/desktop_app_plan.md): project picker; chat with streaming and
// tool calls; the changes panel (per-session diff, Undo via `kell undo`); the model strip (which model did each step, its
// tokens and cost, and whether that model is the configured cheap one); tool suggestion cards (kell's tool router, the same
// call the session's plugin makes); and settings (sign-in, models, engine, "Open console").
import { useCallback, useEffect, useRef, useState } from "react"
import Markdown from "@/components/chat/Markdown"
import SmoothText, { Thinking } from "@/components/chat/SmoothText"
import MessageActions from "@/components/chat/MessageActions"
import ThemeToggle, { ThemeWatcher } from "@/components/ThemeToggle"
import { useSpringScroll } from "@/components/chat/useSpringScroll"
import { KellMark } from "@/components/chat/brand"
import { springVars } from "@/lib/chat/spring"

// The Electron preload's bridge (electron/preload.ts). Absent in a plain browser (`next dev`): sign-in links then open a tab
// and the rooms are a link to the website's chat.
type Rect = { x: number; y: number; width: number; height: number }
type RoomsWho = { id: string; email: string | null } | null
type KellDesktop = {
  openSignIn: (url: string) => void; onSignInDone: (cb: (r: { url: string; ok: boolean }) => void) => () => void
  openAccount: (url: string) => Promise<boolean>; signOutRooms: () => Promise<void>; pickFolder?: () => Promise<string | null>
  rooms: { show: (rect: Rect | null) => void; reload: () => void; theme: (t: "light" | "dark") => void; account: () => Promise<RoomsWho | undefined>; signIn: (provider: "google" | "github") => Promise<boolean>; emailSignIn: () => void }
}
/** A model call refused for want of a website session (lib/websession.ts SIGN_IN_TO_RUN): the page offers the sign-in instead of an error. */
const needsWebSignIn = (e?: string | null) => !!e && /sign in to run models/i.test(e)
const desktop = () => (typeof window !== "undefined" ? (window as unknown as { kellDesktop?: KellDesktop }).kellDesktop : undefined)

// A tool that needs a sign-in comes back from kell's gateway as JSON inside the tool's result:
// {"sign_in_required": true, "server": ..., "link": ...} or {"consent_required": true, ...}. The event carries it as text,
// sometimes escaped once more, so this reads it from the raw event rather than a parsed field.
const signInFrom = (raw: string): { server: string; link: string } | null => {
  if (!/(sign_in_required|consent_required)\\*"\s*:\s*true/.test(raw)) return null
  const link = raw.match(/link\\*"\s*:\s*\\*"(https:[^"\\\s]+)/)?.[1]
  if (!link) return null
  const server = raw.match(/server\\*"\s*:\s*\\*"([^"\\]+)/)?.[1] || "this tool"
  return { server, link }
}

type Line = { who: "user" | "assistant" | "signin" | "plan"; text: string; at?: number; server?: string; state?: "ready" | "open" | "done" | "failed" }
type Step = { kind: "tool" | "model"; label: string; note?: string }
// One row per step (one model round trip). The engine splits it across events: session.step.started names the model (keyed
// by assistantMessageID), session.step.ended carries tokens and cost for the same message. Neither event carries a rung.
type LedgerRow = { id: string; model?: string; providerID?: string; tokens?: string; cost?: number }
type FileDiff = { file: string; patch: string; additions: number; deletions: number; status: "added" | "deleted" | "modified" }
type ToolCard = { name: string; source: "installed" | "registry"; url: string | null; needsSignIn: boolean }
type Settings = {
  signedIn: boolean; account: string | null; subject?: string | null; server: string | null
  lane?: "organization" | "personal" | "own-key" | "none"; gateway?: string | null
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
  const [opening, setOpening] = useState(false)
  // Projects opened before, newest first (this device only), shown in the sidebar instead of the model list.
  const [recents, setRecents] = useState<string[]>([])
  useEffect(() => { try { const r = JSON.parse(window.localStorage.getItem("kell.projects") || "[]"); if (Array.isArray(r)) setRecents(r.filter((x) => typeof x === "string").slice(0, 12)) } catch { /* none */ } }, [])
  const remember = (dir: string) => setRecents((r) => {
    const next = [dir, ...r.filter((x) => x.toLowerCase() !== dir.toLowerCase())].slice(0, 12)
    try { window.localStorage.setItem("kell.projects", JSON.stringify(next)) } catch { /* storage blocked */ }
    return next
  })
  const forget = (dir: string) => setRecents((r) => {
    const next = r.filter((x) => x !== dir)
    try { window.localStorage.setItem("kell.projects", JSON.stringify(next)) } catch { /* storage blocked */ }
    return next
  })
  // The two side columns can be dragged wider or narrower; the widths are kept on this device.
  const [widths, setWidths] = useState({ left: 260, right: 340 })
  useEffect(() => { try { const w = JSON.parse(window.localStorage.getItem("kell.layout") || "null"); if (w && typeof w.left === "number" && typeof w.right === "number") setWidths(w) } catch { /* default */ } }, [])
  const dragColumn = (side: "left" | "right") => (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const start = widths[side]
    let last = widths
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - startX
      const w = Math.round(side === "left" ? Math.min(480, Math.max(190, start + dx)) : Math.min(640, Math.max(240, start - dx)))
      last = { ...widths, [side]: w }
      setWidths(last)
    }
    const up = () => {
      el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up)
      try { window.localStorage.setItem("kell.layout", JSON.stringify(last)) } catch { /* storage blocked */ }
    }
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up)
  }
  const [accountOpen, setAccountOpen] = useState(false)
  const [lines, setLines] = useState<Line[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [sessionID, setSessionID] = useState<string | null>(null)
  const [spend, setSpend] = useState(0)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const [big, setBig] = useState(false)                  // the message box: bigger / smaller, like the website chat
  const textRef = useRef<HTMLTextAreaElement>(null)
  const [files, setFiles] = useState<FileDiff[]>([])
  const [filesNote, setFilesNote] = useState("")
  const [confirm, setConfirm] = useState("")
  const [pending, setPending] = useState(0)
  const [cards, setCards] = useState<ToolCard[]>([])
  const [cardsNote, setCardsNote] = useState("")
  const [settings, setSettings] = useState<Settings | null>(null)
  const [consoleNote, setConsoleNote] = useState("")
  const [signinNote, setSigninNote] = useState("")
  // The coding lane, or the team rooms (the website's chat in a view over the placeholder below; electron/rooms.ts).
  const [pane, setPane] = useState<"code" | "rooms">("code")
  const [roomsWho, setRoomsWho] = useState<RoomsWho | undefined>(undefined)   // undefined: not read yet
  const roomsRef = useRef<HTMLDivElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const logContentRef = useRef<HTMLDivElement>(null)
  // The same spring scroll as the website chat: follows new text only while you are at the bottom, rubber-bands at the edges.
  const { follow, scrollToEnd } = useSpringScroll(logRef, logContentRef, sessionID ?? "")
  const busyRef = useRef(false)                       // a late poll must not overwrite a turn that started after it
  // Messages sent while kell is working wait here and go out one at a time, in order, as each turn ends (× drops one).
  const [queued, setQueued] = useState<string[]>([])

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

  // Open project: with no folder typed, the system's folder picker first (in the app). Says what it is doing and what happened:
  // it used to change nothing on screen when it worked, so it looked broken (2026-11-06).
  const openProject = async (picked?: string) => {
    let dir = picked ?? directory
    if (!dir.trim()) {
      const chosen = await desktop()?.pickFolder?.()
      if (!chosen) return
      dir = chosen; setDirectory(chosen)
    }
    setEngine(null); setOpening(true)
    window.localStorage.setItem("kell.directory", dir)
    let j: { up?: boolean; error?: string }
    try {
      const r = await fetch("/api/engine", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory: dir }) })
      j = await r.json()
    } catch (err) { j = { up: false, error: err instanceof Error ? err.message : String(err) } }
    finally { setOpening(false) }
    setEngine(j.up ? { up: true } : { up: false, error: j.error || "the engine did not start" })
    if (j.up) remember(dir)
    if (j.up) { setQueued([]); setLines([]); setSteps([]); setLedger([]); setSessionID(null); setCards([]); setCardsNote(""); setSpend(0); loadChanges(null) }
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
    if (!body.trim() || !directory) return
    if (busy) {                                              // still working: keep it for when this turn ends
      setQueued((q) => [...q, body])
      if (again === undefined) setText("")
      return
    }
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
      setBusy(false); busyRef.current = false; return
    }
    let sid = sessionID
    let buffer = ""
    let ended = false
    const before = lines.length
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
        else if (t === "session.tool.called" || t === "session.tool.success" || t === "session.tool.failed") {
          setSteps((s) => [...s, { kind: "tool", label: d.tool || d.name || "tool", note: t.endsWith("failed") ? "failed" : undefined }])
          const need = t === "session.tool.called" ? null : signInFrom(JSON.stringify(d))
          if (need) setLines((l) => l.some((x) => x.who === "signin" && x.text === need.link) ? l
            : [...l, { who: "signin", text: need.link, server: need.server, state: "ready", at: Date.now() }])
        }
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
        else if (t === "need_plan") { setLines((l) => [...l, { who: "plan", text: "", at: Date.now() }]); setBusy(false) }
        else if (t === "error") {
          if (needsWebSignIn(ev.error)) { setEngine({ up: false, error: String(ev.error) }); appendAssistant("Sign in to run models: the buttons are on the left.") }
          else appendAssistant(`\n(error: ${ev.error})`)
          setBusy(false)
        }
        else if (t === "done") ended = true
      }
    }
    // A turn that ends with nothing said is never silent: say so (2026-11-06: a failed model call showed an empty turn).
    setLines((l) => l.slice(before + 1).some((x) => x.who !== "user") ? l
      : [...l, { who: "assistant", text: ended ? "kell got no answer from the model for this. Try again, or check Settings > Models."
                                               : "The connection to the engine ended before an answer came. Try again.", at: Date.now() }])
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

  // One sign-in for the whole app: `kell login` (which writes ~/.stealthlab/config.json for the coding lane) prints its
  // link, and the app opens it in the rooms' own cookie store, so signing in on kell's consent page signs the rooms in too.
  // The page then polls settings until the token is there.
  const signIn = async (again = false) => {
    setSigninNote("Your browser opens to sign in. Finish there, then come back here.")
    const started = await fetch("/api/signin", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ again }) })
      .then((r) => r.json()).catch(() => ({ error: "could not start kell login" }))
    if (started?.error) { setSigninNote(String(started.error)); return }
    if (started?.already) { setSigninNote("Already signed in."); return }
    const d = desktop()
    if (d) {
      const ok = await d.openAccount(String(started.url))
      if (!ok) { setSigninNote("Could not open your browser. Try again."); return }
    } else {
      window.open(String(started.url), "_blank", "noopener")
    }
    const until = Date.now() + 300_000
    const tick = async () => {
      const s = await fetch("/api/settings").then((r) => r.json()).catch(() => null)
      if (s) setSettings(s)
      if (s?.signedIn && (!again || s.subject)) { setSigninNote(""); setPane("rooms"); return }
      if (Date.now() > until) { setSigninNote("Still not signed in. Try again."); return }
      setTimeout(tick, 1500)
    }
    setTimeout(tick, 500)
  }

  // The rooms' website session is the app's own (electron/rooms.ts::signInRooms): its own PKCE sign-in in the default browser,
  // so only a one-time code crosses and no token is copied from anywhere.
  const connectRooms = async (provider: "google" | "github") => {
    const d = desktop()
    if (!d) return
    setSigninNote("Finish signing in in your browser, then come back here.")
    const ok = await d.rooms.signIn(provider)
    setSigninNote(ok ? "" : "The rooms were not signed in. Try again.")
    if (ok) setRoomsWho(undefined)
  }

  // Sign out of everything: the coding lane's token and the rooms' session (and its cached copies) together, so the app
  // is never half one account and half another.
  const signOut = async () => {
    setSigninNote("Signing out…")
    const r = await fetch("/api/signin", { method: "DELETE" }).then((x) => x.json()).catch(() => ({ error: "could not run kell logout" }))
    await desktop()?.signOutRooms()
    setRoomsWho(null)
    const s = await fetch("/api/settings").then((x) => x.json()).catch(() => null)
    if (s) setSettings(s)
    setSigninNote(r?.error ? String(r.error) : "Signed out.")
  }

  // One click: the sign-in opens in the app's own window (electron/signin.ts) and closes itself when the tool provider
  // sends the person back to kell; then the request that needed the tool runs again by itself.
  const openToolSignIn = (link: string) => {
    setLines((l) => l.map((x) => (x.who === "signin" && x.text === link ? { ...x, state: "open" } : x)))
    const d = desktop()
    if (d) d.openSignIn(link)
    else window.open(link, "_blank", "noopener")
  }
  const [resume, setResume] = useState<string | null>(null)
  useEffect(() => {
    const d = desktop()
    if (!d) return
    return d.onSignInDone(({ ok }) => {
      let server = ""
      setLines((l) => l.map((x) => {
        if (x.who !== "signin" || x.state !== "open") return x
        server = x.server || ""
        return { ...x, state: ok ? "done" : "failed" }
      }))
      if (ok) setResume(`I signed in to ${server || "the tool"}. Carry on with what I asked.`)
    })
  }, [])
  useEffect(() => {
    if (resume && !busy) { const t = resume; setResume(null); void send(t) }
    // send is recreated each render; the resume only needs to fire once the turn has ended
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resume, busy])
  // The next waiting message goes out once the turn has ended (a sign-in resume goes first: it finishes what was asked).
  useEffect(() => {
    if (busy || resume || !queued.length) return
    const [next, ...rest] = queued
    setQueued(rest)
    void send(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queued, busy, resume])

  // The rooms view follows the placeholder's rectangle while the Rooms pane shows, and hides on the coding pane.
  const roomsOn = pane === "rooms" && !!settings?.signedIn
  useEffect(() => {
    const d = desktop()
    const el = roomsRef.current
    if (!d) return
    if (!roomsOn || !el) { d.rooms.show(null); return }
    const place = () => { const r = el.getBoundingClientRect(); d.rooms.show({ x: r.left, y: r.top, width: r.width, height: r.height }) }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(el)
    window.addEventListener("resize", place)
    return () => { ro.disconnect(); window.removeEventListener("resize", place); d.rooms.show(null) }
  }, [roomsOn])

  // Same account in both lanes: read who the rooms are signed in as while they show (someone can sign in as another person
  // inside them), and keep them in the app's light/dark choice.
  useEffect(() => {
    const d = desktop()
    if (!d || !roomsOn) return
    let live = true
    const read = () => d.rooms.account().then((w) => { if (live) setRoomsWho(w) }).catch(() => undefined)
    void read()
    const t = setInterval(read, 4000)
    const theme = () => d.rooms.theme(document.documentElement.dataset.theme === "dark" ? "dark" : "light")
    theme()
    const mo = new MutationObserver(theme)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] })
    return () => { live = false; clearInterval(t); mo.disconnect() }
  }, [roomsOn])
  const otherAccount = !!(settings?.signedIn && settings.subject && roomsWho && roomsWho.id !== settings.subject)

  const isCheap = (r: LedgerRow) => !!settings?.models.cheap && `${r.providerID}/${r.model}` === settings.models.cheap
  const readEngine = engine?.up && !!directory
  const words = text.trim() ? text.trim().split(/\s+/).length : 0

  const project = directory ? directory.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || directory : "No project"
  const time = (at?: number) => (at ? new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null)

  return (
    <section className="dg-page dk-page" style={springVars() as React.CSSProperties}>
      <ThemeWatcher />
      <nav className="dk-switch" aria-label="Code or rooms">
        <div role="tablist">
          <button type="button" role="tab" aria-selected={pane === "code"} onClick={() => setPane("code")}>Code</button>
          <button type="button" role="tab" aria-selected={pane === "rooms"} onClick={() => setPane("rooms")}>Rooms</button>
        </div>
        <span className="dk-who">{settings?.signedIn ? <>Signed in as <b>{settings.account || "your kell account"}</b></> : "Not signed in"}</span>
      </nav>
      {otherAccount && (
        <div className="dk-err dk-mismatch" role="alert">
          <p style={{ margin: 0 }}>The rooms are signed in as <b>{roomsWho?.email || "another account"}</b>, the rest of the app as <b>{settings?.account || "your kell account"}</b>. Sign out of both, then sign in once with the account you want.</p>
          <div className="dk-row" style={{ margin: 0 }}><button type="button" className="btn-ink" onClick={() => void signOut()}>Sign out of both</button></div>
        </div>
      )}
      {roomsOn && roomsWho === null && (
        <div className="dk-err dk-mismatch" role="status">
          <p style={{ margin: 0 }}>You are signed in as <b>{settings?.account || "your kell account"}</b> for coding. The rooms need their own sign-in, with the same account. Choose how you signed in; it opens in your browser and nothing is copied.</p>
          <div className="dk-row" style={{ margin: 0 }}>
            <button type="button" className="btn-ink" onClick={() => void connectRooms("google")}>Continue with Google</button>
            <button type="button" className="btn-ink" onClick={() => void connectRooms("github")}>Continue with GitHub</button>
            <button type="button" className="btn-ink" onClick={() => desktop()?.rooms.emailSignIn()}>Use email and password</button>
          </div>
          {signinNote && <p className="dk-note">{signinNote}</p>}
        </div>
      )}
      {pane === "rooms" && (
        <div className="dk-rooms" ref={roomsRef} aria-label="Team rooms">
          {(!settings?.signedIn || !desktop()) && (
            <div className="dk-rooms-in">
              <b>Your team&rsquo;s rooms</b>
              <p>People, the agent of your choice and your pal&rsquo;s agents, in one room. Sign in once: the same account runs your coding agent here and your rooms.</p>
              {!settings?.signedIn && <div className="dk-row" style={{ justifyContent: "center" }}><button type="button" className="btn-ink" onClick={() => void signIn()}>Sign in</button></div>}
              {signinNote && <p className="dk-note">{signinNote}</p>}
              {!desktop() && <p className="dk-note"><a href="https://www.kell.org.in/chat" target="_blank" rel="noopener">Open the rooms on the website</a></p>}
            </div>
          )}
        </div>
      )}
      <div className="dg-shell" hidden={pane !== "code"}
        style={{ "--dk-left": `${widths.left}px`, "--dk-right": `${widths.right}px` } as React.CSSProperties}>
        <div className="dk-resize" data-side="left" role="separator" aria-orientation="vertical" aria-label="Drag to resize the project list" onPointerDown={dragColumn("left")} />
        <div className="dk-resize" data-side="right" role="separator" aria-orientation="vertical" aria-label="Drag to resize the changes panel" onPointerDown={dragColumn("right")} />
        <aside className="dg-side" aria-label="Project, models and account">
          <div className="dg-brand">
            <img className="wm-light" src="/kel-wordmark.png" alt="keळ" style={{ height: 38, width: "auto" }} />
            <img className="wm-dark" src="/kel-wordmark-dark.png" alt="" aria-hidden="true" style={{ height: 38, width: "auto" }} />
          </div>

          <h2>Project</h2>
          <div style={{ margin: "0 6px" }}>
            <input value={directory} onChange={(e) => setDirectory(e.target.value)} disabled={!!sessionID}
              placeholder="a folder path to work in" className="dk-field mono" aria-label="Project folder" />
          </div>
          <div className="dk-row">
            <button type="button" className="btn-ink" onClick={() => void openProject()} disabled={!!sessionID || busy || opening}>{opening ? "Opening…" : "Open project"}</button>
            {desktop()?.pickFolder && !sessionID && (
              <button type="button" className="btn-line" disabled={busy || opening}
                onClick={async () => { const d = await desktop()?.pickFolder?.(); if (d) { setDirectory(d); void openProject(d) } }}>Choose folder</button>
            )}
            {sessionID && (
              <button type="button" className="btn-line" onClick={() => { setSessionID(null); setLines([]); setSteps([]); setLedger([]); loadChanges(null) }}>New session</button>
            )}
          </div>
          {engine && !engine.up && (engine.error || sessionID) && !needsWebSignIn(engine.error) && <p className="dk-err" role="status">Engine problem{engine.error ? `: ${engine.error}` : ""}</p>}
          {needsWebSignIn(engine?.error) && desktop() && (
            // Models on your own plan run on the website session (the same sign-in as the rooms): one click, in your browser, nothing copied.
            <div className="dk-err" role="status">
              <p style={{ margin: "0 0 6px" }}>Sign in once more to run models on your kell plan (the same sign-in the rooms use). It opens in your browser; nothing is copied.</p>
              <div className="dk-row" style={{ margin: 0 }}>
                <button type="button" className="btn-ink" onClick={async () => { await connectRooms("google"); void openProject() }}>Continue with Google</button>
                <button type="button" className="btn-ink" onClick={async () => { await connectRooms("github"); void openProject() }}>Continue with GitHub</button>
                <button type="button" className="btn-line" onClick={() => { setPane("rooms"); desktop()?.rooms.emailSignIn() }}>Email and password</button>
              </div>
              {signinNote && <p className="dk-note">{signinNote}</p>}
            </div>
          )}
          {engine?.up && !sessionID && <p className="dk-note" role="status">Project open: {project}. Type what you want done below.</p>}

          <h2>Projects</h2>
          {recents.length === 0 ? <p className="dk-note">Projects you open show here.</p> : (
            <ul className="dk-recents" aria-label="Recent projects">
              {recents.map((d) => {
                const name = d.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || d
                const here = !!directory && d.toLowerCase() === directory.toLowerCase()
                return (
                  <li key={d} data-here={here || undefined}>
                    <button type="button" className="dk-recent" title={d} disabled={busy || opening || (here && !!engine?.up)}
                      onClick={() => { setSessionID(null); setDirectory(d); void openProject(d) }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
                      <span className="nm">{name}</span>
                    </button>
                    <button type="button" className="dk-recent-x" aria-label={`Remove ${name} from the list`} title="Remove from the list" onClick={() => forget(d)}>×</button>
                  </li>
                )
              })}
            </ul>
          )}

          <div className="dk-foot">
            {signinNote && <p className="dk-note" role="status">{signinNote}</p>}
            {consoleNote && <p className="dk-note">{consoleNote}</p>}
            {accountOpen && settings && (
              <div className="dk-account" role="dialog" aria-label="Account">
                <p><b>{settings.signedIn ? settings.account || "your kell account" : "Not signed in"}</b></p>
                <p className="dk-note">
                  {settings.lane === "organization" ? <>Paid through your organization ({settings.gateway || "kell"}).</>
                    : settings.lane === "personal" ? "Paid from your own kell plan."
                    : settings.lane === "own-key" ? "Your own provider key." : "Sign in to use your kell plan."}
                </p>
                {(settings.models.cheap || settings.models.strong) && (
                  <p className="dk-note">Models: {[settings.models.cheap, settings.models.strong].filter(Boolean).map((m) => String(m).split("/").pop()).join(", ")}</p>
                )}
                {!settings.engine.exe && <p className="dk-err">The coding engine is missing: run `kell setup --client opencode`.</p>}
              </div>
            )}
            <div className="dk-iconrow">
              {settings && !settings.signedIn ? (
                <button type="button" className="btn-ink" onClick={() => void signIn()}>Sign in</button>
              ) : (
                <button type="button" className="dk-icon" aria-label="Account" title={settings?.account ? `Signed in as ${settings.account}` : "Account"}
                  aria-expanded={accountOpen} onClick={() => setAccountOpen((o) => !o)}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
                </button>
              )}
              <button type="button" className="dk-icon" aria-label="Open console" title="Open console" onClick={openConsole}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M13 15h4" /></svg>
              </button>
              {settings?.signedIn && (
                <button type="button" className="dk-icon" aria-label="Sign out" title="Sign out" onClick={() => void signOut()}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></svg>
                </button>
              )}
            </div>
          </div>
        </aside>

        <section className="dg-main" aria-label={`Chat in ${project}`}>
          <header className="dg-head">
            <h1>{project}</h1>
            <div className="dk-chips">
              {spend > 0 && <span className="dk-spend">${spend.toFixed(4)} this session</span>}
            </div>
            <ThemeToggle className="dg-theme" />
          </header>

          <div className="dg-scroll">
            <div className="dg-viewport" ref={logRef} aria-label="Messages">
              <div className="dg-content" ref={logContentRef}>
                {lines.length === 0 && (
                  <p className="dg-empty">Open a project, then say what to do. A low-cost model starts each task and the stronger one takes over when the tests say so. Tools that need a sign-in open in a small window here.</p>
                )}
                {lines.map((l, i) => {
                  if (l.who === "plan") {
                    return (
                      <div className="dg-run" data-agent="true" key={i}>
                        <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                        <div className="dg-col">
                          <div className="dg-meta"><b>Kell</b><span className="dg-badge">plan</span><time>{time(l.at)}</time></div>
                          <div className="dk-signin">
                            <p>You need a plan to run models. Starter, Pro and Plus include usage, and you can add credit any time.</p>
                            <div className="dk-row">
                              <a className="btn-ink" href="https://www.kell.org.in/pricing" target="_blank" rel="noopener">See plans</a>
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  }
                  if (l.who === "signin") {
                    return (
                      <div className="dg-run" data-agent="true" key={i}>
                        <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                        <div className="dg-col">
                          <div className="dg-meta"><b>Kell</b><span className="dg-badge">sign-in</span><time>{time(l.at)}</time></div>
                          <div className="dk-signin" data-state={l.state}>
                            <p>{l.state === "done" ? <>Signed in to <b>{l.server}</b>. Carrying on.</>
                              : l.state === "failed" ? <>The sign-in to <b>{l.server}</b> was closed before it finished.</>
                                : l.state === "open" ? <>Finish signing in to <b>{l.server}</b> in the window that opened.</>
                                  : <><b>{l.server}</b> needs you to sign in once. Choose your account in the window that opens; kell keeps the sign-in for next time.</>}</p>
                            {l.state !== "done" && (
                              <div className="dk-row">
                                <button type="button" className="btn-ink" onClick={() => openToolSignIn(l.text)}>{l.state === "failed" ? "Try again" : `Sign in to ${l.server}`}</button>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  }
                  const live = busy && l.who === "assistant" && i === lines.length - 1   // the reply still arriving
                  const mine = l.who === "user"
                  return (
                    <div className={`dg-run${mine ? " dk-you" : ""}`} data-agent={!mine} key={i}>
                      {mine
                        ? <span className="dg-avatar" data-kind="human" aria-hidden="true">You</span>
                        : <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>}
                      <div className="dg-col">
                        <div className="dg-meta"><b>{mine ? "You" : "Kell"}</b>{!mine && <span className="dg-badge">agent</span>}<time>{time(l.at)}</time></div>
                        <div className="dg-msg">
                          <div className="dg-body">{mine ? <p>{l.text}</p> : live ? <SmoothText text={l.text} markdown /> : <Markdown text={l.text} />}</div>
                          {l.text && !live ? (
                            <MessageActions copyText={l.text} retryText={mine ? l.text : lastUserBefore(i)}
                              onCopy={copyText} onRetry={(t) => void send(t)} retryLabel={mine ? "Send this again" : "Ask again"} />
                          ) : null}
                        </div>
                      </div>
                    </div>
                  )
                })}
                {busy && lines.at(-1)?.who !== "assistant" && (
                  <div className="dg-run" data-agent="true">
                    <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                    <div className="dg-col">
                      <div className="dg-meta"><b>Kell</b><span className="dg-badge">agent</span></div>
                      <div className="dg-body" role="status" aria-label="kell is working"><Thinking /></div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {steps.length > 0 && (
            <div className="dk-steps" aria-label="Tool calls">
              {steps.slice(-10).map((s, i) => <span className="dk-step" data-failed={!!s.note || undefined} key={i}><b>{s.label}</b>{s.note ? ` · ${s.note}` : ""}</span>)}
            </div>
          )}
          {queued.map((q, i) => (
            <div key={i} className="dg-queued small" role="status">
              <span className="dim">Waiting for kell:</span> <span className="dg-queued-text">{q}</span>
              <button type="button" aria-label="Don't send this one" title="Don't send this one" onClick={() => setQueued((x) => x.filter((_, k) => k !== i))}>×</button>
            </div>))}
          <div className="dg-typing" role="status" aria-live="polite">
            {busy && <><span className="dg-pulse" aria-hidden="true" /> Kell is working</>}
          </div>
          <form className="dg-composer" onSubmit={(e) => { e.preventDefault(); send() }}>
            <textarea ref={textRef} rows={1} data-big={big || undefined} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={directory ? "Tell kell what to do…" : "Open a project first"}
              disabled={!!engine && !engine.up}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} />
            <div className="dg-composer-meta">
              {text.trim() && <span className="dg-count" aria-live="off">{words} {words === 1 ? "word" : "words"}</span>}
              <button type="button" className="dg-expand" onClick={() => { setBig((b) => !b); textRef.current?.focus() }} aria-pressed={big}
                aria-label={big ? "Make the message box smaller" : "Make the message box bigger"} title={big ? "Smaller" : "Bigger"}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{big ? <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" /> : <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />}</svg>
              </button>
            </div>
            <button type="submit" disabled={!text.trim() || !directory || (engine != null && !engine.up)} title={busy ? "kell is working: this waits and goes out when it finishes" : undefined}>{busy ? "Queue" : "Send"}</button>
          </form>
        </section>

        <aside className="dk-panel" aria-label="Changes, steps and tools">
          <h2>Changes</h2>
          <div className="dk-row">
            <button type="button" className="btn-line" onClick={() => loadChanges(sessionID)} disabled={!readEngine}>Refresh</button>
            <button type="button" className="btn-line" onClick={() => undo()} disabled={!directory || busy || (!!sessionID && pending === 0)}>Undo</button>
          </div>
          {confirm && (
            <div className="dk-err">
              <p style={{ margin: "0 0 6px" }}>{confirm}. Undo would drop that.</p>
              <div className="dk-row" style={{ margin: 0 }}>
                <button type="button" className="btn-ink" onClick={() => undo(true)}>Undo anyway</button>
                <button type="button" className="btn-line" onClick={() => setConfirm("")}>Keep it</button>
              </div>
            </div>
          )}
          {filesNote && !files.length && <p className="dk-note">{filesNote}</p>}
          <div className="dk-files">
            {files.map((f) => (
              <details className="dk-file" key={f.file} open={files.length === 1}>
                <summary>
                  <span className="path">{f.file}</span>
                  <span className="stat"><span className="add">+{f.additions}</span> <span className="del">−{f.deletions}</span></span>
                </summary>
                <div className="dk-diff">{patchLines(f.patch).map((p, i) => <div className={p.cls} key={i}>{p.text || " "}</div>)}</div>
              </details>
            ))}
          </div>

          <h2>Model steps</h2>
          {ledger.length === 0 ? <p className="dk-note">Each model step shows here with its tokens and cost.</p> : (
            <div className="dk-ledger">
              {ledger.map((r) => (
                <div className="row" key={r.id}>
                  <span title={r.tokens || ""}><span className="dk-rung" data-r={isCheap(r) ? "cheap" : "strong"}>{isCheap(r) ? "cheap" : "strong"}</span><b>{r.model || "picking model…"}</b></span>
                  <span>{r.cost != null ? `$${r.cost.toFixed(5)}` : "—"}</span>
                </div>
              ))}
            </div>
          )}

          <h2>Tools for this request</h2>
          {cardsNote && <p className="dk-note">{cardsNote}</p>}
          <div className="dk-cards">
            {cards.map((c) => (
              <div className="dk-card" key={c.name}>
                <span className="name" title={`${c.name}${c.url ? ` — ${c.url}` : ""}`}>{c.name}</span>
                <span className="dg-badge" data-kind="tab">{c.source === "installed" ? "installed" : "available"}</span>
                <span className="meta">{c.source === "installed" ? "installed here" : "kell can run it for you"}{c.needsSignIn ? " · asks you to sign in once" : ""}</span>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </section>
  )
}
