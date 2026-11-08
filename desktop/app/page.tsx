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
  openAccount: (url: string) => Promise<boolean>; signOutRooms: () => Promise<void>; pickFolder?: () => Promise<string | null>; onMenu?: (cb: (what: string) => void) => () => void
  window?: { minimize: () => void; toggleMaximize: () => void; close: () => void }
  rooms: { show: (rect: Rect | null) => void; reload: () => void; theme: (t: "light" | "dark") => void; account: () => Promise<RoomsWho | undefined>; signIn: (provider: "google" | "github") => Promise<boolean>; emailSignIn: () => void }
}
/** A tool call's subject in a few words: the path, pattern, command or query it was given. */
const hintOf = (input: any): string | undefined => {
  if (!input || typeof input !== "object") return undefined
  const v = input.command ?? input.pattern ?? input.query ?? input.path ?? input.filePath ?? input.file ?? input.url ?? input.description
  if (typeof v !== "string" || !v.trim()) return undefined
  const one = v.replace(/\s+/g, " ").trim()
  const short = /[\\/]/.test(one) && !/\s/.test(one) ? one.split(/[\\/]/).filter(Boolean).slice(-2).join("/") : one
  return short.length > 60 ? short.slice(0, 57) + "…" : short
}

/** When a session was last used, short: the time today, else the day. */
const when = (ms: number) => {
  const d = new Date(ms), now = new Date()
  return d.toDateString() === now.toDateString()
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "numeric", month: "short" })
}

/** The Help menu: the site's pages that answer what people ask in the app. */
const HELP_LINKS: [string, string][] = [
  ["How kell works", "https://www.kell.org.in/docs"],
  ["Agents in rooms", "https://www.kell.org.in/docs#agents"],
  ["Teams", "https://www.kell.org.in/docs#teams"],
  ["Plans and credits", "https://www.kell.org.in/pricing"],
  ["Your account and billing", "https://www.kell.org.in/account/billing"],
  ["Contact us", "https://www.kell.org.in/contact"],
]

/** One list of changed files, each opening to its diff. A new file listed by name only says so. */
function FileList({ files, openOne = false }: { files: FileDiff[]; openOne?: boolean }) {
  return (
    <div className="dk-files">
      {files.map((f) => (
        <details className="dk-file" key={f.file} open={openOne && files.length === 1}>
          <summary>
            <span className="path">{f.file}</span>
            <span className="stat">{f.status === "added" && !f.patch ? <span className="add">new</span>
              : <><span className="add">+{f.additions}</span> <span className="del">−{f.deletions}</span></>}</span>
          </summary>
          {f.patch ? <div className="dk-diff">{patchLines(f.patch).map((p, i) => <div className={p.cls} key={i}>{p.text || " "}</div>)}</div>
            : <p className="dk-note" style={{ margin: "6px 8px" }}>A new file, not yet added to git.</p>}
        </details>
      ))}
    </div>
  )
}

type TreeEntry = { name: string; path: string; dir: boolean }

/** One folder in the sidebar's file tree. Each folder asks the app for its own entries when it is first opened
 *  (app/api/files), so the tree costs nothing until you look at it; the root opens on mount. */
function TreeLevel({ directory, rel, depth, onOpen, selected }: {
  directory: string; rel: string; depth: number; onOpen: (p: string) => void; selected?: string
}) {
  const [entries, setEntries] = useState<TreeEntry[] | null>(null)
  const [more, setMore] = useState(0)
  const [err, setErr] = useState("")
  useEffect(() => {
    let live = true
    setEntries(null)
    fetch(`/api/files?directory=${encodeURIComponent(directory)}&dir=${encodeURIComponent(rel)}`)
      .then((r) => r.json())
      .then((j) => {
        if (!live) return
        if (j.error) { setErr(j.error); return }
        setEntries(Array.isArray(j.entries) ? j.entries : []); setMore(Number(j.more) || 0); setErr("")
      })
      .catch((e) => { if (live) setErr(String(e?.message || e)) })
    return () => { live = false }
  }, [directory, rel])

  if (err) return <p className="dk-note" style={{ paddingLeft: 8 + depth * 12 }}>{err}</p>
  if (!entries) return <p className="dk-note" style={{ paddingLeft: 8 + depth * 12 }}>Reading…</p>
  if (!entries.length) return depth === 0 ? <p className="dk-note">No files</p> : null
  return (
    <ul className="dk-tree-level" role={depth === 0 ? "tree" : "group"} aria-label={depth === 0 ? "Project files" : undefined}>
      {entries.map((e) => e.dir
        ? <TreeFolder key={e.path} entry={e} directory={directory} depth={depth} onOpen={onOpen} selected={selected} />
        : (
          <li key={e.path} role="treeitem">
            <button type="button" className="dk-tree-row" data-file data-here={selected === e.path || undefined}
              style={{ paddingLeft: 8 + depth * 12 }} title={e.path} onClick={() => onOpen(e.path)}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>
              <span className="nm">{e.name}</span>
            </button>
          </li>
        ))}
      {more > 0 && <li><p className="dk-note" style={{ paddingLeft: 8 + depth * 12 }}>and {more} more</p></li>}
    </ul>
  )
}

