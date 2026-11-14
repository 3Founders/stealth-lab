"use client"
// The screens, in the website's and console's look (docs/desktop_app_plan.md): project picker; chat with streaming and
// tool calls; the changes panel (per-session diff, Undo via `kell undo`); the model strip (which model did each step, its
// tokens and cost, and whether that model is the configured cheap one); tool suggestion cards (kell's tool router, the same
// call the session's plugin makes); and settings (sign-in, models, engine, "Open console").
import { useCallback, useEffect, useRef, useState } from "react"
import Markdown from "@/components/chat/Markdown"
import SmoothText, { Thinking } from "@/components/chat/SmoothText"
import MessageActions from "@/components/chat/MessageActions"
import { ApprovalCard } from "@/components/chat/ApprovalCard"
import ThemeToggle, { ThemeWatcher } from "@/components/ThemeToggle"
import { useSpringScroll } from "@/components/chat/useSpringScroll"
import { KellMark } from "@/components/chat/brand"
import { springVars } from "@/lib/chat/spring"
import { applyFont, readFont, saveFont } from "@/lib/font"
import type { ToolApproval } from "@/lib/tools"
import type { Delegation } from "@/lib/delegate"

// The Electron preload's bridge (electron/preload.ts). Absent in a plain browser (`next dev`): sign-in links then open a tab
// and the rooms are a link to the website's chat.
type Rect = { x: number; y: number; width: number; height: number }
type RoomsWho = { id: string; email: string | null } | null
type ImportSource = "claude" | "codex" | "opencode"

/** A file staged in the composer. `rel` is set only for a file inside the open project; `path` for a local file. */
type Attachment = { id: string; name: string; size: number; mime: string; path?: string; dataUrl?: string; rel?: string }
type QueuedPrompt = { text: string; files: Attachment[] }

/** The other agents whose chats can be brought into this project, in the order the popup offers them. */
const IMPORT_SOURCES: { id: ImportSource; label: string; note: string }[] = [
  { id: "claude", label: "Claude Code", note: "Chats started with the claude CLI in this folder." },
  { id: "codex", label: "Codex", note: "Chats started with the codex CLI in this folder." },
  { id: "opencode", label: "OpenCode", note: "Chats started with the opencode CLI in this folder." },
]
type KellDesktop = {
  openSignIn: (url: string) => Promise<boolean>
  openAccount: (url: string) => Promise<boolean>; signOutRooms: () => Promise<void>; pickFolder?: () => Promise<string | null>; onMenu?: (cb: (what: string) => void) => () => void; showMenu?: () => void
  pickFiles?: () => Promise<string[]>; pathForFile?: (file: File) => string; openPath?: (path: string) => Promise<string>
  onFont?: (cb: (font: string) => void) => () => void; setFont?: (font: string) => void
  window?: { minimize: () => void; toggleMaximize: () => void; close: () => void; isMaximized?: () => Promise<boolean>; onState?: (cb: (max: boolean) => void) => () => void }
  rooms: { show: (rect: Rect | null) => void; reload: () => void; panel?: (p: "desktop" | "submit") => void; theme: (t: "light" | "dark") => void; account: () => Promise<RoomsWho | undefined>; signIn: (provider: "google" | "github") => Promise<boolean>; emailSignIn: () => void }
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
  const server = raw.match(/server\\*"\s*:\s*\\*"([^"\\]+)/)?.[1] || ""
  return { server, link }
}

type Line = { who: "user" | "assistant" | "signin" | "plan" | "permission" | "goal"; text: string; at?: number; server?: string
              state?: "ready" | "open" | "done" | "failed"; sessionID?: string; requestID?: string; answer?: string
              files?: Attachment[] }
type Step = { kind: "tool" | "model"; label: string; note?: string; id?: string; state?: "running" | "done" | "failed"; hint?: string }
/** One question and its answer in a /btw side thread. Deliberately NOT a Line: a side thread never enters the
 *  conversation, so it cannot reach the next turn or the session history (Claude Code's /btw works the same way). */
type BtwTurn = { q: string; a: string; state: "asking" | "done" | "error"; at: number }
/** A side conversation: follow-ups append turns to the same thread; finished threads are stored per chat
 *  (app/api/btw/history) so they can be reopened or deleted. */
type BtwThread = { id: string; sessionID: string | null; at: number; updated: number; turns: BtwTurn[] }
/** What the message box understands as a slash command (the website chat's SLASH_COMMANDS, reduced to this app's own).
 *  send() is the dispatcher: /goal and /btw are both handled there, before anything reaches the engine. */
const SLASH_COMMANDS: Array<{ name: string; hint: string }> = [
  { name: "/btw", hint: "ask a side question about this chat; answered from it, not added to it" },
  { name: "/goal", hint: "set a completion condition; kell keeps working until it holds" },
]
/** A /goal condition: one per session, judged after every turn by a separate cheap model (app/api/goal). */
type GoalState = { condition: string; started: number; turns: number; reason: string; state: "active" | "paused" | "met" | "impossible"
                   sessionID: string | null; max: number; spendAtStart: number; noProgress: number }
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
  // The recent-project list is the app's own store (app/api/projects, ~/.stealthlab/chats.db), not localStorage: the
  // app's origin changes every start and the old boot script cleared the page's copy (2026-11-11).
  useEffect(() => {
    fetch("/api/projects").then((r) => r.json()).then((j) => {
      if (Array.isArray(j.projects)) setRecents(j.projects.filter((x: unknown) => typeof x === "string").slice(0, 20))
    }).catch(() => undefined)
  }, [])
  const remember = (dir: string) => {
    setRecents((r) => [dir, ...r.filter((x) => x.toLowerCase() !== dir.toLowerCase())].slice(0, 20))
    void fetch("/api/projects", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ path: dir }) })
      .then((r) => r.json()).then((j) => { if (Array.isArray(j.projects)) setRecents(j.projects) }).catch(() => undefined)
  }
  // The folders the engine has worked in fill the list after the ones opened here, except ones taken off it with ×.
  const mergeProjects = (dirs: string[]) => {
    if (!dirs.length) return
    // The server knows which ones were taken off the list with ×, so the answer (not the engine's list) is what shows.
    void fetch("/api/projects", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ paths: dirs }) })
      .then((r) => r.json()).then((j) => { if (Array.isArray(j.projects)) setRecents(j.projects) }).catch(() => undefined)
  }
  const forget = (dir: string) => {
    setRecents((r) => r.filter((x) => x !== dir))
    void fetch(`/api/projects?path=${encodeURIComponent(dir)}`, { method: "DELETE" })
      .then((r) => r.json()).then((j) => { if (Array.isArray(j.projects)) setRecents(j.projects) }).catch(() => undefined)
  }
  // The two side columns can be dragged wider or narrower, and either one can be collapsed; the widths and which
  // sidebars are open are kept on this device (kell.layout / kell.sidebars).
  const [widths, setWidths] = useState({ left: 260, right: 340 })
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const [resizing, setResizing] = useState<"left" | "right" | null>(null)
  const shellRef = useRef<HTMLDivElement>(null)
  // Only the middle of the project sidebar scrolls. The wordmark above it and the account row below it stay put, the way
  // the website chat's .dg-side-scroll / .dg-me-wrap do (routing_kell_frontend/app/chat/chat.css). Each fixed band gets a
  // soft shadow while the list has content hidden behind it; it is written to the element, not to state, so scrolling
  // never re-renders this component.
  const sideEdgeCleanup = useRef<(() => void) | null>(null)
  const sideScrollRef = useCallback((el: HTMLDivElement | null) => {
    sideEdgeCleanup.current?.(); sideEdgeCleanup.current = null
    const side = el?.closest<HTMLElement>(".dg-side")
    if (!el || !side) return
    const calc = () => {
      side.dataset.top = String(el.scrollTop > 1)
      side.dataset.bottom = String(el.scrollTop + el.clientHeight < el.scrollHeight - 1)
    }
    calc()
    el.addEventListener("scroll", calc, { passive: true })
    const ro = new ResizeObserver(calc); ro.observe(el)
    const mo = new MutationObserver(calc); mo.observe(el, { childList: true, subtree: true })
    sideEdgeCleanup.current = () => { el.removeEventListener("scroll", calc); ro.disconnect(); mo.disconnect() }
  }, [])
  // The project row's ⋮ menu: a fixed sheet at the pointer, so the sidebar's scroll area can never clip it, plus the
  // one-line note that says what an action did. Copy path, export the chats as Markdown, copy a summary, or remove the
  // folder from the list (a red item; the files are never touched).
  const [projMenu, setProjMenu] = useState<{ path: string; name: string; top: number; left: number } | null>(null)
  const [projNote, setProjNote] = useState("")
  const projNoteTimer = useRef<number>(undefined)
  const noteProject = (text: string) => {
    setProjNote(text)
    window.clearTimeout(projNoteTimer.current)
    projNoteTimer.current = window.setTimeout(() => setProjNote(""), 2400)
  }
  const openProjectMenu = (e: React.MouseEvent<HTMLButtonElement>, p: string) => {
    e.stopPropagation()
    const r = e.currentTarget.getBoundingClientRect()
    const width = 208, height = 190
    const left = Math.min(window.innerWidth - width - 8, Math.max(8, r.right - width))
    const top = r.bottom + 6 + height > window.innerHeight ? Math.max(8, r.top - height - 6) : r.bottom + 6
    const name = p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p
    setProjMenu((m) => (m?.path === p ? null : { path: p, name, top, left }))
  }
  const copyProjectPath = async (p: string) => {
    try { await navigator.clipboard.writeText(p); noteProject("Path copied.") }
    catch { noteProject("The clipboard is blocked here.") }
  }
  const exportProject = async (p: string) => {
    try {
      const r = await fetch(`/api/projects/export?path=${encodeURIComponent(p)}`)
      if (!r.ok) throw new Error(`Export failed (${r.status}).`)
      const url = URL.createObjectURL(await r.blob())
      const safe = (p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "project").replace(/[^A-Za-z0-9._-]+/g, "-")
      const a = document.createElement("a"); a.href = url; a.download = `${safe}-chats.md`; a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
      noteProject("Chats exported as Markdown.")
    } catch (e: any) { noteProject(String(e?.message || e)) }
  }
  const shareProject = async (p: string) => {
    try {
      const r = await fetch(`/api/projects/export?path=${encodeURIComponent(p)}&format=summary`)
      if (!r.ok) throw new Error(`Share failed (${r.status}).`)
      await navigator.clipboard.writeText(await r.text())
      noteProject("Summary copied — paste it anywhere.")
    } catch (e: any) { noteProject(String(e?.message || e)) }
  }
  // The menu is anchored to a row that can move: close it on Escape, a click anywhere else, a scroll or a resize.
  useEffect(() => {
    if (!projMenu) return
    const away = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null
      if (!t?.closest(".dk-proj-menu") && !t?.closest(".dk-recent-more")) setProjMenu(null)
    }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setProjMenu(null) }
    const close = () => setProjMenu(null)
    document.addEventListener("pointerdown", away)
    document.addEventListener("keydown", esc)
    window.addEventListener("scroll", close, true)
    window.addEventListener("resize", close)
    return () => {
      document.removeEventListener("pointerdown", away)
      document.removeEventListener("keydown", esc)
      window.removeEventListener("scroll", close, true)
      window.removeEventListener("resize", close)
    }
  }, [projMenu])
  useEffect(() => {
    try {
      const w = JSON.parse(window.localStorage.getItem("kell.layout") || "null")
      if (w && typeof w.left === "number" && typeof w.right === "number") setWidths(w)
      const s = JSON.parse(window.localStorage.getItem("kell.sidebars") || "null")
      if (s && typeof s.left === "boolean") setLeftOpen(s.left)
      if (s && typeof s.right === "boolean") setRightOpen(s.right)
    } catch { /* default */ }
  }, [])
  // Narrow windows: the side columns become drawers, so start them closed rather than covering the chat. Only when
  // the window crosses into the narrow range, not on every resize (opening a drawer must stay open).
  const wasNarrow = useRef({ right: false, left: false })
  useEffect(() => {
    const apply = () => {
      const narrowRight = window.innerWidth <= 1100
      const narrowLeft = window.innerWidth <= 760
      if (narrowRight && !wasNarrow.current.right) setRightOpen(false)
      if (narrowLeft && !wasNarrow.current.left) setLeftOpen(false)
      wasNarrow.current = { right: narrowRight, left: narrowLeft }
    }
    apply()
    window.addEventListener("resize", apply)
    return () => window.removeEventListener("resize", apply)
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
  // The right panel gets the same treatment as the website's docs panel (docs/ios_animation.md §4.4): a hairline you can
  // drag or nudge with the arrow keys, a width written straight to the DOM in an animation frame (no React render per
  // pointer move), transitions off while dragging, and a shield so a file view or frame cannot swallow the pointer. The
  // right panel can now grow to the window edge (leaving 360px for the chat) instead of stopping at 640px, and its handle
  // is present in the narrow drawer layout too.
  const columnMin = (side: "left" | "right") => (side === "left" ? 190 : 300)
  const columnMax = (side: "left" | "right") => {
    if (typeof window === "undefined") return side === "left" ? 480 : 640
    if (side === "left") return Math.max(190, Math.min(480, window.innerWidth - (rightOpen ? widths.right : 0) - 360))
    if (window.innerWidth <= 1100) return Math.max(300, Math.round(window.innerWidth * 0.92))   // the drawer may cover most of the chat
    return Math.max(300, window.innerWidth - (leftOpen ? widths.left : 0) - 360)
  }
  const saveWidths = (next: { left: number; right: number }) => {
    try { window.localStorage.setItem("kell.layout", JSON.stringify(next)) } catch { /* storage blocked */ }
  }
  const nudgeColumn = (side: "left" | "right", delta: number) => {
    setWidths((w) => {
      const next = { ...w, [side]: Math.round(Math.min(columnMax(side), Math.max(columnMin(side), w[side] + delta))) }
      saveWidths(next)
      return next
    })
  }
  const dragColumn = (side: "left" | "right") => (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const start = widths[side]
    const min = columnMin(side), max = columnMax(side)
    let last = start, frame = 0
    setResizing(side)
    document.body.style.userSelect = "none"
    const paint = () => {
      frame = 0
      const shell = shellRef.current
      if (shell) shell.style.setProperty(side === "left" ? "--dk-left" : "--dk-right", `${last}px`)
    }
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - startX
      last = Math.round(Math.min(max, Math.max(min, side === "left" ? start + dx : start - dx)))
      if (!frame) frame = requestAnimationFrame(paint)
    }
    const up = () => {
      el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up)
      if (frame) cancelAnimationFrame(frame)
      document.body.style.userSelect = ""
      setResizing(null)
      setWidths((w) => ({ ...w, [side]: last }))
      saveWidths({ ...widths, [side]: last })
    }
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up)
  }
  const [accountOpen, setAccountOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  // The window is maximized: the page's maximize button then shows Windows' restore glyph, as the real caption does.
  const [maximized, setMaximized] = useState(false)
  // File > Open folder and File > New session from the app's menu bar (electron/menu.ts).
  const menuRef = useRef<(what: string) => void>(() => {})
  menuRef.current = (what) => {
    if (what === "open-folder" && !busy && !opening) void (async () => { const d = await desktop()?.pickFolder?.(); if (d) { setSessionID(null); setDirectory(d); void openProject(d) } })()
    if (what === "new-session" && !busy) { setSessionID(null); setLines([]); setSteps([]); setLedger([]); loadChanges(null) }
  }
  useEffect(() => desktop()?.onMenu?.((w) => menuRef.current(w)), [])
  // Follow the window's maximize state: our button, a double-click on the title bar, or Windows itself all keep the
  // maximize/restore glyph in step.
  useEffect(() => {
    const w = desktop()?.window
    if (!w) return
    void w.isMaximized?.().then(setMaximized).catch(() => undefined)
    return w.onState?.(setMaximized)
  }, [])
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
  const linesRef = useRef(lines)
  linesRef.current = lines
  const [steps, setSteps] = useState<Step[]>([])
  const stepsRef = useRef(steps)
  stepsRef.current = steps
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [sessionID, setSessionID] = useState<string | null>(null)
  // The open project's sessions, newest first (the engine keeps them, so they are there after a restart; /api/sessions).
  const [sessions, setSessions] = useState<{ id: string; title: string; updated: number; directory?: string | null }[]>([])
  const dirRef = useRef("")                             // the open folder, for callbacks that outlive a render
  dirRef.current = directory
  const [sessOpen, setSessOpen] = useState(false)
  // Import chats from other agents (Claude Code, Codex, OpenCode) into this project: the sessions the sidebar
  // deliberately hides, plus the other agents' own stores on this machine.
  const [importOpen, setImportOpen] = useState(false)
  const [importSource, setImportSource] = useState<ImportSource>("claude")
  const [importable, setImportable] = useState<{ id: string; title: string; updated: number; preview?: string }[]>([])
  const [importPick, setImportPick] = useState<Set<string>>(new Set())
  const [importBusy, setImportBusy] = useState(false)
  const [importLoading, setImportLoading] = useState(false)
  const [importNote, setImportNote] = useState("")
  const importSeq = useRef(0)                       // switching sources quickly: only the last read may fill the list
  const [spend, setSpend] = useState(0)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  // /goal: the condition, its evaluator history, and the guards that keep one evaluator call in flight at a time.
  const [goal, setGoal] = useState<GoalState | null>(null)
  const goalRef = useRef<GoalState | null>(null)
  goalRef.current = goal
  const spendRef = useRef(0)
  spendRef.current = spend
  const goalBusy = useRef(false)
  const goalAfterTurn = useRef<(sid: string | null, failed: boolean) => void>(() => {})
  const goalCommand = useRef<(raw: string) => void>(() => {})
  // A goal belongs to the session it was set in; on reopening that session (or the app) it comes back, as /goal does.
  useEffect(() => {
    if (!sessionID) return
    try {
      const raw = localStorage.getItem("kell.goal")
      if (!raw) return
      const saved = JSON.parse(raw) as GoalState
      if (saved && saved.sessionID === sessionID && (saved.state === "active" || saved.state === "paused")) setGoal({ ...saved, noProgress: saved.noProgress ?? 0 })
    } catch { /* no saved goal */ }
  }, [sessionID])
  useEffect(() => {
    try {
      if (goal?.sessionID) localStorage.setItem("kell.goal", JSON.stringify(goal))
      else if (!goal) localStorage.removeItem("kell.goal")
    } catch { /* storage blocked */ }
  }, [goal])
  const [big, setBig] = useState(false)                  // the message box: bigger / smaller, like the website chat
  // /btw: the side conversation on screen (an overlay, never a Line). Follow-ups append to the same thread, and a
  // finished thread is stored per chat (app/api/btw/history) so it can be reopened or deleted.
  const [btw, setBtw] = useState<BtwThread | null>(null)
  const [btwHistory, setBtwHistory] = useState<BtwThread[]>([])
  const [btwHistOpen, setBtwHistOpen] = useState(false)
  const [btwFollowUp, setBtwFollowUp] = useState("")
  const btwSeq = useRef(0)                               // two side questions: only the newest answer may land
  const btwBodyRef = useRef<HTMLDivElement | null>(null)
  const btwInputRef = useRef<HTMLInputElement | null>(null)
  // The stored side conversations are in the app's own store, not the session; the sheet filters them to this chat.
  useEffect(() => {
    let live = true
    void (async () => {
      try {
        const r = await fetch("/api/btw/history")
        const j = await r.json()
        if (live && Array.isArray(j?.threads)) {
          // Stored turns are always answered; give them the state the sheet renders with.
          setBtwHistory((j.threads as any[]).map((t) => ({
            id: String(t.id), sessionID: t.sessionID == null ? null : String(t.sessionID),
            at: Number(t.at) || 0, updated: Number(t.updated) || 0,
            turns: (Array.isArray(t.turns) ? t.turns : []).map((x: any) => ({
              q: String(x?.q || ""), a: String(x?.a || ""), at: Number(x?.at) || 0, state: "done" as const,
            })),
          })))
        }
      } catch { /* history is optional: the side question itself still works */ }
    })()
    return () => { live = false }
  }, [])
  // The slash menu: which row is highlighted, and the draft it was dismissed for (typing again reopens it).
  const [cmdIdx, setCmdIdx] = useState(0)
  const [cmdDismissedFor, setCmdDismissedFor] = useState<string | null>(null)
  // Pending tool approvals (P1-11): a call the gate wants the person to allow. The card sits above the
  // composer and polls the desktop proxy, which reaches kell's /tools/approvals with the MCP token; the
  // browser never sees the token or the server address.
  const [approvals, setApprovals] = useState<ToolApproval[]>([])
  const [approvalBusy, setApprovalBusy] = useState(false)
  const refreshApprovals = useCallback(async () => {
    try {
      const r = await fetch("/api/tools/approve")
      if (!r.ok) return
      const j = await r.json()
      setApprovals(Array.isArray(j?.approvals) ? j.approvals : [])
    } catch { /* not signed in, or the server is restarting */ }
  }, [])
  const decideApproval = useCallback(async (id: string, action: "approve" | "deny") => {
    setApprovalBusy(true)
    try {
      await fetch("/api/tools/approve", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, action }),
      })
      await refreshApprovals()
    } catch { /* the next poll shows the row again */ } finally { setApprovalBusy(false) }
  }, [refreshApprovals])
  useEffect(() => {
    void refreshApprovals()
    const t = window.setInterval(() => void refreshApprovals(), 15_000)
    const onFocus = () => void refreshApprovals()
    window.addEventListener("focus", onFocus)
    return () => { window.clearInterval(t); window.removeEventListener("focus", onFocus) }
  }, [refreshApprovals])
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
  // The delegation for the current turn: kell's Goal/Way/Tool, the model that runs the turn, and the scoped side call.
  // `planRef` keeps the last plan even when the router answered after the prompt had already gone out.
  const [delegation, setDelegation] = useState<Delegation | null>(null)
  const planRef = useRef<Delegation | null>(null)
  const planSeq = useRef(0)
  const [delegateAsk, setDelegateAsk] = useState("")
  const [delegateAnswer, setDelegateAnswer] = useState("")
  // Every direct answer the delegated agent has given in this session, for the agent view in the middle.
  const [delegateLog, setDelegateLog] = useState<Array<{ q: string; a: string; at: number }>>([])
  // Which agent the middle column is showing: the session itself, or the model the router delegated this turn to.
  const [agentView, setAgentView] = useState<"main" | "delegated">("main")
  const [delegateErr, setDelegateErr] = useState("")
  const [delegateNote, setDelegateNote] = useState("")
  const [delegateStatus, setDelegateStatus] = useState("")
  const [delegateBusy, setDelegateBusy] = useState(false)
  // Opening or starting a chat always returns the middle column to the session itself.
  useEffect(() => { setAgentView("main") }, [sessionID])
  const [settings, setSettings] = useState<Settings | null>(null)
  // The rooms can call this desktop's sessions (lib/remote.ts); on by default, switched in the Account popover.
  const [remoteOn, setRemoteOnState] = useState(true)
  useEffect(() => { fetch("/api/remote").then((r) => r.json()).then((j) => setRemoteOnState(j.on !== false)).catch(() => undefined) }, [])
  const setRemote = async (on: boolean) => {
    try { const j = await (await fetch("/api/remote", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ on }) })).json(); setRemoteOnState(j.on !== false) }
    catch { /* unchanged */ }
  }
  const [consoleNote, setConsoleNote] = useState("")
  const [signinNote, setSigninNote] = useState("")
  // The coding lane, or the team rooms (the website's chat in a view over the placeholder below; electron/rooms.ts).
  const [pane, setPane] = useState<"code" | "rooms">("code")
  const [roomsWho, setRoomsWho] = useState<RoomsWho | undefined>(undefined)   // undefined: not read yet
  // The site's own email/password sign-in page is showing in the rooms view (it is placed even before a session exists).
  const [roomsForm, setRoomsForm] = useState(false)
  // An explicit rooms sign-in should also provision the coding token, without asking for a second credential.
  const pendingCoding = useRef(false)
  const provisioning = useRef(false)
  const roomsRef = useRef<HTMLDivElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const logContentRef = useRef<HTMLDivElement>(null)
  // The same spring scroll as the website chat: follows new text only while you are at the bottom, rubber-bands at the edges.
  const { follow, scrollToEnd, atEnd } = useSpringScroll(logRef, logContentRef, sessionID ?? "")
  const busyRef = useRef(false)                       // a late poll must not overwrite a turn that started after it
  // Messages sent while kell is working wait here and go out one at a time, in order, as each turn ends (× drops one).
  const [queued, setQueued] = useState<QueuedPrompt[]>([])
  // Files staged in the composer. Paths stay paths: the API decides whether a path reference or an attachment is cheaper.
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [leavingAtts, setLeavingAtts] = useState<string[]>([])   // chips playing their exit before they leave the list
  const [dragFiles, setDragFiles] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // The command palette (Ctrl/Cmd-K): app actions and "go to file", with the last few choices first.
  const [palette, setPalette] = useState(false)
  const [paletteQ, setPaletteQ] = useState("")
  const [paletteIdx, setPaletteIdx] = useState(0)
  const [fileHits, setFileHits] = useState<{ name: string; path: string }[]>([])
  const [stopping, setStopping] = useState(false)
  const [stopNote, setStopNote] = useState("")

  useEffect(() => {
    fetch("/api/engine").then((r) => r.json()).then(async (j) => {
      setEngine({ up: !!j.up })
      if (j.up) { void loadSessions(); return }
      // The folder and the chat to reopen come from the app's own store (app/api/last): the page's localStorage is
      // wiped on every start, which is why the app used to open on nothing and look like the chats were gone.
      let last: { directory?: string | null; sessionID?: string | null } | null = null
      try { last = (await (await fetch("/api/last")).json()).last } catch { /* none yet */ }
      if (last?.directory) {
        setDirectory(last.directory)
        void openProject(last.directory, last.sessionID || undefined)
      }
    })
    fetch("/api/settings").then((r) => r.json()).then(setSettings).catch(() => undefined)
  }, [])
  // Typeface (View > Font): apply the saved choice, tell the native menu which one it is, then follow later menu clicks.
  useEffect(() => {
    const font = readFont()
    applyFont(font)
    const d = desktop()
    d?.setFont?.(font)
    return d?.onFont?.((f) => saveFont(f === "inter" || f === "poppins" ? f : "default"))
  }, [])
  useEffect(() => { scrollToEnd({ instant: true }) }, [scrollToEnd, sessionID])
  useEffect(() => { histPos.current = null; histSaved.current = "" }, [sessionID])
  useEffect(() => { follow() }, [lines, steps, busy, follow])
  // A file dropped outside the composer must not navigate the window to file://; the drop zone still handles its own.
  useEffect(() => {
    const stop = (e: DragEvent) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault() }
    document.addEventListener("dragover", stop)
    document.addEventListener("drop", stop)
    return () => { document.removeEventListener("dragover", stop); document.removeEventListener("drop", stop) }
  }, [])

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
      // Backfill once from the engine, then read the app's own store (docs/localdb.md).
      await fetch("/api/local/sync", { method: "POST" }).catch(() => undefined)
      const dir = dirRef.current
      const j = await (await fetch(`/api/local/sessions${dir ? `?directory=${encodeURIComponent(dir)}` : ""}`)).json()
      setSessions(Array.isArray(j.sessions) ? j.sessions : [])
      const p = await (await fetch("/api/sessions?projects=1")).json()
      if (Array.isArray(p.projects)) mergeProjects(p.projects.map((x: { dir: string }) => x.dir))
    } catch { /* the lists stay as they were */ }
  }, [])

  // Another agent's chats in this folder, with a checkbox each. OpenCode's are sessions the sidebar deliberately hides;
  // Claude Code's and Codex's are read from their own stores on this machine. Importing marks them as kell sessions, so
  // they appear in the sidebar like any other and can be continued here.
  const loadImportable = useCallback(async (source: ImportSource) => {
    const seq = ++importSeq.current
    setImportLoading(true); setImportNote(""); setImportPick(new Set()); setImportable([])
    const label = IMPORT_SOURCES.find((s) => s.id === source)?.label || source
    try {
      const url = source === "opencode"
        ? "/api/sessions?opencode=1"
        : `/api/import?source=${source}&directory=${encodeURIComponent(directory)}`
      const j = await (await fetch(url)).json()
      if (seq !== importSeq.current) return
      if (j.error) setImportNote(String(j.error))
      setImportable(Array.isArray(j.chats) ? j.chats : Array.isArray(j.sessions) ? j.sessions : [])
    } catch { if (seq === importSeq.current) setImportNote(`Could not read the ${label} chats.`) }
    finally { if (seq === importSeq.current) setImportLoading(false) }
  }, [directory])
  const openImport = (source: ImportSource = "claude") => {
    setImportOpen(true); setImportSource(source); void loadImportable(source)
  }
  const pickImportSource = (source: ImportSource) => {
    if (importBusy || source === importSource) return
    setImportSource(source); void loadImportable(source)
  }
  const doImport = async () => {
    const ids = [...importPick]
    if (!ids.length || importBusy) return
    setImportBusy(true); setImportNote("")
    try {
      const r = importSource === "opencode"
        ? await fetch("/api/sessions/import", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionIDs: ids }) })
        : await fetch("/api/import", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source: importSource, directory, ids }) })
      const j = await r.json()
      if (!r.ok) { setImportNote(String(j.error || "Could not import these chats.")); return }
      const failed: { id: string; error: string }[] = Array.isArray(j.failed) ? j.failed : []
      if (failed.length) {
        setImportNote(`${ids.length - failed.length} imported; ${failed.length} could not be: ${String(failed[0].error || "")}`)
        setImportPick(new Set(failed.map((f) => String(f.id))))
        await loadSessions()
        return
      }
      setImportOpen(false)
      await loadSessions()
    } catch { setImportNote("Could not import these chats.") }
    finally { setImportBusy(false) }
  }
  // The import popup closes on Escape, like every other overlay in the app; never while it is writing.
  useEffect(() => {
    if (!importOpen) return
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !importBusy) setImportOpen(false) }
    document.addEventListener("keydown", esc)
    return () => document.removeEventListener("keydown", esc)
  }, [importOpen, importBusy])

  // A past session: its conversation back on screen, and the next message continues it.
  const openSession = async (id: string) => {
    if (busy || id === sessionID) return
    setQueued([]); setSteps([]); setLedger([]); setCards([]); setCardsNote(""); setSpend(0)
    setSessionID(id); setLines([])
    void fetch("/api/last", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: id }) }).catch(() => undefined)
    try {
      // The app's own history first (docs/localdb.md); the engine is only the one-time backfill source.
      let j = await (await fetch(`/api/local/sessions?id=${encodeURIComponent(id)}`)).json()
      if (j.error || !Array.isArray(j.lines)) j = await (await fetch(`/api/sessions?id=${encodeURIComponent(id)}`)).json()
      setLines(Array.isArray(j.lines) ? j.lines : [])
    } catch { setLines([{ who: "assistant", text: "kell could not read this session.", at: Date.now() }]) }
    loadChanges(id)
  }

  // Fork: a new session with this chat up to `upTo` (or all of it), opened at once; the original is left as it was.
  const fork = async (upTo?: { who: string; text: string; at?: number }) => {
    if (!sessionID || busy) return
    try {
      const r = await fetch("/api/fork", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID, upTo }) })
      const j = await r.json()
      if (!r.ok || !j.sessionID) { appendNote(`fork: ${j.error || "did not work"}`); return }
      void loadSessions()
      await openSession(j.sessionID)
    } catch (err: any) { appendNote(`fork: ${String(err?.message || err)}`) }
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
  const openProject = async (picked?: string, session?: string) => {
    let dir = picked ?? directory
    if (!dir.trim()) {
      const chosen = await desktop()?.pickFolder?.()
      if (!chosen) return
      dir = chosen; setDirectory(chosen)
    }
    setEngine(null); setOpening(true)
    // The callbacks below read the folder from a ref: set it now, so a fast engine failure cannot leave the previous
    // project's directory in place when loadSessions asks for the list.
    dirRef.current = dir
    let j: { up?: boolean; error?: string }
    try {
      const r = await fetch("/api/engine", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory: dir }) })
      j = await r.json()
    } catch (err) { j = { up: false, error: err instanceof Error ? err.message : String(err) } }
    finally { setOpening(false) }
    setEngine(j.up ? { up: true } : { up: false, error: j.error || "the engine did not start" })
    if (j.up) remember(dir)
    if (j.up) {
      setQueued([]); setLines([]); setSteps([]); setLedger([]); setSessionID(null); setCards([]); setCardsNote(""); setSpend(0); loadChanges(null)
      // The folder (and the chat to reopen, when the app is restoring the last one) is kept in the app's store.
      void fetch("/api/last", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory: dir, sessionID: session ?? null }) }).catch(() => undefined)
    }
    // The app's own store is read even when the engine is down: the chats are still there to read.
    await loadSessions()
    if (j.up && session) void openSession(session)
  }

  const appendAssistant = (delta: string) => setLines((l) => {
    const last = l.at(-1)
    if (last?.who === "assistant") { const c = l.slice(0, -1); c.push({ who: "assistant", text: last.text + delta, at: last.at }); return c }
    return [...l, { who: "assistant", text: delta, at: Date.now() }]
  })

  // The prompt an answer came from, for "Ask again".
  const lastUserBefore = (i: number) => { for (let k = i - 1; k >= 0; k--) if (lines[k].who === "user") return lines[k].text; return undefined }
  const copyText = async (t: string) => { try { await navigator.clipboard.writeText(t); return true } catch { return false } }

  // ---- Attachments -----------------------------------------------------------------------------------------------
  // The cheapest path wins: a text file inside the project is sent as a path reference (the agent reads it with its
  // own tools); only images, PDFs and files outside the project are attached as bytes. Nothing is copied through the page.
  const fileToDataUrl = (f: File) => new Promise<string>((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result || ""))
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
  const addPaths = async (paths: string[]) => {
    if (!paths.length || !directory) return
    try {
      const r = await fetch("/api/attach", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory, paths }) })
      const j = await r.json()
      if (!r.ok) { setConsoleNote(String(j.error || "Could not attach that file")); return }
      const next: Attachment[] = (Array.isArray(j.files) ? j.files : []).map((f: any) => ({
        id: crypto.randomUUID(), name: String(f.name || "attachment"), size: Number(f.size) || 0,
        mime: String(f.mime || "application/octet-stream"), path: String(f.path),
        ...(f.rel ? { rel: String(f.rel) } : {}),
      }))
      setAttachments((a) => [...a, ...next].slice(0, 10))
    } catch { setConsoleNote("Could not attach that file") }
  }
  const addBrowserFiles = async (files: File[]) => {
    const next: Attachment[] = []
    for (const f of files.slice(0, 10)) {
      const p = desktop()?.pathForFile?.(f) || (f as any).path
      if (p) { await addPaths([p]); continue }
      if (f.size > 25 * 1024 * 1024) { setConsoleNote(`${f.name} is larger than 25 MB`); continue }
      try { next.push({ id: crypto.randomUUID(), name: f.name, size: f.size, mime: f.type || "application/octet-stream", dataUrl: await fileToDataUrl(f) }) }
      catch { setConsoleNote(`Could not read ${f.name}`) }
    }
    if (next.length) setAttachments((a) => [...a, ...next].slice(0, 10))
  }
  const pickAttachments = async () => {
    if (desktop()?.pickFiles) { await addPaths(await desktop()!.pickFiles!()); return }
    fileInputRef.current?.click()
  }
  const removeAttachment = (id: string) => {
    // Let the chip play its exit first (transform/opacity only, docs/ios_animation.md); reduced motion removes it now.
    const reduced = typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    if (reduced) { setAttachments((a) => a.filter((x) => x.id !== id)); return }
    setLeavingAtts((s) => (s.includes(id) ? s : [...s, id]))
    window.setTimeout(() => {
      setAttachments((a) => a.filter((x) => x.id !== id))
      setLeavingAtts((s) => s.filter((x) => x !== id))
    }, 190)
  }
  const openAttachment = (a: Attachment) => {
    if (a.rel) { void openFile(a.rel); return }                 // a project file: the app's own viewer
    if (a.path && desktop()?.openPath) { void desktop()!.openPath!(a.path); return }   // outside the project: the OS app
    if (a.dataUrl) { const link = document.createElement("a"); link.href = a.dataUrl; link.download = a.name; link.click() }
  }
  const attachmentChips = (files: Attachment[], removable = false) => files.length ? (
    <div className="dg-atts" aria-label="Attached files">
      {files.map((f) => (
        <span key={f.id} className="dg-att" data-leaving={leavingAtts.includes(f.id) || undefined} title={f.rel || f.path || f.name}>
          <button type="button" className="dg-att-open" onClick={() => openAttachment(f)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>
            <span className="nm">{f.name}</span>
            {f.size > 0 && <span className="sz">{f.size < 1024 ? `${f.size} B` : f.size < 1024 * 1024 ? `${Math.round(f.size / 1024)} KB` : `${(f.size / 1024 / 1024).toFixed(1)} MB`}</span>}
          </button>
          {removable && <button type="button" className="dg-att-x" aria-label={`Remove ${f.name}`} title="Remove" onClick={() => removeAttachment(f.id)}>×</button>}
        </span>
      ))}
    </div>
  ) : null

  // /btw <question>: a side conversation about this chat, answered from the conversation already on screen with no tools,
  // in an overlay. send() intercepts it BEFORE the busy check, so it works while a turn is running, and it never becomes a
  // Line: nothing here enters the session, the history or the next prompt (Claude Code's /btw). Follow-ups stay in the
  // same thread and see the earlier side turns.
  const transcriptForBtw = () => linesRef.current
    .filter((l) => (l.who === "user" || l.who === "assistant") && l.text.trim())
    .slice(-24)
    .map((l) => `${l.who === "user" ? "User" : "kell"}: ${l.text.trim()}`)
    .join("\n\n")

  const saveBtwThread = async (thread: BtwThread) => {
    const stored: BtwThread = { ...thread, turns: thread.turns.filter((t) => t.state === "done") }
    if (!stored.turns.length) return
    setBtwHistory((h) => [stored, ...h.filter((t) => t.id !== stored.id)])
    try {
      await fetch("/api/btw/history", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ thread: stored }),
      })
    } catch { /* kept in memory for this run */ }
  }

  /** Ask, or follow up in the open thread. `fresh` starts a new thread even when one is open. */
  const askBtw = async (raw: string, fresh = false) => {
    const q = raw.trim()
    if (!q) return
    const seq = ++btwSeq.current
    const now = Date.now()
    const base = fresh ? null : btw
    // A superseded question that never answered is dropped, not left spinning in the thread.
    const kept = base ? base.turns.filter((t) => t.state !== "asking") : []
    const next: BtwThread = base
      ? { ...base, updated: now, turns: [...kept, { q, a: "", state: "asking", at: now }] }
      : { id: crypto.randomUUID(), sessionID: sessionID || null, at: now, updated: now,
          turns: [{ q, a: "", state: "asking", at: now }] }
    setBtw(next)
    setBtwHistOpen(false)
    try {
      const r = await fetch("/api/btw", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: q,
          transcript: transcriptForBtw(),
          // The earlier side turns, so a follow-up like "and the second one?" has something to point at.
          history: next.turns.slice(0, -1).filter((t) => t.state === "done").map(({ q: pq, a }) => ({ q: pq, a })),
        }),
      })
      const j = await r.json().catch(() => null)
      if (seq !== btwSeq.current) return                    // a newer side question replaced this one
      const answer = r.ok && j?.answer ? String(j.answer) : ""
      const turn: BtwTurn = answer
        ? { q, a: answer, state: "done", at: now }
        : { q, a: String(j?.error || `The side question failed (${r.status}).`), state: "error", at: now }
      const done: BtwThread = { ...next, updated: Date.now(), turns: [...next.turns.slice(0, -1), turn] }
      setBtw(done)
      if (turn.state === "done") void saveBtwThread(done)
    } catch (e: any) {
      if (seq !== btwSeq.current) return
      const turn: BtwTurn = { q, a: String(e?.message || e), state: "error", at: now }
      setBtw({ ...next, updated: Date.now(), turns: [...next.turns.slice(0, -1), turn] })
    }
  }

  const closeBtw = () => { btwSeq.current++; setBtw(null); setBtwHistOpen(false); textRef.current?.focus() }

  const openBtwThread = (t: BtwThread) => {
    btwSeq.current++                                      // an answer still in flight belongs to the thread being left
    setBtw(t)
    setBtwHistOpen(false)
    setBtwFollowUp("")
  }

  const newBtwThread = () => {
    btwSeq.current++
    const now = Date.now()
    setBtw({ id: crypto.randomUUID(), sessionID: sessionID || null, at: now, updated: now, turns: [] })
    setBtwHistOpen(false)
    setBtwFollowUp("")
  }

  const deleteBtwThread = async (id: string) => {
    setBtwHistory((h) => h.filter((t) => t.id !== id))
    if (btw?.id === id) setBtw(null)
    try { await fetch(`/api/btw/history?id=${encodeURIComponent(id)}`, { method: "DELETE" }) } catch { /* gone from this run anyway */ }
  }

  const handleBtwCommand = (raw: string) => {
    const q = raw.replace(/^\/btw\b/i, "").trim()
    if (q) { void askBtw(q); return }
    // Bare /btw opens the newest side thread for this chat, or an empty sheet ready for a question.
    const recent = btwHistory
      .filter((t) => (t.sessionID || null) === (sessionID || null))
      .sort((a, b) => b.updated - a.updated)[0]
    if (recent) openBtwThread(recent)
    else newBtwThread()
  }

  // The sheet's input takes focus when it opens (and when the history list closes), so a follow-up can be typed at once.
  useEffect(() => {
    if (!btw || btwHistOpen) return
    requestAnimationFrame(() => btwInputRef.current?.focus())
  }, [btw?.id, btwHistOpen])
  // Newest turn (or the answer as it lands) stays in view inside the sheet.
  useEffect(() => {
    const el = btwBodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [btw])

  // Escape closes the side sheet from anywhere; Enter or Space close it only when the sheet itself has focus, never from
  // the follow-up input or one of its buttons.
  useEffect(() => {
    if (!btw) return
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); closeBtw(); return }
      const el = e.target as HTMLElement | null
      if ((e.key === "Enter" || e.key === " ") && el?.closest?.(".dk-btw") && !el.closest("input, textarea, button, select, a")) {
        e.preventDefault(); closeBtw()
      }
    }
    document.addEventListener("keydown", key)
    return () => document.removeEventListener("keydown", key)
  }, [btw])

  // Route the task and pick the model for this turn. The route streams its progress, so the panel shows "asking the
  // router", then the Goal/Way, then the model, instead of going silent. A message waits at most 2.2 s for the plan; a
  // late plan still fills the panel (the route caches it, so a repeat is instant).
  const planFor = async (text: string): Promise<Delegation | null> => {
    const seq = ++planSeq.current
    let settle: (p: Delegation | null) => void = () => undefined
    const planP = new Promise<Delegation | null>((ok) => { settle = ok })
    void (async () => {
      try {
        const r = await fetch("/api/delegate", {
          method: "POST",
          headers: { "content-type": "application/json", accept: "text/event-stream" },
          body: JSON.stringify({ text, models: settings?.models || {}, timeoutMs: 20_000 }),
        })
        if (!r.ok || !r.body) { settle(null); return }
        const reader = r.body.getReader()
        const dec = new TextDecoder()
        let buf = "", plan: Delegation | null = null
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          const parts = buf.split("\n\n")
          buf = parts.pop() || ""
          for (const part of parts) {
            if (!part.startsWith("data: ")) continue
            let ev: any
            try { ev = JSON.parse(part.slice(6)) } catch { continue }
            if (ev.type === "status" && seq === planSeq.current) setDelegateStatus(String(ev.text || ""))
            else if (ev.type === "plan" && ev.plan) {
              plan = ev.plan as Delegation
              if (seq === planSeq.current) { planRef.current = plan; setDelegation(plan); setDelegateNote("") }
              settle(plan)
            } else if (ev.type === "error" && seq === planSeq.current) {
              setDelegateNote(String(ev.error || "the router did not answer"))
            }
          }
        }
        settle(plan)
      } catch { settle(null) }
      finally { if (seq === planSeq.current) setDelegateStatus("") }
    })()
    return await Promise.race([planP, new Promise<null>((ok) => setTimeout(() => ok(null), 2200))])
  }

  const send = async (again?: string, filesOverride?: Attachment[], asGoal = false) => {
    const body = again ?? text
    const outgoing = again === undefined ? attachments : (filesOverride ?? [])
    if (!asGoal && /^\/goal\b/i.test(body.trim())) { goalCommand.current(body.trim()); return }
    if (!asGoal && /^\/btw\b/i.test(body.trim())) { handleBtwCommand(body.trim()); if (again === undefined) { setText(""); histPos.current = null } return }
    setAgentView("main")                                    // a new prompt is for the session, not the agent view
    if ((!body.trim() && !outgoing.length) || !directory) return
    if (busyRef.current) {                                    // still working: keep it for when this turn ends
      setQueued((q) => [...q, { text: body, files: outgoing }])
      if (again === undefined) { setText(""); setAttachments([]); histPos.current = null }
      return
    }
    const sent = body
    // The router starts now and the message waits at most 2.2 s for it: the turn runs on the model the procedure needs.
    const planP = planFor(sent)
    if (asGoal) setLines((l) => [...l, { who: "goal", text: sent, at: Date.now() }])
    else setLines((l) => [...l, { who: "user", text: sent, at: Date.now(), files: outgoing }])
    setSteps([])
    if (again === undefined) { setText(""); setAttachments([]); histPos.current = null }   // sending it again keeps what you were typing
    setBusy(true); busyRef.current = true
    setStopNote("")
    // The cards are for this request: ask while the turn runs (the tool router is fast; the model is not).
    setCards([]); setCardsNote(""); loadTools(sent || outgoing.map((f) => f.name).join(" "))
    const plan = await planP
    if (plan) { setDelegateAnswer(""); setDelegateErr("") }
    const payload = { sessionID, directory, text: sent, model: plan?.model ?? undefined, plan: plan ?? undefined, files: outgoing.map(({ name, path, dataUrl, mime, rel, size }) => ({ name, path, dataUrl, mime, rel, size })) }
    const r = await fetch("/api/prompt", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) })
    if (!r.ok || !r.body) {
      const err = await r.text()
      setLines((l) => [...l, { who: "assistant", text: `kell could not reach the engine: ${err.slice(0, 200)}`, at: Date.now() }])
      setBusy(false); busyRef.current = false; return
    }
    let sid = sessionID
    let buffer = ""
    let ended = false
    let failed = false
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
        if (t === "session") {
          sid = ev.sessionID; setSessionID(ev.sessionID)
          void fetch("/api/last", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: ev.sessionID }) }).catch(() => undefined)
        }
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
        else if (t === "need_plan") { failed = true; setLines((l) => [...l, { who: "plan", text: "", at: Date.now() }]); setBusy(false) }
        else if (t === "error") {
          failed = true
          if (needsWebSignIn(ev.error)) { setEngine({ up: false, error: String(ev.error) }); appendAssistant("Sign in to run models: the buttons are on the left.") }
          else appendAssistant(`\n(error: ${ev.error})`)
          setBusy(false)
        }
        else if (t === "done") ended = true
      }
    }
    // A turn that ends with nothing said is never silent: say so (2026-11-06: a failed model call showed an empty turn).
    setLines((l) => l.slice(before + 1).some((x) => x.who === "assistant" || x.who === "signin" || x.who === "permission" || x.who === "plan") ? l
      : [...l, { who: "assistant", text: ended ? "kell got no answer from the model for this. Try again, or check Settings > Models."
                                               : "The connection to the engine ended before an answer came. Try again.", at: Date.now() }])
    setBusy(false)
    busyRef.current = false
    // Let React flush the last line, then let an active goal judge the turn (app/api/goal). A failed turn pauses it.
    setTimeout(() => goalAfterTurn.current(sid, failed), 80)
    // The session's snapshot record lands shortly after the last stream event, and how long is not fixed: check now, then
    // poll a few times, so the panel fills itself instead of waiting for a Refresh click.
    loadChanges(sid)
    for (const ms of [600, 1200, 2500, 5000]) setTimeout(() => { if (!busyRef.current) loadChanges(sid) }, ms)
    loadSessions()
    setTimeout(loadSessions, 4000)                           // the engine names a new session a moment after its first turn
  }

  // Ask the delegated model directly, with the scoped brief only (app/api/delegate/ask): no files, no shell, no history.
  // The answer streams with status updates ("sent the brief", "working… 4s"), so the panel never goes silent.
  const askDelegate = async () => {
    const plan = delegation || planRef.current
    const question = delegateAsk.trim()
    if (!plan || !question || delegateBusy) return
    setDelegateBusy(true); setDelegateErr(""); setDelegateAnswer("")
    try {
      const r = await fetch("/api/delegate/ask", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ plan, question }),
      })
      if (!r.ok || !r.body) {
        const j = await r.json().catch(() => ({}))
        throw new Error(j?.error || `the delegated model answered ${r.status}`)
      }
      const reader = r.body.getReader()
      const dec = new TextDecoder()
      let buf = "", answer = ""
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const parts = buf.split("\n\n")
        buf = parts.pop() || ""
        for (const part of parts) {
          if (!part.startsWith("data: ")) continue
          let ev: any
          try { ev = JSON.parse(part.slice(6)) } catch { continue }
          if (ev.type === "status") setDelegateStatus(String(ev.text || ""))
          else if (ev.type === "answer") { answer = String(ev.answer || ""); setDelegateAnswer(answer) }
          else if (ev.type === "error") throw new Error(String(ev.error || "the delegated model failed"))
        }
      }
      if (!answer) throw new Error("the delegated model returned no answer")
      setDelegateLog((l) => [...l, { q: question, a: answer, at: Date.now() }])
      setDelegateAsk("")
    } catch (e: any) { setDelegateErr(String(e?.message || e)) }
    finally { setDelegateBusy(false); setDelegateStatus("") }
  }

  // /goal: one condition per session. The command sets, checks, pauses, resumes or clears it; after every turn the
  // evaluator (app/api/goal) returns met / not_yet / impossible, and not_yet starts the next turn with its reason.
  const goalLine = (text: string) => setLines((l) => [...l, { who: "goal", text, at: Date.now() }])
  const handleGoalCommand = (raw: string) => {
    const arg = raw.replace(/^\/goal\b/i, "").trim()
    const g = goalRef.current
    if (!arg) {
      goalLine(g
        ? `Goal ${g.state}: ${g.condition}\n${g.turns} turn${g.turns === 1 ? "" : "s"} evaluated · $${Math.max(0, spendRef.current - g.spendAtStart).toFixed(4)}${g.reason ? `\nLast check: ${g.reason}` : ""}`
        : "No goal set. Use /goal <condition> to set one.")
      return
    }
    if (/^(clear|stop|off|reset|none|cancel)$/i.test(arg)) {
      if (!g) { goalLine("No goal set."); return }
      setGoal(null)
      goalLine(`Goal cleared: ${g.condition}`)
      return
    }
    if (/^pause$/i.test(arg)) {
      if (!g) { goalLine("No goal set."); return }
      setGoal({ ...g, state: "paused" })
      goalLine("Goal paused. /goal resume to carry on.")
      return
    }
    if (/^resume$/i.test(arg)) {
      if (!g) { goalLine("No goal set."); return }
      setGoal({ ...g, state: "active" })
      goalLine(`Goal resumed: ${g.condition}`)
      if (!busyRef.current) void send(g.condition, [], true)
      return
    }
    if (g && g.state === "active") { goalLine("A goal is already active. /goal clear first, or set a new condition to replace it."); return }
    if (!directory) { goalLine("Open a project first: a goal runs in the open folder."); return }
    const bound = Number(arg.match(/stop after (\d+) turns?/i)?.[1] || 20)
    const max = Math.max(1, Math.min(100, bound))
    setGoal({ condition: arg, started: Date.now(), turns: 0, reason: "", state: "active", sessionID, max, spendAtStart: spendRef.current, noProgress: 0 })
    if (!busyRef.current) void send(arg, [], true)   // busy: the evaluator after this turn starts it
  }
  goalCommand.current = handleGoalCommand

  goalAfterTurn.current = (sid, failed) => { void (async () => {
    const g = goalRef.current
    if (!g || g.state !== "active" || goalBusy.current) return
    if (failed) {
      setGoal({ ...g, state: "paused", reason: "The last turn failed." })
      goalLine("Goal paused: the last turn failed. Fix it, then /goal resume.")
      return
    }
    if (sid && g.sessionID !== sid) setGoal({ ...g, sessionID: sid })
    goalBusy.current = true
    setGoal((x) => (x ? { ...x, reason: "checking…" } : x))
    try {
      const tools = stepsRef.current.map((s) => `TOOL ${s.label}${s.hint ? ` ${s.hint}` : ""} ${s.state || "running"}`).join("\n")
      const transcript = (linesRef.current.filter((l) => l.who !== "goal").slice(-40).map((l) => `${l.who.toUpperCase()}: ${l.text}`).join("\n")
        + (tools ? `\n\nTool calls this turn:\n${tools}` : "")).slice(-30_000)
      const r = await fetch("/api/goal", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ condition: g.condition, transcript }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || `the evaluator answered ${r.status}`)
      const now = goalRef.current
      if (!now || now.state !== "active") return
      const reason = String(j.reason || "").slice(0, 800)
      if (j.verdict === "met") { setGoal({ ...now, state: "met", reason }); goalLine(`Goal met: ${now.condition}${reason ? `\n${reason}` : ""}`); return }
      if (j.verdict === "impossible") { setGoal({ ...now, state: "impossible", reason }); goalLine(`Goal impossible: ${reason}`); return }
      const turns = now.turns + 1
      const noProgress = stepsRef.current.some((s) => s.kind === "tool") ? 0 : (now.noProgress || 0) + 1
      if (noProgress >= 3) { setGoal({ ...now, state: "paused", turns, reason, noProgress }); goalLine(`Goal paused: no tool use for ${noProgress} turns in a row. ${reason}`); return }
      if (turns >= now.max) { setGoal({ ...now, state: "paused", turns, reason, noProgress }); goalLine(`Goal paused after ${turns} turns (the bound in the condition). ${reason}`); return }
      setGoal({ ...now, turns, reason, noProgress })
      goalLine(`Not yet (turn ${turns}/${now.max}): ${reason || "the condition does not hold yet"}`)
      goalBusy.current = false                   // the next turn's end must be free to evaluate again
      void send(`Continue working toward this goal: ${now.condition}\n\nThe evaluator's latest check: ${reason || "not met yet"}\n\nKeep going until the condition is demonstrated, then say what proves it.`, [], true)
      return
    } catch (err) {
      const now = goalRef.current
      if (now && now.state === "active") {
        const why = String((err as Error)?.message || err)
        setGoal({ ...now, state: "paused", reason: why })
        goalLine(`Goal paused: ${why}`)
      }
    } finally {
      goalBusy.current = false
    }
  })() }

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

  // One sign-in for the whole app (owner, 2026-11-11). The rooms' website session comes first
  // (electron/rooms.ts::signInRooms): the coding lane's model calls already use it, and it is what Supabase's consent page
  // reads. Then `kell login`'s authorize link opens in a window on the same cookie store (openAccountSignIn), so the
  // consent page knows the person and one "Allow" writes ~/.stealthlab/config.json. No second credential is asked for.
  const provisionCoding = useCallback(async () => {
    if (provisioning.current) return
    const d = desktop()
    if (!d) return
    provisioning.current = true
    setSigninNote("One more step: allow kell to use this account for coding.")
    const started = await fetch("/api/signin", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ again: true }) })
      .then((r) => r.json()).catch(() => ({ error: "could not start kell login" }))
    if (started?.error) { provisioning.current = false; setSigninNote(String(started.error)); return }
    if (started?.already) {
      provisioning.current = false
      const s = await fetch("/api/settings").then((r) => r.json()).catch(() => null)
      if (s) setSettings(s)
      setSigninNote("")
      return
    }
    const ok = await d.openAccount(String(started.url))
    if (!ok) { provisioning.current = false; setSigninNote("The coding sign-in was not finished. Try again."); return }
    const until = Date.now() + 300_000
    const tick = async () => {
      const s = await fetch("/api/settings").then((r) => r.json()).catch(() => null)
      if (s) setSettings(s)
      if (s?.signedIn) { provisioning.current = false; setSigninNote(""); return }
      if (Date.now() > until) { provisioning.current = false; setSigninNote("Still not signed in. Try again."); return }
      setTimeout(tick, 1500)
    }
    setTimeout(tick, 500)
  }, [])

  // The rooms' website session is the app's own (electron/rooms.ts::signInRooms): its own PKCE sign-in in the default browser,
  // so only a one-time code crosses and no token is copied from anywhere.
  const connectRooms = async (provider: "google" | "github") => {
    const d = desktop()
    if (!d) return
    setSigninNote("Finish signing in in your browser, then come back here.")
    pendingCoding.current = true
    const ok = await d.rooms.signIn(provider)
    if (!ok) { pendingCoding.current = false; setSigninNote("The rooms were not signed in. Try again."); return }
    setRoomsWho(undefined)
    setRoomsForm(false)
    const s = await fetch("/api/settings").then((r) => r.json()).catch(() => null)
    if (s) setSettings(s)
    if (s?.signedIn) { pendingCoding.current = false; setSigninNote("Signed in."); return }
    // The view wrote the session before signInRooms resolved; read who it is, then provision the coding token. This runs
    // even when the person signed in from the Code tab, where the Rooms poll is not running.
    let w = await d.rooms.account().catch(() => undefined)
    for (let i = 0; i < 8 && !w; i++) {
      await new Promise((r) => setTimeout(r, 500))
      w = await d.rooms.account().catch(() => undefined)
    }
    if (w) { setRoomsWho(w); pendingCoding.current = false; await provisionCoding() }
  }

  // The email/password path signs in inside the rooms view. Once its session appears there, provision the coding token too,
  // so whichever way the person signed in, they are signed in for both lanes.
  useEffect(() => {
    if (!pendingCoding.current || !roomsWho || !settings) return
    pendingCoding.current = false
    if (!settings.signedIn) void provisionCoding()
  }, [roomsWho, settings, provisionCoding])

  // The account button and the palette: the rooms first (with a provider choice), then the coding token.
  const signIn = async () => {
    const d = desktop()
    if (!d) return
    if (roomsWho) {
      const s = await fetch("/api/settings").then((r) => r.json()).catch(() => null)
      if (s) setSettings(s)
      if (s?.signedIn) { setSigninNote("Already signed in."); return }
      await provisionCoding()
      return
    }
    pendingCoding.current = true
    setPane("rooms")
    setSigninNote("Choose how to sign in: one sign-in covers the rooms and the coding agent.")
  }

  // Sign out of everything: the coding lane's token and the rooms' session (and its cached copies) together, so the app
  // is never half one account and half another.
  const signOut = async () => {
    setSigninNote("Signing out…")
    const r = await fetch("/api/signin", { method: "DELETE" }).then((x) => x.json()).catch(() => ({ error: "could not run kell logout" }))
    await desktop()?.signOutRooms()
    setRoomsWho(null)
    setRoomsForm(false)
    pendingCoding.current = false
    provisioning.current = false
    const s = await fetch("/api/settings").then((x) => x.json()).catch(() => null)
    if (s) setSettings(s)
    setSigninNote(r?.error ? String(r.error) : "Signed out.")
  }

  // One click: the sign-in opens in the person's own browser (electron/signin.ts). No callback comes back, so the page asks
  // kell whether it really finished (lib/tools.ts toolSignInStatus, /api/tools/signin): while the card says "open" and
  // whenever the app regains focus. A closed browser is not a sign-in; only kell saying the token or grant is there is.
  const openToolSignIn = (link: string) => {
    setLines((l) => l.map((x) => (x.who === "signin" && x.text === link ? { ...x, state: "open" } : x)))
    const d = desktop()
    if (!d) { window.open(link, "_blank", "noopener"); return }
    void d.openSignIn(link).then((ok) => {
      if (!ok) setLines((l) => l.map((x) => (x.who === "signin" && x.text === link ? { ...x, state: "failed" } : x)))
    })
  }
  const [resume, setResume] = useState<string | null>(null)
  // The tools whose card is open right now; a change restarts the watch. Only a server kell named can be checked.
  const openSignIns = lines.filter((l) => l.who === "signin" && l.state === "open" && l.server)
  const openKey = openSignIns.map((l) => l.server).join("|")
  useEffect(() => {
    const servers = openKey ? openKey.split("|") : []
    if (!servers.length) return
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const until = Date.now() + 15 * 60_000           // a sign-in link lasts 10 minutes; stop asking after 15
    const check = async () => {
      // The link says which kind it was (a consent page or an OAuth flow); the server uses it to read the grant or a usable token.
      const open = linesRef.current.filter((l) => l.who === "signin" && l.state === "open" && l.server)
      const done: string[] = []
      for (const l of open) {
        const server = l.server
        if (!server) continue
        const ok = await fetch("/api/tools/signin", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ server, link: l.text }) })
          .then((r) => r.json()).then((j) => j?.signedIn === true).catch(() => false)
        if (!live) return
        if (ok) done.push(server)
      }
      if (done.length) {
        setLines((l) => l.map((x) => (x.who === "signin" && x.server && done.includes(x.server) && x.state === "open" ? { ...x, state: "done" } : x)))
        setResume(`I signed in to ${done.join(", ")}. Carry on with what I asked.`)
      }
      if (!live) return
      if (Date.now() > until) {
        setLines((l) => l.map((x) => (x.who === "signin" && x.state === "open" ? { ...x, state: "failed" } : x)))
        return
      }
      timer = setTimeout(check, 4000)
    }
    const onFocus = () => { void check() }
    window.addEventListener("focus", onFocus)
    void check()
    return () => { live = false; if (timer) clearTimeout(timer); window.removeEventListener("focus", onFocus) }
  }, [openKey])
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
    void send(next.text, next.files)
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
  // The rooms view is placed when the WEBSITE session exists (or while its own email/password page is showing), not when the
  // coding token does. The website sign-in is the first step of the one sign-in; gating this on the coding token is what
  // forced people to sign in twice (owner, 2026-11-11).
  const roomsOn = pane === "rooms" && !!desktop() && (!!roomsWho || roomsForm)
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

  // Same account in both lanes: read who the rooms are signed in as while the Rooms tab is open (someone can sign in as
  // another person inside them). The view is created hidden for this when it does not exist yet, so the tab can decide
  // between the provider buttons and the rooms before it shows anything.
  useEffect(() => {
    const d = desktop()
    if (!d || pane !== "rooms") return
    let live = true
    const read = () => d.rooms.account().then((w) => {
      if (!live) return
      setRoomsWho(w)
      if (w) setRoomsForm(false)
    }).catch(() => undefined)
    void read()
    const t = setInterval(read, 2000)
    const theme = () => d.rooms.theme(document.documentElement.dataset.theme === "dark" ? "dark" : "light")
    theme()
    const mo = new MutationObserver(theme)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] })
    return () => { live = false; clearInterval(t); mo.disconnect() }
  }, [pane])
  const otherAccount = !!(settings?.signedIn && settings.subject && roomsWho && roomsWho.id !== settings.subject)
  const dw = desktop()

  const isCheap = (r: LedgerRow) => !!settings?.models.cheap && `${r.providerID}/${r.model}` === settings.models.cheap
  // The prompts sent in this chat, oldest first. Opening a past session loads its user lines, so Up works there too;
  // a new session starts empty. Scoped to `lines`, which is reset when the session changes.
  const promptHistory = lines.filter((l) => l.who === "user").map((l) => l.text)
  const readEngine = engine?.up && !!directory
  const words = text.trim() ? text.trim().split(/\s+/).length : 0

  // The slash menu: opens on "/" and narrows as you type. A command typed in full closes it, so Enter runs that command
  // instead of picking it again (the website chat's rule). Esc dismisses it for this exact draft only.
  const slashQ = (() => { const m = /^\/([\w-]*)$/.exec(text); return m && cmdDismissedFor !== text ? m[1].toLowerCase() : null })()
  const slashCmds = slashQ === null ? [] : SLASH_COMMANDS.filter((c) => {
    const n = c.name.slice(1)
    return n.startsWith(slashQ) && n !== slashQ
  })
  const slashAt = Math.min(cmdIdx, Math.max(0, slashCmds.length - 1))
  const pickSlash = (name: string) => {
    const next = `${name} `
    setText(next); setCmdIdx(0); setCmdDismissedFor(null)
    requestAnimationFrame(() => { textRef.current?.focus(); textRef.current?.setSelectionRange(next.length, next.length) })
  }

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
    setText(t.text); setAttachments(t.files); histPos.current = null; textRef.current?.focus()
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
    { id: "fork", title: "Fork this session", hint: "a new session with this whole chat; the original stays as it is", run: () => void fork() },
    { id: "left", title: "Show or hide the project sidebar", run: () => toggleSidebar("left") },
    { id: "right", title: "Show or hide the changes panel", run: () => toggleSidebar("right") },
    { id: "stop", title: "Stop kell", hint: "end the running turn; nothing is undone", run: () => void stop() },
    { id: "code", title: "Go to the coding agent", run: () => setPane("code") },
    { id: "rooms", title: "Go to the team rooms", run: () => setPane("rooms") },
    { id: "submit-way", title: "Submit a Way", hint: "teach kell a goal and its steps; each step is reviewed", run: () => { setPane("rooms"); desktop()?.rooms.panel?.("submit") } },
    { id: "desktop-sessions", title: "My desktop sessions", hint: "what the rooms can ask this desktop, and who may", run: () => { setPane("rooms"); desktop()?.rooms.panel?.("desktop") } },
    { id: "console", title: "Open the console", run: () => void openConsole() },
    { id: "goal", title: "Set a goal", hint: "/goal <condition> — kell keeps working until it holds", run: () => { setText("/goal "); textRef.current?.focus() } },
    { id: "btw", title: "Ask a side question", hint: "/btw <question> — answered from this chat, without adding to it", run: () => { setText("/btw "); textRef.current?.focus() } },
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

  const importSourceInfo = IMPORT_SOURCES.find((s) => s.id === importSource) || IMPORT_SOURCES[0]

  // The side sheet's derived view: this chat's stored threads, newest first, and whether its newest turn is still out.
  const btwThreads = btwHistory
    .filter((t) => (t.sessionID || null) === (sessionID || null))
    .sort((a, b) => b.updated - a.updated)
  const btwAsking = !!btw?.turns.some((t) => t.state === "asking")
  // The delegated agent's last turn output, shown in its own view in the middle.
  const lastAssistant = [...lines].reverse().find((l) => l.who === "assistant" && l.text.trim())
  // A stored thread's time: the clock for today, a short date for anything older.
  const btwWhen = (ts: number) => {
    const d = new Date(ts)
    return new Date().toDateString() === d.toDateString()
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" })
  }

  return (
    <section className="dg-page dk-page" style={springVars() as React.CSSProperties}>
      <ThemeWatcher />
      <nav className="dk-switch" aria-label="Code, rooms and import">
        {desktop() && (
          <button type="button" className="dk-icon dk-icon-sm dk-menubtn" aria-label="File, Edit and View menu" title="File, Edit, View and Help" onClick={() => desktop()?.showMenu?.()}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
        )}
        <div role="tablist">
          <button type="button" role="tab" aria-selected={pane === "code"} onClick={() => setPane("code")}>Code</button>
          <button type="button" role="tab" aria-selected={pane === "rooms"} onClick={() => setPane("rooms")}>Rooms</button>
        </div>
        {/* Bring another agent's chats into this project: the same popup as the sidebar's import link, with a source each. */}
        <button type="button" className="dk-import-open" onClick={() => openImport()} aria-haspopup="dialog"
          title="Import chats from Claude Code, Codex or OpenCode">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>
          <span className="lbl">Import chats</span>
        </button>
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
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5.5h10" stroke="currentColor" strokeWidth="1" /></svg>
            </button>
            <button type="button" className="dk-winbtn" aria-label={maximized ? "Restore" : "Maximize"} title={maximized ? "Restore" : "Maximize"}
              onClick={() => dw.window!.toggleMaximize()}>
              {maximized
                ? <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><rect x="0.5" y="2.5" width="7" height="7" stroke="currentColor" strokeWidth="1" /><path d="M2.5 2.5v-2h7v7h-2" stroke="currentColor" strokeWidth="1" /></svg>
                : <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><rect x="0.5" y="0.5" width="9" height="9" stroke="currentColor" strokeWidth="1" /></svg>}
            </button>
            <button type="button" className="dk-winbtn dk-winbtn-x" aria-label="Close" title="Close" onClick={() => dw.window!.close()}>
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M0.5 0.5l9 9M9.5 0.5l-9 9" stroke="currentColor" strokeWidth="1" /></svg>
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
      {pane === "rooms" && (
        <div className="dk-rooms" ref={roomsRef} aria-label="Team rooms">
          {!desktop() && (
            <div className="dk-rooms-in">
              <b>Your team&rsquo;s rooms</b>
              <p>People, the agent of your choice and your pal&rsquo;s agents, in one room.</p>
              <p className="dk-note"><a href="https://www.kell.org.in/chat" target="_blank" rel="noopener">Open the rooms on the website</a></p>
            </div>
          )}
          {desktop() && roomsWho === undefined && !roomsForm && (
            <div className="dk-rooms-in"><b>Your team&rsquo;s rooms</b><p>Checking your sign-in…</p></div>
          )}
          {desktop() && roomsWho === null && !roomsForm && (
            <div className="dk-rooms-in">
              <b>Your team&rsquo;s rooms</b>
              <p>People, the agent of your choice and your pal&rsquo;s agents, in one room. Sign in once: the same account runs your coding agent here and your rooms.</p>
              <div className="dk-row" style={{ justifyContent: "center" }}>
                <button type="button" className="btn-ink" onClick={() => void connectRooms("google")}>Continue with Google</button>
                <button type="button" className="btn-ink" onClick={() => void connectRooms("github")}>Continue with GitHub</button>
                <button type="button" className="btn-line"
                  onClick={() => { pendingCoding.current = true; setRoomsForm(true); desktop()?.rooms.emailSignIn() }}>Use email and password</button>
              </div>
              {signinNote && <p className="dk-note">{signinNote}</p>}
            </div>
          )}
        </div>
      )}
      <div className="dg-shell" ref={shellRef} hidden={pane !== "code"} data-left={leftOpen ? "open" : "closed"} data-right={rightOpen ? "open" : "closed"} data-menu={leftOpen ? "open" : "closed"}
        data-resizing={resizing ?? undefined}
        style={{ "--dk-left": `${widths.left}px`, "--dk-right": `${widths.right}px` } as React.CSSProperties}>
        <div className="dg-scrim" onClick={() => setLeftOpen(false)} aria-hidden="true" />
        <div className="dk-resize" data-side="left" role="separator" tabIndex={0} aria-orientation="vertical" aria-label="Drag to resize the project list"
          aria-valuenow={widths.left} aria-valuemin={columnMin("left")}
          onPointerDown={dragColumn("left")}
          onKeyDown={(e) => {
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return
            e.preventDefault()
            nudgeColumn("left", (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 40 : 10))
          }} />
        <div className="dk-resize" data-side="right" role="separator" tabIndex={0} aria-orientation="vertical" aria-label="Drag to resize the changes panel"
          aria-valuenow={widths.right} aria-valuemin={columnMin("right")}
          onPointerDown={dragColumn("right")}
          onKeyDown={(e) => {
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return
            e.preventDefault()
            // The right handle is on the panel's left edge: left widens it, right narrows it.
            nudgeColumn("right", (e.key === "ArrowLeft" ? 1 : -1) * (e.shiftKey ? 40 : 10))
          }} />
        <aside className="dg-side" aria-label="Project, models and account">
          <div className="dg-brand">
            <img className="wm-light" src="/kel-wordmark.png" alt="keळ" style={{ height: 38, width: "auto" }} />
            <img className="wm-dark" src="/kel-wordmark-dark.png" alt="" aria-hidden="true" style={{ height: 38, width: "auto" }} />
          </div>
          <button type="button" className="dk-icon dk-icon-sm dk-side-close" aria-label="Hide the project sidebar"
            title="Hide the project sidebar" onClick={() => toggleSidebar("left")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6" /></svg>
          </button>

          {/* Only this part scrolls: the wordmark above and the account row below stay put, as in the website chat
              (routing_kell_frontend/app/chat/chat.css's .dg-side-scroll / .dg-me-wrap). */}
          <div className="dg-side-scroll" ref={sideScrollRef}>

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
                <button type="button" className="btn-line"
                  onClick={() => { pendingCoding.current = true; setRoomsForm(true); setPane("rooms"); desktop()?.rooms.emailSignIn() }}>Email and password</button>
              </div>
              {signinNote && <p className="dk-note">{signinNote}</p>}
            </div>
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

          {/* Once a project is open its files come first; the projects you have opened sit below them. With no project
              open there is no Files section, so this is still the first list under the project picker. */}
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
                    <button type="button" className="dk-recent-more" aria-haspopup="menu" aria-expanded={projMenu?.path === d}
                      aria-label={`Actions for ${name}`} title="More actions" onClick={(e) => openProjectMenu(e, d)}>
                      {/* Lucide's ellipsis-vertical (ISC), drawn the way the account / console icons at the foot are:
                          24×24, currentColor, round caps. */}
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <circle cx="12" cy="5" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="12" cy="19" r="1" />
                      </svg>
                    </button>
                    {here && (engine?.up || sessions.length > 0) && (
                      // The open project's sessions under it, one list: the newest shows, a chevron shows the rest.
                      <div className="dk-sess">
                        {sessions.length > 0 && (sessOpen || sessions.slice(1).some((x) => x.id === sessionID) ? sessions : sessions.slice(0, 1)).map(sessionRow)}
                        {sessions.length > 1 && (
                          <button type="button" className="dk-sess-toggle" aria-expanded={sessOpen} aria-label={sessOpen ? "Show fewer sessions" : "Show all sessions"}
                            title={sessOpen ? "Show fewer" : "Show all"} onClick={() => setSessOpen((o) => !o)}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
                          </button>
                        )}
                        <button type="button" className="dk-sess-import" onClick={() => openImport("opencode")}>Import OpenCode chats</button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          </div>

          <div className="dk-foot">
            {projNote && <p className="dk-note" role="status">{projNote}</p>}
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
                <label className="dk-note" title="Your sessions show in the rooms (title and folder name only). You, and teammates you allowed in the rooms, can ask them from there.">
                  <input type="checkbox" checked={remoteOn} onChange={(e) => void setRemote(e.target.checked)} /> Let my rooms call this desktop
                </label>
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
              <button type="button" className="dk-icon" aria-label="Route a task to a tool" title="Route a task to a tool" onClick={() => window.location.assign("/route")}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
              </button>
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
          {projMenu && (
            // Fixed, not absolute: the sidebar scrolls, and an absolutely positioned menu would be clipped by it.
            <div className="dk-proj-menu" role="menu" aria-label={`Actions for ${projMenu.name}`}
              style={{ top: projMenu.top, left: projMenu.left }}>
              <button type="button" role="menuitem" onClick={() => { void copyProjectPath(projMenu.path); setProjMenu(null) }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="8" y="8" width="14" height="14" rx="2" /><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2" /></svg>
                Copy path
              </button>
              <button type="button" role="menuitem" onClick={() => { void exportProject(projMenu.path); setProjMenu(null) }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m7 10 5 5 5-5" /><path d="M12 15V3" /></svg>
                Export chats
              </button>
              <button type="button" role="menuitem" onClick={() => { void shareProject(projMenu.path); setProjMenu(null) }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" /></svg>
                Share
              </button>
              <hr />
              <button type="button" role="menuitem" className="danger" onClick={() => { forget(projMenu.path); setProjMenu(null) }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M3 6h18" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                Remove from list
              </button>
            </div>
          )}
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

          <div className="dg-scroll"
            onDragEnter={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); setDragFiles(true) } }}
            onDragOver={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy" } }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragFiles(false) }}
            onDrop={(e) => { e.preventDefault(); setDragFiles(false); void addBrowserFiles(Array.from(e.dataTransfer.files)) }}>
            {dragFiles && <div className="dg-drop" aria-hidden="true"><span>Drop files to attach</span></div>}
            <div className="dg-viewport" ref={logRef} aria-label="Messages">
              <div className={`dg-content${agentView === "delegated" ? " dk-agent-content" : ""}`} ref={logContentRef}>
                {agentView === "delegated" ? (
                  // The delegated agent's own view: what the router picked, the turn it ran, and every direct answer.
                  <div className="dk-agent-view">
                    <div className="dk-agent-view-head">
                      <span className="dk-agent-dot" data-rung={delegation?.rung || "cheap"} aria-hidden="true" />
                      <b>{delegation?.model?.id || "Delegated agent"}</b>
                      {delegation?.why && <span className="dk-agent-view-sub">{delegation.why}</span>}
                      <button type="button" className="btn-line dg-mini" onClick={() => setAgentView("main")}>Back to main</button>
                    </div>
                    {!delegation?.model ? (
                      <p className="dk-note">No delegated agent yet. Send a task and the router picks the model that procedure needs.</p>
                    ) : (
                      <>
                        <div className="dk-agent-plan">
                          <div className="dk-delegate-row"><span>Goal</span><b title={delegation.goal?.name}>{delegation.goal?.name || "no Goal matched"}</b></div>
                          <div className="dk-delegate-row"><span>Way</span><b title={delegation.way?.name}>{delegation.way?.name || "none bound yet"}</b>{delegation.way && <i>{delegation.way.approval}</i>}</div>
                          <div className="dk-delegate-row"><span>Tool</span><b title={delegation.tool?.server || delegation.tool?.name}>{delegation.tool?.name || "none bound"}</b></div>
                          <div className="dk-delegate-row"><span>Model</span><b>{delegation.model.id}</b>{delegation.rung && <i data-rung={delegation.rung}>{delegation.rung}</i>}</div>
                        </div>
                        {(delegateStatus || busy) && (
                          <p className="dk-delegate-status" role="status" aria-live="polite">
                            <span className="dg-pulse" aria-hidden="true" />
                            {delegateStatus || `Working with ${delegation.model.id}…`}
                          </p>
                        )}
                        {lastAssistant && (
                          <div className="dk-agent-turn">
                            <div className="dk-agent-turn-label">Its last turn</div>
                            <Markdown text={lastAssistant.text} />
                          </div>
                        )}
                        {delegateLog.length > 0 ? (
                          <div className="dk-agent-log">
                            {delegateLog.map((x, i) => (
                              <div className="dk-agent-qa" key={`${x.at}-${i}`}>
                                <div className="dk-agent-q">{x.q}</div>
                                <div className="dk-agent-a"><Markdown text={x.a} /></div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="dk-note">Its direct answers appear here. Ask it from the Delegated panel on the right; the brief is all it sees.</p>
                        )}
                        <p className="dk-note">The turn it ran is also in the Main agent view.</p>
                      </>
                    )}
                  </div>
                ) : (
                <>
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
                            <p>{l.state === "done" ? <>Signed in to <b>{l.server || "the tool"}</b>. Carrying on.</>
                              : l.state === "failed" ? <>The sign-in to <b>{l.server || "the tool"}</b> did not finish. Try again.</>
                                : l.state === "open" ? <>Finish signing in to <b>{l.server || "the tool"}</b> in your browser, then come back here.</>
                                  : <><b>{l.server || "This tool"}</b> needs you to sign in once. It opens in your browser; kell keeps the sign-in for next time.</>}</p>
                            {l.state !== "done" && (
                              <div className="dk-row">
                                <button type="button" className="btn-ink" onClick={() => openToolSignIn(l.text)}>{l.state === "failed" ? "Try again" : `Sign in to ${l.server || "the tool"}`}</button>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  }
                  if (l.who === "goal") {
                    return (
                      <div className="dg-run" data-agent="true" key={i}>
                        <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                        <div className="dg-col">
                          <div className="dg-meta"><b>Kell</b><span className="dg-badge">goal</span><time>{time(l.at)}</time></div>
                          <div className="dk-goal-line">{l.text}</div>
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
                          {mine && l.files?.length ? attachmentChips(l.files) : null}
                          {l.text && !live ? (
                            <MessageActions copyText={l.text} retryText={mine ? l.text : lastUserBefore(i)}
                              onCopy={copyText} onRetry={(t) => void send(t)} retryLabel={mine ? "Send this again" : "Ask again"}
                              onFork={sessionID && !busy ? () => void fork({ who: l.who, text: l.text, at: l.at }) : undefined} />
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
                </>
                )}
              </div>
            </div>
            {!atEnd && (
              <button type="button" className="dg-jump" onClick={() => scrollToEnd()} aria-label="Scroll to the latest message" title="Scroll to the latest message">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5v14M5 12l7 7 7-7" /></svg>
                Latest
              </button>
            )}
            {btw && (
              // /btw's side conversation, over the conversation and not in it. Follow-ups stay in this sheet; the clock
              // opens the threads stored for this chat, each with its own delete. Escape, Enter/Space when the sheet
              // itself has focus, or the × dismiss it. It runs beside the turn, so opening it never stops anything.
              <section className="dk-btw" role="dialog" aria-label="Side question" tabIndex={-1}>
                <div className="dk-btw-head">
                  <span className="dk-btw-tag" aria-hidden="true">/btw</span>
                  <span className="dk-btw-q" title={btw.turns[0]?.q || ""}>{btw.turns[0]?.q || "Side question"}</span>
                  <span className="dk-iconrow">
                    <button type="button" className="dk-icon dk-icon-sm" aria-label="Previous side questions"
                      title="Previous side questions" aria-pressed={btwHistOpen} onClick={() => setBtwHistOpen((v) => !v)}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
                    </button>
                    <button type="button" className="dk-icon dk-icon-sm" aria-label="New side question"
                      title="New side question" onClick={newBtwThread}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
                    </button>
                    <button type="button" className="dk-icon dk-icon-sm" aria-label="Close the side answer" title="Close (Esc)" onClick={closeBtw}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
                    </button>
                  </span>
                </div>

                {btwHistOpen ? (
                  <div className="dk-btw-history" aria-label="Previous side questions">
                    {btwThreads.length === 0 ? (
                      <p className="dk-btw-empty">No side questions in this chat yet.</p>
                    ) : (
                      <ul>
                        {btwThreads.map((t) => (
                          <li key={t.id}>
                            <button type="button" className="dk-btw-hist-row" onClick={() => openBtwThread(t)}>
                              <span className="dk-btw-hist-q">{t.turns[0]?.q || "Side question"}</span>
                              <span className="dk-btw-hist-meta">
                                {t.turns.length} {t.turns.length === 1 ? "question" : "questions"}
                                {" · "}
                                {btwWhen(t.updated)}
                              </span>
                            </button>
                            <button type="button" className="dk-icon dk-icon-sm" aria-label="Delete this side question" title="Delete"
                              onClick={() => void deleteBtwThread(t.id)}>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14" /></svg>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : (
                  <div className="dk-btw-a" ref={btwBodyRef} aria-live="polite">
                    {btw.turns.length === 0 && (
                      <p className="dk-btw-empty">Ask a side question about this chat. Follow-ups stay in this sheet; none of it is added to the conversation.</p>
                    )}
                    {btw.turns.map((turn, i) => (
                      <div className="dk-btw-turn" key={`${turn.at}-${i}`}>
                        {/* The header already carries the first question; follow-ups need their own line. */}
                        {i > 0 && <div className="dk-btw-turn-q">{turn.q}</div>}
                        <div className="dk-btw-turn-a">
                          {turn.state === "asking"
                            ? <span className="dk-btw-think"><span className="dg-pulse" aria-hidden="true" /> Reading this chat…</span>
                            : turn.state === "error"
                              ? <p className="dk-btw-err">{turn.a}</p>
                              : <Markdown text={turn.a} />}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {!btwHistOpen && (
                  <form className="dk-btw-ask" onSubmit={(e) => { e.preventDefault(); const q = btwFollowUp; setBtwFollowUp(""); void askBtw(q) }}>
                    <input ref={btwInputRef} value={btwFollowUp} onChange={(e) => setBtwFollowUp(e.target.value)}
                      placeholder={btw.turns.length ? "Ask a follow-up…" : "Ask a side question…"}
                      aria-label="Ask a side question" autoComplete="off" disabled={btwAsking} />
                    <button type="submit" className="btn-line dg-mini" disabled={!btwFollowUp.trim() || btwAsking}>
                      {btwAsking ? "Asking…" : "Ask"}
                    </button>
                  </form>
                )}

                <p className="dk-btw-foot">Answered from this chat only · no tools · not added to the conversation · Esc closes</p>
              </section>
            )}
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
              <span className="dim">Waiting for kell:</span> <span className="dg-queued-text">{q.text}</span>
              {q.files.length > 0 && <span className="dg-queued-files"> +{q.files.length} file{q.files.length === 1 ? "" : "s"}</span>}
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
          {goal && (
            <div className="dk-goal" data-state={goal.state} role="status" aria-label="Goal">
              <span className="dk-goal-mark" aria-hidden="true">◎</span>
              <b>/goal {goal.state}</b>
              <span className="dk-goal-text" title={goal.condition}>{goal.condition}</span>
              <span className="dk-goal-meta">{goal.turns}/{goal.max} turns · ${Math.max(0, spend - goal.spendAtStart).toFixed(4)}</span>
              {goal.reason && <span className="dk-goal-why">{goal.reason}</span>}
              <span className="dk-goal-actions">
                {goal.state === "active" && <button type="button" onClick={() => handleGoalCommand("/goal pause")}>Pause</button>}
                {goal.state === "paused" && <button type="button" onClick={() => handleGoalCommand("/goal resume")}>Resume</button>}
                <button type="button" onClick={() => handleGoalCommand("/goal clear")}>Clear</button>
              </span>
            </div>
          )}
          {approvals.length > 0 && (
            <div role="region" aria-label="Tool approvals" style={{ padding: "0 4px 8px" }}>
              {approvals.slice(0, 3).map((a) => (
                <ApprovalCard key={a.id} approval={a} busy={approvalBusy}
                  onApprove={(id) => void decideApproval(id, "approve")}
                  onDeny={(id) => void decideApproval(id, "deny")} />
              ))}
            </div>
          )}
          {delegateStatus && (
            // The delegated worker reporting in, in the chat: visible even when the right panel is closed.
            <div className="dk-delegate-live" role="status" aria-live="polite">
              <span className="dg-pulse" aria-hidden="true" />
              {delegateStatus}
            </div>
          )}
          {delegation?.model && (
            // The routed model for this turn, in the chat: clicking brings the delegation panel back in the right sidebar.
            <button type="button" className="dk-delegate-chip" onClick={() => showSidebar("right")}
              title={`${delegation.why}. Open the delegation panel.`}>
              <span className="dk-delegate-dot" data-rung={delegation.rung || "cheap"} aria-hidden="true" />
              Delegated to <b>{delegation.model.id}</b>
              <span className="dk-delegate-chip-why">{delegation.goal ? delegation.goal.name : delegation.why}</span>
            </button>
          )}
          <form className="dg-composer" onSubmit={(e) => { e.preventDefault(); send() }}>
            <span className="dg-composer-grip" role="separator" aria-orientation="horizontal" aria-label="Resize the message box" onPointerDown={startComposerResize} />
            {attachments.length > 0 && attachmentChips(attachments, true)}
            <input ref={fileInputRef} type="file" multiple hidden
              onChange={(e) => { const fs = Array.from(e.currentTarget.files || []); e.currentTarget.value = ""; void addBrowserFiles(fs) }} />
            {slashCmds.length > 0 && (
              // The slash menu: the website chat's .dg-commands list, same glass and yellow active row. Picking fills the
              // command plus a space; the command itself runs when that draft is sent (send() dispatches it).
              <ul id="dk-slash" role="listbox" aria-label="Commands" className="dg-mentions dg-commands">
                {slashCmds.map((c, i) => (
                  <li key={c.name} id={`dk-slash-${i}`} role="option" aria-selected={i === slashAt} data-active={i === slashAt}
                    onMouseDown={(e) => { e.preventDefault(); pickSlash(c.name) }}>
                    <code>{c.name}</code> <span className="dim small">{c.hint}</span>
                  </li>
                ))}
              </ul>
            )}
            <textarea ref={textRef} rows={1} data-big={big || undefined} value={text}
              role="combobox" aria-autocomplete="list" aria-expanded={slashCmds.length > 0}
              aria-controls={slashCmds.length ? "dk-slash" : undefined}
              aria-activedescendant={slashCmds.length ? `dk-slash-${slashAt}` : undefined}
              onChange={(e) => { setText(e.target.value); histPos.current = null; setCmdIdx(0) }}
              placeholder={directory ? "Tell kell what to do… (/ for commands)" : "Open a project first"}
              disabled={!!engine && !engine.up}
              onPaste={(e) => { const fs = Array.from(e.clipboardData.files || []); if (fs.length) { e.preventDefault(); void addBrowserFiles(fs) } }}
              onKeyDown={(e) => {
                // While the slash menu is open it owns the arrows, Enter/Tab and Esc: Enter picks a command instead of
                // sending a half-typed one (the website chat's composer behaves the same way).
                if (slashCmds.length) {
                  if (e.key === "ArrowDown") { e.preventDefault(); setCmdIdx((i) => (i + 1) % slashCmds.length); return }
                  if (e.key === "ArrowUp") { e.preventDefault(); setCmdIdx((i) => (i - 1 + slashCmds.length) % slashCmds.length); return }
                  if (e.key === "Escape") { e.preventDefault(); setCmdDismissedFor(text); return }
                  if ((e.key === "Enter" || e.key === "Tab") && !e.nativeEvent.isComposing) {
                    e.preventDefault(); pickSlash(slashCmds[slashAt].name); return
                  }
                }
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
              <button type="button" className="dg-attach dg-attach-file" onClick={() => void pickAttachments()}
                disabled={!directory || (engine != null && !engine.up)} aria-label="Attach files" title="Attach files">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21.4 11.6-8.9 8.9a5 5 0 0 1-7.1-7.1l8.9-8.9a3.5 3.5 0 1 1 5 5l-8.9 8.9a2 2 0 1 1-2.8-2.8l8.2-8.2" /></svg>
              </button>
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
            <button type="submit" className="dk-send" disabled={(!text.trim() && !attachments.length) || !directory || (engine != null && !engine.up)}
              aria-label={busy ? "Queue this message" : "Send"} title={busy ? "kell is working: this waits and goes out when it finishes" : "Send"}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          </form>
        </section>

        <aside className="dk-panel" aria-label="File, changes, steps and tools">
          {/* While the divider is dragged, a transparent shield keeps a file view or an embedded frame from swallowing the pointer. */}
          {resizing === "right" && <div className="dk-shield" aria-hidden="true" />}
          {/* The agents in this session: the session itself and the model the router delegated to. Clicking one swaps the
              middle column between the chat and that agent's own output. */}
          <div className="dk-panel-head"><h2>Agents</h2></div>
          <div className="dk-agents" role="tablist" aria-label="Agents">
            <button type="button" role="tab" className="dk-agent" aria-selected={agentView === "main"} onClick={() => setAgentView("main")}>
              <span className="dk-agent-dot" aria-hidden="true" />
              <span><b>Main agent</b><small>this chat</small></span>
            </button>
            {delegation?.model ? (
              <button type="button" role="tab" className="dk-agent" aria-selected={agentView === "delegated"} onClick={() => setAgentView("delegated")}>
                <span className="dk-agent-dot" data-rung={delegation.rung || "cheap"} aria-hidden="true" />
                <span><b>{delegation.model.id}</b><small>delegated{delegation.rung ? ` · ${delegation.rung}` : ""}</small></span>
              </button>
            ) : (
              <div className="dk-agent dk-agent-empty">
                <span className="dk-agent-dot" aria-hidden="true" />
                <span><b>No delegated agent</b><small>send a task</small></span>
              </div>
            )}
          </div>
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
          {/* The delegated model: what the router matched, which model runs the turn and why, and the scoped side call. */}
          <details className="dk-fold dk-delegate" open>
            <summary className="dk-panel-head">
              <h2>Delegated{delegation?.model ? ` · ${delegation.model.id}` : ""}</h2>
              {delegation?.model && (
                <span className="dk-iconrow">
                  <button type="button" className="dk-icon dk-icon-sm" aria-label="Ask the delegated model" title="Ask it directly"
                    onClick={(e) => { e.preventDefault(); document.getElementById("dk-delegate-ask")?.focus() }}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
                  </button>
                </span>
              )}
            </summary>
            {(delegateStatus || (busy && delegation?.model)) && (
              <p className="dk-delegate-status" role="status" aria-live="polite">
                <span className="dg-pulse" aria-hidden="true" />
                {delegateStatus || `Working with ${delegation?.model?.id}…`}
              </p>
            )}
            {!delegation && <p className="dk-note">Send a task: kell routes it to a Goal and a Way, then runs the turn on the model that procedure needs.</p>}
            {!delegation && delegateNote && <p className="dk-err">{delegateNote}</p>}
            {delegation && (
              <div className="dk-delegate-body">
                <div className="dk-delegate-row">
                  <span>Goal</span>
                  <b title={delegation.goal?.name}>{delegation.goal?.name || "no Goal matched"}</b>
                </div>
                <div className="dk-delegate-row">
                  <span>Way</span>
                  <b title={delegation.way?.name}>{delegation.way?.name || "none bound yet"}</b>
                  {delegation.way && <i>{delegation.way.approval}</i>}
                </div>
                <div className="dk-delegate-row">
                  <span>Tool</span>
                  <b title={delegation.tool?.server || delegation.tool?.name}>{delegation.tool?.name || "none bound"}</b>
                  {delegation.tool?.server && <i>{delegation.tool.server.replace(/^https?:\/\//, "").split("/")[0]}</i>}
                </div>
                <div className="dk-delegate-row">
                  <span>Model</span>
                  <b>{delegation.model?.id || "the engine's default"}</b>
                  {delegation.rung && <i data-rung={delegation.rung}>{delegation.rung}</i>}
                </div>
                <p className="dk-note">{delegation.why}</p>
                <details className="dk-brief">
                  <summary>What it may see</summary>
                  <pre>{delegation.brief}</pre>
                </details>
                <form className="dk-delegate-ask" onSubmit={(e) => { e.preventDefault(); void askDelegate() }}>
                  <input id="dk-delegate-ask" value={delegateAsk} onChange={(e) => setDelegateAsk(e.target.value)}
                    placeholder={`Ask ${delegation.model?.id || "the delegated model"} directly…`} autoComplete="off"
                    aria-label="Ask the delegated model" disabled={delegateBusy} />
                  <button type="submit" className="btn-line dg-mini" disabled={delegateBusy || !delegateAsk.trim()}>
                    {delegateBusy ? "Asking…" : "Ask"}
                  </button>
                </form>
                <p className="dk-note">The brief and the question only: no files, no shell, no chat history.</p>
                {delegateErr && <p className="dk-err">{delegateErr}</p>}
                {delegateAnswer && <div className="dk-delegate-a" aria-live="polite"><Markdown text={delegateAnswer} /></div>}
              </div>
            )}
          </details>
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
        <div className="dk-scrim-right" onClick={() => setRightOpen(false)} aria-hidden="true" />
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
          <div className="dk-import" role="dialog" aria-modal="true" aria-labelledby="dk-import-title" onMouseDown={(e) => e.stopPropagation()}>
            <div className="dk-import-head">
              <h2 id="dk-import-title">Import chats</h2>
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Close" title="Close" disabled={importBusy} onClick={() => setImportOpen(false)}>×</button>
            </div>
            <div className="dk-import-src" role="tablist" aria-label="Where to import from">
              {IMPORT_SOURCES.map((s) => (
                <button key={s.id} type="button" role="tab" aria-selected={importSource === s.id} disabled={importBusy}
                  onClick={() => pickImportSource(s.id)}>{s.label}</button>
              ))}
            </div>
            <p className="dk-note">{importSourceInfo.note} Imported chats appear in kell&apos;s session list and can be continued here.</p>
            {directory && <p className="dk-note dk-import-dir mono" title={directory}>{directory}</p>}
            {!engine?.up && <p className="dk-note">Open a project first: imported chats go into the open project.</p>}
            {importNote && <p className="dk-err" role="status">{importNote}</p>}
            {importLoading ? <p className="dk-note dk-import-empty">Reading {importSourceInfo.label} chats…</p>
              : importable.length === 0 ? <p className="dk-note dk-import-empty">No {importSourceInfo.label} chats in this folder.</p>
              : (
                <>
                  <div className="dk-import-tools">
                    <button type="button" className="btn-line dg-mini" disabled={importBusy} onClick={() => setImportPick(new Set(importable.map((s) => s.id)))}>Select all</button>
                    <button type="button" className="btn-line dg-mini" disabled={importBusy} onClick={() => setImportPick(new Set())}>Clear</button>
                    <span className="dk-note">{importable.length} {importable.length === 1 ? "chat" : "chats"}</span>
                  </div>
                  <ul className="dk-import-list">
                    {importable.map((s) => (
                      <li key={s.id}>
                        <label>
                          <input type="checkbox" checked={importPick.has(s.id)}
                            onChange={(e) => setImportPick((p) => { const n = new Set(p); if (e.target.checked) n.add(s.id); else n.delete(s.id); return n })} />
                          <span className="t">
                            <span className="nm">{s.title || "Untitled chat"}</span>
                            {s.preview && <small>{s.preview}</small>}
                          </span>
                          <time>{s.updated ? when(s.updated) : ""}</time>
                        </label>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            <div className="dk-import-actions">
              <button type="button" className="btn-line" disabled={importBusy} onClick={() => setImportOpen(false)}>Cancel</button>
              <button type="button" className="btn-ink" disabled={importBusy || importLoading || !engine?.up || importPick.size === 0} onClick={() => void doImport()}>
                {importBusy ? "Importing…" : importPick.size ? `Import ${importPick.size}` : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