/** A folder row: the chevron turns (micro spring) and the children mount only once it is open. */
function TreeFolder({ entry, directory, depth, onOpen, selected }: {
  entry: TreeEntry; directory: string; depth: number; onOpen: (p: string) => void; selected?: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <li role="treeitem" aria-expanded={open}>
      <button type="button" className="dk-tree-row" data-dir style={{ paddingLeft: 8 + depth * 12 }}
        aria-expanded={open} title={entry.path} onClick={() => setOpen((o) => !o)}>
        <svg className="chev" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
        <span className="nm">{entry.name}</span>
      </button>
      {open && <TreeLevel directory={directory} rel={entry.path} depth={depth + 1} onOpen={onOpen} selected={selected} />}
    </li>
  )
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

type Line = { who: "user" | "assistant" | "signin" | "plan" | "permission"; text: string; at?: number; server?: string
              state?: "ready" | "open" | "done" | "failed"; sessionID?: string; requestID?: string; answer?: string }
type Step = { kind: "tool" | "model"; label: string; note?: string; id?: string; state?: "running" | "done" | "failed"; hint?: string }
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
    try {
      window.localStorage.setItem("kell.projects", JSON.stringify(next))
      const hidden = JSON.parse(window.localStorage.getItem("kell.projects.hidden") || "[]")
      if (Array.isArray(hidden) && hidden.includes(dir.toLowerCase()))       // opened again: it may come back from the engine's list too
        window.localStorage.setItem("kell.projects.hidden", JSON.stringify(hidden.filter((x) => x !== dir.toLowerCase())))
    } catch { /* storage blocked */ }
    return next
  })
  // The folders the engine has worked in fill the list after the ones opened here (2026-11-07: the list started empty after a
  // restart), except ones taken off it with ×.
  const mergeProjects = (dirs: string[]) => setRecents((r) => {
    let hidden: string[] = []
    try { const h = JSON.parse(window.localStorage.getItem("kell.projects.hidden") || "[]"); if (Array.isArray(h)) hidden = h } catch { /* none */ }
    const have = new Set(r.map((x) => x.toLowerCase()))
    const extra = dirs.filter((d) => !have.has(d.toLowerCase()) && !hidden.includes(d.toLowerCase()))
    if (!extra.length) return r
    const next = [...r, ...extra].slice(0, 12)
    try { window.localStorage.setItem("kell.projects", JSON.stringify(next)) } catch { /* storage blocked */ }
    return next
  })
  // × also keeps a project off the list when the engine's own project list is merged in (mergeProjects).
  const forget = (dir: string) => setRecents((r) => {
    const next = r.filter((x) => x !== dir)
    try {
      window.localStorage.setItem("kell.projects", JSON.stringify(next))
      const hidden = JSON.parse(window.localStorage.getItem("kell.projects.hidden") || "[]")
      window.localStorage.setItem("kell.projects.hidden", JSON.stringify([...new Set([dir.toLowerCase(), ...(Array.isArray(hidden) ? hidden : [])])].slice(0, 100)))
    } catch { /* storage blocked */ }
    return next
  })
  // The two side columns can be dragged wider or narrower, and either one can be collapsed; the widths and which
  // sidebars are open are kept on this device (kell.layout / kell.sidebars).
  const [widths, setWidths] = useState({ left: 260, right: 340 })
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  useEffect(() => {
    try {
      const w = JSON.parse(window.localStorage.getItem("kell.layout") || "null")
      if (w && typeof w.left === "number" && typeof w.right === "number") setWidths(w)
      const s = JSON.parse(window.localStorage.getItem("kell.sidebars") || "null")
      if (s && typeof s.left === "boolean") setLeftOpen(s.left)
      if (s && typeof s.right === "boolean") setRightOpen(s.right)
    } catch { /* default */ }
  }, [])
  const toggleSidebar = (side: "left" | "right") => {
    const next = { left: side === "left" ? !leftOpen : leftOpen, right: side === "right" ? !rightOpen : rightOpen }
    setLeftOpen(next.left); setRightOpen(next.right)
    try { window.localStorage.setItem("kell.sidebars", JSON.stringify(next)) } catch { /* storage blocked */ }
  }
  const showSidebar = (side: "left" | "right") => {
    const next = { left: side === "left" ? true : leftOpen, right: side === "right" ? true : rightOpen }
    setLeftOpen(next.left); setRightOpen(next.right)
    try { window.localStorage.setItem("kell.sidebars", JSON.stringify(next)) } catch { /* storage blocked */ }
  }
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
  const [helpOpen, setHelpOpen] = useState(false)
  // File > Open folder and File > New session from the app's menu bar (electron/menu.ts).
  const menuRef = useRef<(what: string) => void>(() => {})
  menuRef.current = (what) => {
    if (what === "open-folder" && !busy && !opening) void (async () => { const d = await desktop()?.pickFolder?.(); if (d) { setSessionID(null); setDirectory(d); void openProject(d) } })()
    if (what === "new-session" && !busy) { setSessionID(null); setLines([]); setSteps([]); setLedger([]); loadChanges(null) }
  }
  useEffect(() => desktop()?.onMenu?.((w) => menuRef.current(w)), [])
  // The Help menu closes on a click anywhere else or on Escape, not only on its own button.
  const helpRef = useRef<HTMLSpanElement | null>(null)
  useEffect(() => {
    if (!helpOpen) return
    const away = (e: PointerEvent) => { if (!helpRef.current?.contains(e.target as Node)) setHelpOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setHelpOpen(false) }
    document.addEventListener("pointerdown", away)
    document.addEventListener("keydown", esc)
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc) }
  }, [helpOpen])
  const [lines, setLines] = useState<Line[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [sessionID, setSessionID] = useState<string | null>(null)
  // The open project's sessions, newest first (the engine keeps them, so they are there after a restart; /api/sessions).
  const [sessions, setSessions] = useState<{ id: string; title: string; updated: number }[]>([])
  const [sessOpen, setSessOpen] = useState(false)
  // Import OpenCode CLI chats into this project: the sessions the sidebar deliberately hides.
  const [importOpen, setImportOpen] = useState(false)
  const [importable, setImportable] = useState<{ id: string; title: string; updated: number }[]>([])
  const [importPick, setImportPick] = useState<Set<string>>(new Set())
  const [importBusy, setImportBusy] = useState(false)
  const [importNote, setImportNote] = useState("")
  const [spend, setSpend] = useState(0)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const [big, setBig] = useState(false)                  // the message box: bigger / smaller, like the website chat
  const textRef = useRef<HTMLTextAreaElement>(null)
  // Up-arrow history: the prompts already sent in THIS chat (the session's user lines), not a global list.
  const histPos = useRef<number | null>(null)
  const histSaved = useRef("")
  const [files, setFiles] = useState<FileDiff[]>([])
  const [filesNote, setFilesNote] = useState("")
  // Everything else uncommitted in the folder (your own edits, another tool's): shown apart from kell's, and never touched by Undo.
  const [otherFiles, setOtherFiles] = useState<FileDiff[]>([])
  const [otherMore, setOtherMore] = useState(0)
  const [confirm, setConfirm] = useState("")
  const [pending, setPending] = useState(0)
  // The file view: a file picked in the sidebar's tree, its text from app/api/files, and the state of that read.
  const [filePath, setFilePath] = useState<string | null>(null)
  const [fileView, setFileView] = useState<{ text: string; bytes: number; lines: number; truncated: boolean; binary: boolean } | null>(null)
  const [fileErr, setFileErr] = useState("")
  const [fileBusy, setFileBusy] = useState(false)
  const [fileCopied, setFileCopied] = useState(false)
  const [treeEpoch, setTreeEpoch] = useState(0)          // bump: the tree re-reads the folder it was on
  const openFile = async (rel: string) => {
    setFilePath(rel); setFileErr(""); setFileView(null); setFileBusy(true)
    showSidebar("right")                                  // the view lives in the right panel: bring it back if it was hidden
    try {
      const r = await fetch(`/api/files?directory=${encodeURIComponent(directory)}&path=${encodeURIComponent(rel)}`)
      const j = await r.json()
      if (!r.ok || j.error) { setFileErr(j.error || `Could not read ${rel}`); return }
      setFileView(j)
    } catch (e: any) { setFileErr(String(e?.message || e)) }
    finally { setFileBusy(false) }
  }
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
  // The command palette (Ctrl/Cmd-K): app actions and "go to file", with the last few choices first.
  const [palette, setPalette] = useState(false)
  const [paletteQ, setPaletteQ] = useState("")
  const [paletteIdx, setPaletteIdx] = useState(0)
  const [fileHits, setFileHits] = useState<{ name: string; path: string }[]>([])
  const [stopping, setStopping] = useState(false)
  const [stopNote, setStopNote] = useState("")

  useEffect(() => {
    const last = window.localStorage.getItem("kell.directory") || ""
    setDirectory(last)
    fetch("/api/engine").then((r) => r.json()).then((j) => {
      setEngine({ up: !!j.up })
      if (j.up) loadSessions()
      else if (last && desktop()) void openProject(last)    // the app reopens the project you had open, with its sessions
    })
    fetch("/api/settings").then((r) => r.json()).then(setSettings).catch(() => undefined)
  }, [])
  useEffect(() => { scrollToEnd({ instant: true }) }, [scrollToEnd, sessionID])
  useEffect(() => { histPos.current = null; histSaved.current = "" }, [sessionID])
  useEffect(() => { follow() }, [lines, steps, busy, follow])

  const loadChanges = useCallback(async (sid: string | null) => {
    // With a session: its turn's snapshot pair (OpenCode's shadow git) gives both the diff and the undo target, so the panel
    // and the Undo button can never disagree. Without one: the working tree's diff.
    const r = await fetch(sid ? `/api/diff?sessionID=${encodeURIComponent(sid)}` : "/api/diff")
    const j = await r.json()
    if (!r.ok) { setFiles([]); setOtherFiles([]); setOtherMore(0); setPending(0); setFilesNote(j.error || "Could not read changes"); return }
    setFiles(Array.isArray(j.files) ? j.files : [])
    setOtherFiles(Array.isArray(j.other) ? j.other : [])
    setOtherMore(typeof j.more === "number" ? j.more : 0)
    setPending(typeof j.pending === "number" ? j.pending : (j.files || []).length)
    setFilesNote(Array.isArray(j.files) && j.files.length === 0
      ? (sid && j.snapshot ? "No changes in the last turn" : "No changes by kell")
      : (typeof j.pending === "number" && j.pending === 0 ? "Undone" : ""))
  }, [])

  const loadSessions = useCallback(async () => {
    try {
      const j = await (await fetch("/api/sessions")).json()
      setSessions(Array.isArray(j.sessions) ? j.sessions : [])
      const p = await (await fetch("/api/sessions?projects=1")).json()
      if (Array.isArray(p.projects)) mergeProjects(p.projects.map((x: { dir: string }) => x.dir))
    } catch { /* the lists stay as they were */ }
  }, [])

  // The OpenCode CLI chats in this folder, with a checkbox each. Importing marks them as kell sessions; they then
  // appear in the sidebar like any other and can be continued here.
  const openImport = async () => {
    setImportOpen(true); setImportNote(""); setImportPick(new Set()); setImportable([])
    try {
      const j = await (await fetch("/api/sessions?opencode=1")).json()
      if (j.error) setImportNote(String(j.error))
      setImportable(Array.isArray(j.sessions) ? j.sessions : [])
    } catch { setImportNote("Could not read the OpenCode chats.") }
  }
  const doImport = async () => {
    const ids = [...importPick]
    if (!ids.length || importBusy) return
    setImportBusy(true); setImportNote("")
    try {
      const r = await fetch("/api/sessions/import", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionIDs: ids }) })
      const j = await r.json()
      if (!r.ok) { setImportNote(String(j.error || "Could not import these chats.")); return }
      setImportOpen(false)
      await loadSessions()
    } catch { setImportNote("Could not import these chats.") }
    finally { setImportBusy(false) }
  }

  // A past session: its conversation back on screen, and the next message continues it.
  const openSession = async (id: string) => {
    if (busy || id === sessionID) return
    setQueued([]); setSteps([]); setLedger([]); setCards([]); setCardsNote(""); setSpend(0)
    setSessionID(id); setLines([])
    try {
      const j = await (await fetch(`/api/sessions?id=${encodeURIComponent(id)}`)).json()
      setLines(Array.isArray(j.lines) ? j.lines : [])
    } catch { setLines([{ who: "assistant", text: "kell could not read this session.", at: Date.now() }]) }
    loadChanges(id)
  }

  const sessionRow = (x: { id: string; title: string; updated: number }) => (
    <button type="button" key={x.id} className="dk-sess-row" data-here={x.id === sessionID || undefined} title={x.title || "Untitled session"}
      disabled={busy} onClick={() => void openSession(x.id)}>
      <span className="nm">{x.title || "Untitled session"}</span>
      <span className="when">{x.updated ? when(x.updated) : ""}</span>
    </button>
  )

  const loadTools = async (need: string) => {
    setCardsNote("Finding tools…")
    try {
      const r = await fetch("/api/tools", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ need }) })
      const j = await r.json()
      if (!r.ok) { setCards([]); setCardsNote(j.error || "the tool router did not answer"); return }
      setCards(j.tools || [])
      setCardsNote((j.tools || []).length ? "" : "No outside tool needed")
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
    if (j.up) { setQueued([]); setLines([]); setSteps([]); setLedger([]); setSessionID(null); setCards([]); setCardsNote(""); setSpend(0); loadChanges(null); loadSessions() }
    else setSessions([])
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
      if (again === undefined) { setText(""); histPos.current = null }
      return
    }
    const sent = body
    setLines((l) => [...l, { who: "user", text: sent, at: Date.now() }])
    setSteps([])
    if (again === undefined) { setText(""); histPos.current = null }   // sending it again keeps what you were typing
    setBusy(true); busyRef.current = true
    setStopNote("")
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
        else if (t === "session.tool.input.started") {
          // The tool's name comes only here; the called / success / failed events carry the call's id (2026-11-07: every chip read "tool").
          const id = String(d.id || ""), label = String(d.name || "tool")
          setSteps((s) => s.some((x) => x.id === id) ? s : [...s, { kind: "tool", label, id, state: "running" }])
        }
        else if (t === "session.tool.called" || t === "session.tool.success" || t === "session.tool.failed") {
          const id = String(d.id || "")
          const state = t === "session.tool.called" ? "running" : t === "session.tool.success" ? "done" : "failed"
          const hint = t === "session.tool.called" ? hintOf(d.input) : undefined
          setSteps((s) => s.some((x) => x.id === id)
            ? s.map((x) => (x.id === id ? { ...x, state, hint: hint ?? x.hint, note: state === "failed" ? "failed" : x.note } : x))
            : [...s, { kind: "tool", label: String(d.name || "tool"), id, state, hint, note: state === "failed" ? "failed" : undefined }])
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
        else if (t === "permission.asked") {
          // the engine waits for this answer (reading a .env file asks first); without it the turn hung (2026-11-08, the e2e test)
          const what = [d.action, ...(Array.isArray(d.resources) ? d.resources.slice(0, 3) : [])].filter(Boolean).join(" ")
          setLines((l) => l.some((x) => x.requestID === d.id) ? l
            : [...l, { who: "permission", text: String(d.message || what || "a tool call"), sessionID: d.sessionID, requestID: String(d.id), at: Date.now() }])
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
    loadSessions()
    setTimeout(loadSessions, 4000)                           // the engine names a new session a moment after its first turn
  }

  /** Stop the turn at its next safe point (reqs §5). Work that already ran is not undone, and the note says so. */
  const stop = async () => {
    if (!sessionID || stopping) return
    setStopping(true)
    try {
      const r = await fetch("/api/stop", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || `stop failed (${r.status})`)
      setStopNote("Stopped. Steps that already ran stay; nothing is undone.")
    } catch (e: any) {
      setStopNote(`Could not stop the turn: ${String(e?.message || e)}`)
    } finally { setStopping(false) }
  }

  const answerPermission = async (l: Line, decision: "once" | "always" | "reject") => {
    setLines((all) => all.map((x) => (x.requestID === l.requestID ? { ...x, answer: decision } : x)))
    const r = await fetch("/api/permission", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ sessionID: l.sessionID, requestID: l.requestID, decision }) }).catch(() => null)
    if (!r?.ok) setLines((all) => all.map((x) => (x.requestID === l.requestID ? { ...x, answer: undefined } : x)))
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

  // Ctrl/Cmd-K opens the command palette from anywhere in the app (reqs §1: "same shortcut in every context").
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); setPalette((p) => !p); setPaletteQ(""); setPaletteIdx(0) }
      else if (e.key === "Escape" && palette) { setPalette(false) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [palette])

  // The draft prompt survives switching projects or sessions (reqs §1). The queued prompts already outlive a re-render;
  // this keeps what was being typed when the person looked at another session.
  useEffect(() => {
    if (!directory) return
    try { setText(window.localStorage.getItem(`kell.draft:${directory}`) || "") } catch { /* storage blocked */ }
    // only when the project changes: typing must not be overwritten by this effect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [directory])
  useEffect(() => {
    if (!directory) return
    try {
      if (text) window.localStorage.setItem(`kell.draft:${directory}`, text)
      else window.localStorage.removeItem(`kell.draft:${directory}`)
    } catch { /* storage blocked */ }
  }, [text, directory])

  // "Go to file" in the palette: the app searches the project (bounded walk) once the query has a letter in it.
  useEffect(() => {
    if (!palette || !directory || paletteQ.trim().length < 2) { setFileHits([]); return }
    let live = true
    const timer = setTimeout(() => {
      fetch(`/api/files?directory=${encodeURIComponent(directory)}&q=${encodeURIComponent(paletteQ)}`)
        .then((r) => r.json())
        .then((j) => { if (live) setFileHits(Array.isArray(j.files) ? j.files : []) })
        .catch(() => { if (live) setFileHits([]) })
    }, 140)
    return () => { live = false; clearTimeout(timer) }
  }, [palette, directory, paletteQ])

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
  const dw = desktop()

  const isCheap = (r: LedgerRow) => !!settings?.models.cheap && `${r.providerID}/${r.model}` === settings.models.cheap
  // The prompts sent in this chat, oldest first. Opening a past session loads its user lines, so Up works there too;
  // a new session starts empty. Scoped to `lines`, which is reset when the session changes.
  const promptHistory = lines.filter((l) => l.who === "user").map((l) => l.text)
  const readEngine = engine?.up && !!directory
  const words = text.trim() ? text.trim().split(/\s+/).length : 0

  const project = directory ? directory.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || directory : "No project"
  const time = (at?: number) => (at ? new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null)

  // The waiting prompts can be reordered or pulled back into the box, not only dropped (reqs §2: "edit / reorder / delete").
  const moveQueued = (i: number, d: number) => setQueued((q) => {
    const j = i + d
    if (j < 0 || j >= q.length) return q
    const next = [...q]
    ;[next[i], next[j]] = [next[j], next[i]]
    return next
  })
  const editQueued = (i: number) => {
    const t = queued[i]
    setQueued((q) => q.filter((_, k) => k !== i))
    setText(t); histPos.current = null; textRef.current?.focus()
  }

  // The command palette (Ctrl/Cmd-K): app actions, then files whose path matches (app/api/files?q=). Recent choices come
  // first on an empty query, so the shortcut is never an empty box (reqs §1).
  const runCommand = (run: () => void, id?: string) => {
    setPalette(false); setPaletteQ(""); setPaletteIdx(0)
    if (id) try {
      const prev = JSON.parse(window.localStorage.getItem("kell.palette.recent") || "[]")
      const next = [id, ...(Array.isArray(prev) ? prev : []).filter((x) => x !== id)].slice(0, 5)
      window.localStorage.setItem("kell.palette.recent", JSON.stringify(next))
    } catch { /* storage blocked */ }
    run()
  }
  const commands = [
    { id: "new-session", title: "New session", hint: "a fresh chat in this project", run: () => { setSessionID(null); setLines([]); setSteps([]); setLedger([]); setCards([]); setCardsNote(""); setSpend(0); setStopNote(""); loadChanges(null) } },
    { id: "open-folder", title: "Open a folder…", hint: "choose the project kell works in", run: () => { if (!desktop()?.pickFolder) { void openProject(); return } void desktop()?.pickFolder?.().then((d) => { if (d) { setDirectory(d); void openProject(d) } }) } },
    { id: "refresh", title: "Refresh the changes", run: () => loadChanges(sessionID) },
    { id: "undo", title: "Undo kell's last changes", hint: "put the files back as they were", run: () => void undo() },
    { id: "left", title: "Show or hide the project sidebar", run: () => toggleSidebar("left") },
    { id: "right", title: "Show or hide the changes panel", run: () => toggleSidebar("right") },
    { id: "stop", title: "Stop kell", hint: "end the running turn; nothing is undone", run: () => void stop() },
    { id: "code", title: "Go to the coding agent", run: () => setPane("code") },
    { id: "rooms", title: "Go to the team rooms", run: () => setPane("rooms") },
    { id: "console", title: "Open the console", run: () => void openConsole() },
    { id: "signin", title: "Sign in", run: () => void signIn() },
    { id: "signout", title: "Sign out", run: () => void signOut() },
    { id: "help", title: "Help: how kell works", run: () => window.open("https://www.kell.org.in/docs", "_blank", "noopener") },
  ]
  // Subsequence match: "nw s" finds "New session". An empty query matches everything.
  const fuzzy = (hay: string, needle: string) => {
    let i = 0
    for (const ch of hay.toLowerCase()) { if (ch === needle[i]) i++; if (i === needle.length) return true }
    return needle.length === 0
  }
  const recentCommands = (() => {
    if (typeof window === "undefined") return [] as string[]       // the page is prerendered once, without a window
    try { const r = JSON.parse(window.localStorage.getItem("kell.palette.recent") || "[]"); return Array.isArray(r) ? r.filter((x) => typeof x === "string") : [] }
    catch { return [] }
  })()
  const paletteQ2 = paletteQ.trim().toLowerCase()
  const matchedCommands = commands.filter((c) => !paletteQ2 || fuzzy(c.title, paletteQ2))
  const orderedCommands = paletteQ2 ? matchedCommands
    : [...matchedCommands].sort((a, b) => (recentCommands.indexOf(a.id) + 1 || 99) - (recentCommands.indexOf(b.id) + 1 || 99))
  const paletteItems = [
    ...orderedCommands.map((c) => ({ id: c.id, title: c.title, hint: c.hint as string | undefined, kind: "command" as const, run: () => runCommand(c.run, c.id) })),
    ...(paletteQ2.length >= 2 ? fileHits.map((f) => ({ id: `file:${f.path}`, title: f.path, hint: undefined, kind: "file" as const, run: () => runCommand(() => void openFile(f.path)) })) : []),
  ].slice(0, 40)
  const runPaletteItem = (i: number) => { const it = paletteItems[i]; if (it) it.run() }
  const paletteSelected = Math.min(paletteIdx, Math.max(0, paletteItems.length - 1))

  /** Drag the pill at the top of the composer to set its height. The native corner grip still works; this one is
   *  visible and resizes upward the way a bottom-anchored composer should. */
  const startComposerResize = (e: React.PointerEvent<HTMLSpanElement>) => {
    e.preventDefault()
    const el = textRef.current
    if (!el) return
    const startY = e.clientY
    const startH = el.getBoundingClientRect().height
    const max = Math.max(120, window.innerHeight * 0.5)
    const move = (ev: PointerEvent) => {
      const h = Math.max(44, Math.min(max, startH + (startY - ev.clientY)))
      el.style.height = `${h}px`
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up, { once: true })
  }

  return (
    <section className="dg-page dk-page" style={springVars() as React.CSSProperties}>
      <ThemeWatcher />
      <nav className="dk-switch" aria-label="Code or rooms">
        <div role="tablist">
          <button type="button" role="tab" aria-selected={pane === "code"} onClick={() => setPane("code")}>Code</button>
          <button type="button" role="tab" aria-selected={pane === "rooms"} onClick={() => setPane("rooms")}>Rooms</button>
        </div>
        <span className="dk-who">{settings?.signedIn ? <>Signed in as <b>{settings.account || "your kell account"}</b></> : "Not signed in"}</span>
        <span className="dk-help" ref={helpRef}>
          <button type="button" className="dk-icon dk-icon-sm" aria-label="Help" title="Help" aria-expanded={helpOpen} onClick={() => setHelpOpen((o) => !o)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6" /><path d="M12 17h.01" /></svg>
          </button>
          {helpOpen && (
            // Each opens on kell.org.in in the person's browser (the app window never leaves the app).
            <span className="dk-help-menu" role="menu" onClick={() => setHelpOpen(false)}>
              {HELP_LINKS.map(([label, href]) => <a key={href} role="menuitem" href={href} target="_blank" rel="noopener">{label}</a>)}
            </span>
          )}
        </span>
        {/* The app draws its own frame: these are the window's minimize / maximize / close (desktop/electron/main.ts). */}
        {dw?.window && (
          <span className="dk-winbtns">
            <button type="button" className="dk-winbtn" aria-label="Minimize" title="Minimize" onClick={() => dw.window!.minimize()}>
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 5h6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" /></svg>
            </button>
            <button type="button" className="dk-winbtn" aria-label="Maximize" title="Maximize" onClick={() => dw.window!.toggleMaximize()}>
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><rect x="2" y="2" width="6" height="6" stroke="currentColor" strokeWidth="1.1" /></svg>
            </button>
            <button type="button" className="dk-winbtn dk-winbtn-x" aria-label="Close" title="Close" onClick={() => dw.window!.close()}>
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" /></svg>
            </button>
          </span>
        )}
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
      <div className="dg-shell" hidden={pane !== "code"} data-left={leftOpen ? "open" : "closed"} data-right={rightOpen ? "open" : "closed"}
        style={{ "--dk-left": `${widths.left}px`, "--dk-right": `${widths.right}px` } as React.CSSProperties}>
        <div className="dk-resize" data-side="left" role="separator" aria-orientation="vertical" aria-label="Drag to resize the project list" onPointerDown={dragColumn("left")} />
        <div className="dk-resize" data-side="right" role="separator" aria-orientation="vertical" aria-label="Drag to resize the changes panel" onPointerDown={dragColumn("right")} />
        <aside className="dg-side" aria-label="Project, models and account">
          <div className="dg-brand">
            <img className="wm-light" src="/kel-wordmark.png" alt="keळ" style={{ height: 38, width: "auto" }} />
            <img className="wm-dark" src="/kel-wordmark-dark.png" alt="" aria-hidden="true" style={{ height: 38, width: "auto" }} />
          </div>
          <button type="button" className="dk-icon dk-icon-sm dk-side-close" aria-label="Hide the project sidebar"
            title="Hide the project sidebar" onClick={() => toggleSidebar("left")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6" /></svg>
          </button>

          <h2>Project</h2>
          {/* One way in: the folder picker (the app), or a typed path where there is no picker (a browser during development). Projects
              opened before are in the list below. */}
          {!desktop()?.pickFolder && (
            <div style={{ margin: "0 6px" }}>
              <input value={directory} onChange={(e) => setDirectory(e.target.value)} disabled={!!sessionID}
                placeholder="a folder path to work in" className="dk-field mono" aria-label="Project folder" />
            </div>
          )}
          {desktop()?.pickFolder && directory && <p className="dk-note mono" title={directory} style={{ overflowWrap: "anywhere" }}>{directory}</p>}
          <div className="dk-row">
            {!sessionID && (
              <button type="button" className="btn-ink" disabled={busy || opening}
                onClick={async () => {
                  if (!desktop()?.pickFolder) { void openProject(); return }
                  const d = await desktop()?.pickFolder?.(); if (d) { setDirectory(d); void openProject(d) }
                }}>{opening ? "Opening…" : "Open folder"}</button>
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
                    {here && engine?.up && (
                      // The open project's sessions under it, one list: the newest shows, a chevron shows the rest.
                      <div className="dk-sess">
                        {sessions.length > 0 && (sessOpen || sessions.slice(1).some((x) => x.id === sessionID) ? sessions : sessions.slice(0, 1)).map(sessionRow)}
                        {sessions.length > 1 && (
                          <button type="button" className="dk-sess-toggle" aria-expanded={sessOpen} aria-label={sessOpen ? "Show fewer sessions" : "Show all sessions"}
                            title={sessOpen ? "Show fewer" : "Show all"} onClick={() => setSessOpen((o) => !o)}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
                          </button>
                        )}
                        <button type="button" className="dk-sess-import" onClick={() => void openImport()}>Import OpenCode chats</button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}

          {/* The project's files: a folder opens on click and asks the app for its entries then; a file opens in the
              right panel's file view. */}
          {directory && (
            <>
              <div className="dk-sec-head">
                <h2>Files</h2>
                <button type="button" className="dk-icon dk-icon-sm" aria-label="Refresh the file list" title="Refresh the file list"
                  onClick={() => setTreeEpoch((n) => n + 1)}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>
                </button>
              </div>
              <div className="dk-tree" key={`${directory}:${treeEpoch}`}>
                <TreeLevel directory={directory} rel="" depth={0} onOpen={openFile} selected={filePath || undefined} />
              </div>
            </>
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
            {/* The two panel toggles live on their own sides of the project bar: the project sidebar on the left,
                the changes panel on the right. They used to sit together in one cluster after the title, which put
                both in the middle of the bar. */}
            {!leftOpen && (
              <button type="button" className="dk-icon dk-icon-sm dk-head-toggle" aria-label="Show the project sidebar" title="Show the project sidebar"
                onClick={() => toggleSidebar("left")}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></svg>
              </button>
            )}
            <h1>{project}</h1>
            <div className="dk-chips">
              {spend > 0 && <span className="dk-spend">${spend.toFixed(4)} this session</span>}
            </div>
            <ThemeToggle className="dg-theme" />
            {!rightOpen && (
              <button type="button" className="dk-icon dk-icon-sm dk-head-toggle dk-show-right" aria-label="Show the changes panel" title="Show the changes panel"
                onClick={() => toggleSidebar("right")}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></svg>
              </button>
            )}
          </header>

          <div className="dg-scroll">
            <div className="dg-viewport" ref={logRef} aria-label="Messages">
              <div className="dg-content" ref={logContentRef}>
                {lines.length === 0 && !busy && (
                  // The empty state is not a blank box (reqs §8): one line states the boundary, and three prompts that
                  // are known to work here fill the composer rather than a highlight reel.
                  <div className="dk-empty">
                    {directory ? (
                      <>
                        <p className="dk-empty-line">kell can read and edit this folder and run commands here. It asks before anything destructive, and every change comes back as a diff you can undo.</p>
                        <div className="dk-empty-prompts">
                          {["Explain how this project is put together", "Find and fix the bug I describe", "Write tests for a file I name"].map((p) => (
                            <button key={p} type="button" className="btn-line dg-mini dk-empty-prompt" onClick={() => { setText(p); textRef.current?.focus() }}>{p}</button>
                          ))}
                        </div>
                      </>
                    ) : (
                      <>
                        <p className="dk-empty-line">kell works in one folder at a time. Open a folder, then describe the task — the changes come back as a diff you can undo.</p>
                        <div className="dk-row" style={{ margin: 0 }}>
                          <button type="button" className="btn-ink" onClick={async () => {
                            if (!desktop()?.pickFolder) { void openProject(); return }
                            const d = await desktop()?.pickFolder?.(); if (d) { setDirectory(d); void openProject(d) }
                          }}>Open a folder</button>
                        </div>
                      </>
                    )}
                  </div>
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
                  if (l.who === "permission") {
                    return (
                      <div className="dg-run" data-agent="true" key={i}>
                        <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                        <div className="dg-col">
                          <div className="dg-meta"><b>Kell</b><span className="dg-badge">permission</span><time>{time(l.at)}</time></div>
                          <div className="dk-signin" data-state={l.answer ? "done" : "ready"}>
                            <p>{l.answer ? <>{l.answer === "reject" ? "Not allowed" : "Allowed"}: <code>{l.text}</code></> : <>Allow <code>{l.text}</code>?</>}</p>
                            {!l.answer && (
                              <div className="dk-row">
                                <button type="button" className="btn-ink" onClick={() => answerPermission(l, "once")}>Allow once</button>
                                <button type="button" className="btn-line" onClick={() => answerPermission(l, "always")}>Always</button>
                                <button type="button" className="btn-line" onClick={() => answerPermission(l, "reject")}>Don&apos;t allow</button>
                              </div>
                            )}
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

          {busy && steps.length > 0 && (
            // What kell is doing right now: the newest calls, one under the other, each sliding in; the running one spins, done ones fade.
            <ol className="dk-activity" aria-label="What kell is doing" aria-live="polite">
              {steps.slice(-4).map((s, i, all) => (
                <li key={s.id || i} data-state={s.state || "running"} style={{ "--age": all.length - 1 - i } as React.CSSProperties}>
                  <span className="dk-act-mark" aria-hidden="true" />
                  <b>{s.label}</b>
                  {s.hint && <span className="dk-act-hint">{s.hint}</span>}
                </li>
              ))}
            </ol>
          )}
          {queued.map((q, i) => (
            <div key={i} className="dg-queued small" role="status">
              <span className="dim">Waiting for kell:</span> <span className="dg-queued-text">{q}</span>
              <span className="dk-queue-tools">
                {i > 0 && <button type="button" aria-label="Send this sooner" title="Send this sooner" onClick={() => moveQueued(i, -1)}>↑</button>}
                {i < queued.length - 1 && <button type="button" aria-label="Send this later" title="Send this later" onClick={() => moveQueued(i, 1)}>↓</button>}
                <button type="button" aria-label="Edit this message" title="Edit this message" onClick={() => editQueued(i)}>✎</button>
                <button type="button" aria-label="Don't send this one" title="Don't send this one" onClick={() => setQueued((x) => x.filter((_, k) => k !== i))}>×</button>
              </span>
            </div>))}
          {stopNote && <div className="dg-queued small" role="status"><span className="dim">{stopNote}</span></div>}
          <div className="dg-typing" role="status" aria-live="polite">
            {busy && <><span className="dg-pulse" aria-hidden="true" /> Kell is working</>}
          </div>
          <form className="dg-composer" onSubmit={(e) => { e.preventDefault(); send() }}>
            <span className="dg-composer-grip" role="separator" aria-orientation="horizontal" aria-label="Resize the message box" onPointerDown={startComposerResize} />
            <textarea ref={textRef} rows={1} data-big={big || undefined} value={text}
              onChange={(e) => { setText(e.target.value); histPos.current = null }}
              placeholder={directory ? "Tell kell what to do…" : "Open a project first"}
              disabled={!!engine && !engine.up}
              onKeyDown={(e) => {
                // IME-safe: Enter during a CJK composition selects a candidate, it must not send the prompt.
                if (e.key === "Enter" && !e.shiftKey) {
                  if (e.nativeEvent.isComposing) return
                  e.preventDefault(); send(); return
                }
                if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return
                // Only when the caret is at the very start: inside a multi-line draft Up/Down still moves the caret.
                if (e.currentTarget.selectionStart !== 0 || e.currentTarget.selectionEnd !== 0) return
                if (!promptHistory.length) return
                if (e.key === "ArrowUp") {
                  e.preventDefault()
                  if (histPos.current === null) { histSaved.current = text; histPos.current = promptHistory.length - 1 }
                  else histPos.current = Math.max(0, histPos.current - 1)
                  setText(promptHistory[histPos.current])
                  return
                }
                if (histPos.current === null) return
                e.preventDefault()
                if (histPos.current >= promptHistory.length - 1) { histPos.current = null; setText(histSaved.current) }
                else { histPos.current += 1; setText(promptHistory[histPos.current]) }
              }} />
            <div className="dg-composer-meta">
              {text.trim() && <span className="dg-count" aria-live="off">{words} {words === 1 ? "word" : "words"}</span>}
              <button type="button" className="dg-expand" onClick={() => { setBig((b) => !b); if (textRef.current) textRef.current.style.height = ""; textRef.current?.focus() }} aria-pressed={big}
                aria-label={big ? "Make the message box smaller" : "Make the message box bigger"} title={big ? "Smaller" : "Bigger"}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{big ? <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" /> : <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />}</svg>
              </button>
            </div>
            {busy && (
              // Always reachable while a turn runs (reqs §5). Stopping does not undo what already ran, and the title says so.
              <button type="button" className="dk-stop" onClick={() => void stop()} disabled={stopping || !sessionID}
                aria-label="Stop kell" title="Stop the turn. Steps that already ran stay; nothing is undone.">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
              </button>
            )}
            <button type="submit" className="dk-send" disabled={!text.trim() || !directory || (engine != null && !engine.up)}
              aria-label={busy ? "Queue this message" : "Send"} title={busy ? "kell is working: this waits and goes out when it finishes" : "Send"}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          </form>
        </section>

        <aside className="dk-panel" aria-label="File, changes, steps and tools">
          {/* The file view: the file picked in the sidebar's tree, read-only, with its size and a copy button. */}
          {filePath && (
            <section className="dk-viewer" aria-label={`File ${filePath}`}>
              <div className="dk-panel-head dk-viewer-head">
                <h2 title={filePath}>{filePath}</h2>
                <span className="dk-iconrow">
                  <button type="button" className="dk-icon dk-icon-sm" aria-label="Copy the file's text" title="Copy the file"
                    disabled={!fileView || fileView.binary}
                    onClick={async () => {
                      if (!fileView) return
                      try {
                        await navigator.clipboard.writeText(fileView.text)
                        setFileCopied(true); setTimeout(() => setFileCopied(false), 1400)
                      } catch { /* the clipboard can be blocked; the text stays selectable */ }
                    }}>
                    {fileCopied
                      ? <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4 12 5 5L20 6" /></svg>
                      : <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>}
                  </button>
                  <button type="button" className="dk-icon dk-icon-sm" aria-label="Close the file" title="Close the file"
                    onClick={() => { setFilePath(null); setFileView(null); setFileErr("") }}>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
                  </button>
                </span>
              </div>
              {fileBusy && <p className="dk-note">Reading…</p>}
              {fileErr && <p className="dk-err">{fileErr}</p>}
              {fileView && (fileView.binary
                ? <p className="dk-note">A binary file ({Math.max(1, Math.round(fileView.bytes / 1024))} KB) — nothing to show as text.</p>
                : <>
                    <pre className="dk-code" tabIndex={0}>{fileView.text}</pre>
                    <p className="dk-note">{fileView.lines} lines · {Math.max(1, Math.round(fileView.bytes / 1024))} KB{fileView.truncated ? " · showing the start of the file" : ""}</p>
                  </>)}
            </section>
          )}
          <details className="dk-fold" open>
          <summary className="dk-panel-head">
            <h2>Changed by kell{files.length ? ` · ${files.length}` : ""}</h2>
            <span className="dk-iconrow">
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Hide the changes panel" title="Hide the changes panel"
                onClick={(e) => { e.preventDefault(); toggleSidebar("right") }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m10 6 6 6-6 6" /></svg>
              </button>
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Refresh the changes" title="Refresh" onClick={(e) => { e.preventDefault(); loadChanges(sessionID) }} disabled={!readEngine}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>
              </button>
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Undo kell's last changes" title="Undo kell's last changes" onClick={(e) => { e.preventDefault(); undo() }} disabled={!directory || busy || (!!sessionID && pending === 0)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></svg>
              </button>
            </span>
          </summary>
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
          <FileList files={files} openOne />
          </details>

          <details className="dk-fold">
          <summary><h2>Other changes in this folder{otherFiles.length ? ` · ${otherFiles.length + otherMore}` : ""}</h2></summary>
          {readEngine && otherFiles.length === 0 && <p className="dk-note">No other changes</p>}
          <FileList files={otherFiles} />
          {otherMore > 0 && <p className="dk-note">and {otherMore} more</p>}
          </details>

          <details className="dk-fold">
          <summary><h2>Model steps{ledger.length ? ` · ${ledger.length}` : ""}</h2></summary>
          {ledger.length === 0 ? <p className="dk-note">No model steps yet</p> : (
            <div className="dk-ledger">
              {ledger.map((r) => (
                <div className="row" key={r.id}>
                  <span title={r.tokens || ""}><span className="dk-rung" data-r={isCheap(r) ? "cheap" : "strong"}>{isCheap(r) ? "cheap" : "strong"}</span><b>{r.model || "picking model…"}</b></span>
                  <span>{r.cost != null ? `$${r.cost.toFixed(5)}` : "—"}</span>
                </div>
              ))}
            </div>
          )}
          </details>

          <details className="dk-fold">
          <summary><h2>Tools for this request{cards.length ? ` · ${cards.length}` : ""}</h2></summary>
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
          </details>
        </aside>
      </div>

      {palette && (
        <div className="dk-palette-backdrop" onMouseDown={() => setPalette(false)}>
          <div className="dk-palette" role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}>
            <input className="dk-palette-input" autoFocus value={paletteQ} placeholder="Type a command, or a file name…"
              aria-label="Search commands and files" aria-controls="dk-palette-list"
              aria-activedescendant={paletteItems[paletteSelected] ? `dk-palette-${paletteSelected}` : undefined}
              onChange={(e) => { setPaletteQ(e.target.value); setPaletteIdx(0) }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setPaletteIdx((i) => Math.min(Math.max(0, paletteItems.length - 1), i + 1)) }
                else if (e.key === "ArrowUp") { e.preventDefault(); setPaletteIdx((i) => Math.max(0, i - 1)) }
                else if (e.key === "Enter") { if (e.nativeEvent.isComposing) return; e.preventDefault(); runPaletteItem(paletteSelected) }
              }} />
            <ul id="dk-palette-list" role="listbox" aria-label="Commands and files" className="dk-palette-list">
              {paletteItems.map((it, i) => (
                <li key={it.id} id={`dk-palette-${i}`} role="option" aria-selected={i === paletteSelected}>
                  <button type="button" className="dk-palette-row" onMouseEnter={() => setPaletteIdx(i)} onClick={() => it.run()}>
                    <span className="t">{it.title}</span>
                    {it.hint && <span className="h">{it.hint}</span>}
                    <span className="k">{it.kind === "file" ? "file" : ""}</span>
                  </button>
                </li>
              ))}
              {!paletteItems.length && <li className="dk-note" style={{ padding: "10px 14px" }}>Nothing matches.</li>}
            </ul>
            <div className="dk-palette-foot"><kbd>↑</kbd><kbd>↓</kbd> move <kbd>↵</kbd> run <kbd>esc</kbd> close</div>
          </div>
        </div>
      )}

      {importOpen && (
        <div className="dk-import-backdrop" onMouseDown={() => { if (!importBusy) setImportOpen(false) }}>
          <div className="dk-import" role="dialog" aria-modal="true" aria-label="Import OpenCode chats" onMouseDown={(e) => e.stopPropagation()}>
            <div className="dk-import-head">
              <h2>Import OpenCode chats</h2>
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Close" title="Close" disabled={importBusy} onClick={() => setImportOpen(false)}>×</button>
            </div>
            <p className="dk-note">Chats started with the <code>opencode</code> CLI in this folder. Imported chats appear in kell&apos;s session list and can be continued here.</p>
            {importNote && <p className="dk-err" role="status">{importNote}</p>}
            {importable.length > 0 && (
              <div className="dk-import-tools">
                <button type="button" className="btn-line dg-mini" onClick={() => setImportPick(new Set(importable.map((s) => s.id)))}>Select all</button>
                <button type="button" className="btn-line dg-mini" onClick={() => setImportPick(new Set())}>Clear</button>
              </div>
            )}
            {importable.length === 0 ? <p className="dk-note">No OpenCode chats in this folder.</p> : (
              <ul className="dk-import-list">
                {importable.map((s) => (
                  <li key={s.id}>
                    <label>
                      <input type="checkbox" checked={importPick.has(s.id)}
                        onChange={(e) => setImportPick((p) => { const n = new Set(p); if (e.target.checked) n.add(s.id); else n.delete(s.id); return n })} />
                      <span>{s.title || "Untitled chat"}</span>
                      <time>{s.updated ? when(s.updated) : ""}</time>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <div className="dk-import-actions">
              <button type="button" className="btn-line" disabled={importBusy} onClick={() => setImportOpen(false)}>Cancel</button>
              <button type="button" className="btn-ink" disabled={importBusy || importPick.size === 0} onClick={() => void doImport()}>
                {importBusy ? "Importing…" : importPick.size ? `Import ${importPick.size}` : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
