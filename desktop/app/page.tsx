"use client"
// The screens, in the website's and console's look (docs/desktop_app_plan.md): project picker; chat with streaming and
// tool calls; the changes panel (per-session diff, Undo via `kell undo`); the model strip (which model did each step, its
// tokens and cost, and whether that model is the configured cheap one); tool suggestion cards (kell's tool router, the same
// call the session's plugin makes); and settings (sign-in, models, engine, "Open console").
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import InfoTip from "@/components/InfoTip"
import Markdown from "@/components/chat/Markdown"
import SmoothText, { Thinking } from "@/components/chat/SmoothText"
import { DocCards, DocPreview, type Doc } from "@/components/DocPanel"
import { ConnectorsPanel, InboxPanel, SchedulesPanel, type InboxItem } from "@/components/Attention"
import MessageActions from "@/components/chat/MessageActions"
import ThemeToggle, { ThemeWatcher } from "@/components/ThemeToggle"
import { useSpringScroll } from "@/components/chat/useSpringScroll"
import { KellMark } from "@/components/chat/brand"
import { springVars } from "@/lib/chat/spring"
import { applyFont, readFont, saveFont } from "@/lib/font"
import type { Delegation, DelegationStep, GoalOption, WayOption } from "@/lib/plan"
import { planFromOption, recommendStepModel } from "@/lib/plan"
import ProcedurePicker from "@/components/ProcedurePicker"
import AttachStrip from "@/components/AttachStrip"
import ModelPicker, { toModelRef } from "@/components/ModelPicker"
import SubmitWayPanel from "@/components/SubmitWayPanel"
import ModesIntro from "@/components/ModesIntro"
import { submissionsApi } from "@/lib/submissions-client"
import { MAX_ATTACHMENTS, fileToDataUrl as readDataUrl, imageDataUrl, isImage, MAX_ATTACH_BYTES, type BarAttachment } from "@/lib/attach"
import BottomPanel, { GlobeIcon, TerminalIcon, type PanelTab } from "@/components/BottomPanel"

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
  openAccount: (url: string) => Promise<boolean>; signOutRooms: () => Promise<void>; pickFolder?: () => Promise<string | null>; onMenu?: (cb: (what: string) => void) => () => void; showMenu?: () => void
  pickFiles?: () => Promise<string[]>; pathForFile?: (file: File) => string; openPath?: (path: string) => Promise<string>
  onQuick?: (cb: (text: string) => void) => () => void
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

// The engine's own subagent tool (it hands a sub-task to a worker agent). It is the one tool whose work deserves a row of
// its own in the agents panel, not just a chip in the chat (2026-11-14: it ran and the right sidebar never showed it).
const SUBAGENT_TOOL = /^(subagent|task)$/i
/** What the subagent was asked to do, in its own one line. */
const subagentTitle = (input: any): string => {
  const v = input?.description ?? input?.subagent_type ?? input?.agent ?? input?.title
  const one = typeof v === "string" ? v.replace(/\s+/g, " ").trim() : ""
  return one ? (one.length > 90 ? one.slice(0, 87) + "…" : one) : "Subagent"
}
/** A tool result's text (session.tool.success carries content parts, not one string). */
const contentText = (c: any): string =>
  Array.isArray(c) ? c.filter((x: any) => x && x.type === "text").map((x: any) => String(x.text || "")).join("\n\n").trim() : ""

/** Where a Way run stands, read from a kell_run_way reply: the run, the step it waits at, and how many there are. */
type RunAt = { id: string; step: number; of: number; title: string }
const runAtOf = (text: string): RunAt | null => {
  let r: any
  try { r = JSON.parse(text) } catch { return null }
  const id = typeof r?.run_id === "string" ? r.run_id : ""
  if (!id || !Number.isFinite(Number(r.of))) return null
  if (["done", "failed", "cancelled"].includes(String(r.status))) return null
  const step = Number(r.next_step ?? r.step) || 1
  const row = Array.isArray(r.steps) ? r.steps.find((s: any) => Number(s?.n) === step) : null
  return { id, step, of: Number(r.of), title: String(row?.title || r.your_turn?.title || r.waiting_on?.title || "") }
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

/** Small talk does not need the catalog; everything else is searched for a Goal/Way first (2026-11-16). */
const CHAT_ONLY = /^\s*(hi|hello|hey|thanks|thank you|ok|okay|yes|no|yep|nope|cool|great|good|nice|lol|sure|bye)\b[\s!.?]*$/i
const desktop = () => (typeof window !== "undefined" ? (window as unknown as { kellDesktop?: KellDesktop }).kellDesktop : undefined)

// A tool that needs a sign-in is no longer shown in the app: kell's gateway answer is left to the agent (2026-11-16).

type Line = { who: "user" | "assistant" | "thought" | "plan" | "permission" | "goal" | "catalog"; text: string; at?: number; server?: string
              sessionID?: string; requestID?: string; answer?: string
              files?: Attachment[]
              /** plan lines: the Way run the missing credit paused, or (low) the balance before a long Way starts */
              run?: RunAt; low?: { left: number; steps: number } }
type Step = { kind: "tool" | "model"; label: string; note?: string; id?: string; state?: "running" | "done" | "failed"; hint?: string }
/** One subagent the engine ran for this turn. The right panel lists it beside the main and delegated agents; clicking it
 *  shows the brief it was given and the answer it returned. */
type SubagentRun = { id: string; title: string; state: "running" | "done" | "failed"; brief?: string; result?: string }
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
  { name: "/model", hint: "choose the model for this chat, e.g. /model deepseek-v4-pro (/model alone lists them)" },
  { name: "/compact", hint: "summarise the older turns so a long chat costs less (automatic at 95% full)" },
  { name: "/clear", hint: "start a fresh chat in this folder; this one stays in the list" },
  { name: "/goal", hint: "set a completion condition; kell keeps working until it holds" },
  { name: "/direct", hint: "send prompts straight to the model; never search for a procedure" },
  { name: "/auto", hint: "search for a procedure and use the best fit automatically" },
  { name: "/ask", hint: "search for a procedure and show the ways to choose from" },
]
/** A model's short name: "deepseek-v4-pro" from "kell/deepseek-v4-pro". */
const shortModel = (m: string) => m.split("/").pop() || m
/** What the status says when kell thinks a stronger model would help. Offered, never applied: the cheap rung runs the
 *  turn, and the person takes the suggestion from the model menu (2026-11-19). */
const suggestLine = (plan: Delegation | null) =>
  plan?.suggest ? ` · kell suggests ${shortModel(plan.suggest.model.id)} (${plan.suggest.why}); pick it in the model menu` : ""
/** How a prompt is handled before the model runs: direct sends it as is; auto searches and uses the best-fitting Way;
 *  ask searches and waits for the person to pick one (the default, so no procedure is ever applied on its own). */
type ProcMode = "direct" | "agent" | "auto" | "ask"
const PROC_MODES: Array<{ id: ProcMode; label: string; hint: string }> = [
  { id: "direct", label: "Direct", hint: "Straight to the model, no procedure search (/direct)" },
  { id: "agent", label: "Agent", hint: "The model decides: it may search the web with kell's tools (Exa), run several sweeps and iterate; kell applies no stored Way (/agent)" },
  { id: "auto", label: "Auto", hint: "Search for a procedure and use the best fit automatically (/auto)" },
  { id: "ask", label: "Ask", hint: "Search for a procedure and show the ways to pick from (/ask)" },
]
/** A /goal condition: one per session, judged after every turn by a separate cheap model (app/api/goal). */
type GoalState = { condition: string; started: number; turns: number; reason: string; state: "active" | "paused" | "met" | "impossible"
                   sessionID: string | null; max: number; spendAtStart: number; noProgress: number }
// One row per step (one model round trip). The engine splits it across events: session.step.started names the model (keyed
// by assistantMessageID), session.step.ended carries tokens and cost for the same message. Neither event carries a rung.
type LedgerRow = { id: string; model?: string; providerID?: string; tokens?: string; cost?: number }
type FileDiff = { file: string; patch: string; additions: number; deletions: number; status: "added" | "deleted" | "modified" }
type Settings = {
  signedIn: boolean; account: string | null; subject?: string | null; server: string | null
  lane?: "organization" | "personal" | "own-key" | "none"; gateway?: string | null
  models: { cheap: string | null; strong: string | null; fallback: string | null; mode: string | null }
  /** Every model this machine may run; the step model picker searches it. Empty when unknown. */
  catalog?: string[]
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

/** A chosen plan as the WayOption the record editor takes, so the panel can show it after the pick. */
const delegationWay = (d: Delegation | null): WayOption | null => d?.way ? {
  id: d.way.id, name: d.way.name, approval: d.way.approval, review: "", why: d.why,
  goal: d.goal, tool: d.tool, steps: d.steps,
} : null

/** A stored locator ({type, value}) or one as typed: one line, for a step's note. */
const locatorText = (l: unknown) =>
  typeof l === "string" ? l : l && typeof l === "object" ? String((l as any).value || "") : ""

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
  // The account card floats over the window at its own width, above the Account button: never squeezed by a narrow sidebar.
  const accountBtnRef = useRef<HTMLButtonElement>(null)
  const [accountAt, setAccountAt] = useState<{ left: number; bottom: number } | null>(null)
  useEffect(() => {
    if (!accountOpen) { setAccountAt(null); return }
    const place = () => { const b = accountBtnRef.current?.getBoundingClientRect(); if (b) setAccountAt({ left: Math.max(8, b.left), bottom: window.innerHeight - b.top + 8 }) }
    place()
    window.addEventListener("resize", place)
    return () => window.removeEventListener("resize", place)
  }, [accountOpen])
  const [helpOpen, setHelpOpen] = useState(false)
  // The window is maximized: the page's maximize button then shows Windows' restore glyph, as the real caption does.
  const [maximized, setMaximized] = useState(false)
  // File > Open folder and File > New session from the app's menu bar (electron/menu.ts).
  const menuRef = useRef<(what: string) => void>(() => {})
  menuRef.current = (what) => {
    if (what === "open-folder") void openFolder()
    if (what === "new-session") newSession()
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
  // The subagents this turn ran (the engine's own subagent/task tool). They are listed with the agents in the right
  // panel, so a delegated worker is visible while it runs instead of only as a chip in the chat (2026-11-14).
  const [subagents, setSubagents] = useState<SubagentRun[]>([])
  // The call ids already known to be subagents: the tool's name comes on input.started, and the later events for the same
  // call may carry only its id (so the panel must not depend on the name arriving twice).
  const subagentIds = useRef<Set<string>>(new Set())
  /** The Way run this chat is in, from the last kell_run_way reply: a stop for credit names where it paused. */
  const lastRun = useRef<RunAt | null>(null)
  // The model this chat last ran on, from its own history (app/api/sessions). The main agent row says it, so a reopened
  // chat does not read as "no agent" (2026-11-14).
  const [sessionModel, setSessionModel] = useState<string | null>(null)
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [sessionID, setSessionID] = useState<string | null>(null)
  const sessionIDRef = useRef<string | null>(null)
  sessionIDRef.current = sessionID
  // The chat being read from the app's store (or the engine's backfill): a switch says so instead of going blank.
  const [sessLoading, setSessLoading] = useState<string | null>(null)
  const sessionSeq = useRef(0)                             // only the last clicked chat may fill the pane
  // A new session discards whatever a procedure search that is still running would put on screen: its picker, its
  // card, or its error belonged to the chat the person just left (2026-11-16).
  const turnGen = useRef(0)
  // The open project's sessions, newest first (the engine keeps them, so they are there after a restart; /api/sessions).
  const [sessions, setSessions] = useState<{ id: string; title: string; updated: number; directory?: string | null; pinned?: boolean; archived?: boolean; folder?: string | null }[]>([])
  const sessionsRef = useRef(sessions)
  sessionsRef.current = sessions
  const dirRef = useRef("")                             // the open folder, for callbacks that outlive a render
  dirRef.current = directory
  const [sessOpen, setSessOpen] = useState(false)
  // The left sidebar's two views (owner, 2026-11-14): the projects list, or one open project (its sessions, then files).
  const [sideView, setSideView] = useState<"projects" | "project">("projects")
  const [showSessions, setShowSessions] = useState(true)
  const [showFiles, setShowFiles] = useState(true)
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
  // The delegation for the current turn: kell's Goal/Way/Tool, the model that runs the turn, and the scoped side call.
  // `planRef` keeps the last plan even when the router answered after the prompt had already gone out.
  const [delegation, setDelegation] = useState<Delegation | null>(null)
  const planRef = useRef<Delegation | null>(null)
  const planSeq = useRef(0)
  const planErr = useRef("")                              // why the procedure search could not run (sign-in, server)
  // The Ways the router offered for the message waiting on the person's choice. The turn does not run until one is
  // picked (or the person chooses to run without a Way).
  const [delegateOptions, setDelegateOptions] = useState<WayOption[]>([])
  // The Way whose record is open in the chat (the sheet): the right panel follows it, so the record is always visible.
  const [inspectWay, setInspectWay] = useState<WayOption | null>(null)
  // Step edits per Way (title, note, and an optional model), shared by the right panel and the card's sheet.
  const [procDrafts, setProcDrafts] = useState<Record<string, DelegationStep[]>>({})
  // The Goals the search matched, even when none has a written Way: the chat shows the result before the model runs.
  const [delegateGoals, setDelegateGoals] = useState<GoalOption[]>([])
  const [pendingTurn, setPendingTurn] = useState<{ text: string; files: Attachment[]; asGoal: boolean; goal: Delegation["goal"]; sid: string | null } | null>(null)
  const [delegateAsk, setDelegateAsk] = useState("")
  const [delegateAnswer, setDelegateAnswer] = useState("")
  // Every direct answer the delegated agent has given in this session, for the agent view in the middle.
  const [delegateLog, setDelegateLog] = useState<Array<{ q: string; a: string; at: number }>>([])
  // Which agent the middle column is showing: the session itself, or the model the router delegated this turn to.
  const [agentView, setAgentView] = useState<string>("main")   // "main" | "delegated" | `sub:<call id>`
  const [delegateErr, setDelegateErr] = useState("")
  const [delegateNote, setDelegateNote] = useState("")
  const [delegateStatus, setDelegateStatus] = useState("")
  const [delegateBusy, setDelegateBusy] = useState(false)
  // The two right-panel folds that start open are controlled: without this, a poll or any other re-render re-applies the
  // `open` attribute and pops the section back open right after the person closes it.
  const [delegateOpen, setDelegateOpen] = useState(true)
  const [changesOpen, setChangesOpen] = useState(true)
  // The procedure graph's glass effect: on unless the machine asks for less transparency, and the person can switch it
  // off (persisted). The graph is one blurred layer behind the cards, not one per card (see ProcedureGraph).
  const [glassGraph, setGlassGraph] = useState(true)
  useEffect(() => {
    try {
      const saved = localStorage.getItem("kell.procedure.glass")
      if (saved === "on" || saved === "off") { setGlassGraph(saved === "on"); return }
    } catch { /* storage can be blocked; the default stands */ }
    const less = window.matchMedia?.("(prefers-reduced-transparency: reduce)").matches
      || window.matchMedia?.("(prefers-contrast: more)").matches
    setGlassGraph(!less)
  }, [])
  const toggleGlassGraph = useCallback(() => {
    setGlassGraph((on) => {
      const next = !on
      try { localStorage.setItem("kell.procedure.glass", next ? "on" : "off") } catch { /* the state still changes */ }
      return next
    })
  }, [])
  // Direct / Auto / Ask, the same idea as Claude's mode switch: the person chooses whether a prompt goes straight to the
  // model, uses the best-fitting Way on its own, or waits for a choice. Persisted, and switchable by button or command.
  const [procMode, setProcMode] = useState<ProcMode>("ask")
  useEffect(() => {
    try {
      const saved = localStorage.getItem("kell.procedure.mode")
      if (saved === "direct" || saved === "agent" || saved === "auto" || saved === "ask") setProcMode(saved)
    } catch { /* storage blocked: the default stands */ }
  }, [])
  const chooseProcMode = useCallback((mode: ProcMode) => {
    setProcMode(mode)
    try { localStorage.setItem("kell.procedure.mode", mode) } catch { /* the state still changes */ }
  }, [])
  // The model the main composer's bar names (provider/id, no value = Chat, kell's router decides). A pick is this person's
  // standing choice and outlasts a reload, unlike the per-turn router rung: it replaces that rung for the turn it sends and
  // rides in the plan, so a Way's steps and the contract describe the same run the chat does (2026-11-18).
  const [chatModel, setChatModel] = useState("")
  useEffect(() => {
    try { const saved = localStorage.getItem("kell.chat.model"); if (saved) setChatModel(saved) } catch { /* storage blocked */ }
  }, [])
  const chooseChatModel = useCallback((m: string) => {
    setChatModel(m)
    try { if (m) localStorage.setItem("kell.chat.model", m); else localStorage.removeItem("kell.chat.model") } catch { /* the state still changes */ }
  }, [])
  // What the model thought, and which files this chat changed: both live in the bar above the message box, so they are
  // one click away instead of being lost (2026-11-19). Thoughts default on; the choice outlasts a reload.
  const [showThoughts, setShowThoughts] = useState(true)
  useEffect(() => {
    try { if (localStorage.getItem("kell.show.thoughts") === "off") setShowThoughts(false) } catch { /* storage blocked */ }
  }, [])
  const toggleThoughts = () => {
    setShowThoughts((on) => {
      const next = !on
      try { localStorage.setItem("kell.show.thoughts", next ? "on" : "off") } catch { /* kept for this run */ }
      return next
    })
  }
  const [editsOpen, setEditsOpen] = useState(false)
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
  // The bottom panel under the chat: terminal and browser (components/BottomPanel.tsx). Mounted on first use and then only
  // hidden, so its shells keep running.
  const [panel, setPanel] = useState<{ open: boolean; tab: PanelTab; used: boolean }>({ open: false, tab: "terminal", used: false })
  const togglePanel = (tab: PanelTab) => setPanel((p) => ({ used: true, tab, open: !(p.open && p.tab === tab) }))
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
  // ChatGPT-like prompt rail: the person's prompts as ticks along the chat's right edge. Hovering the edge shows them;
  // clicking one scrolls that prompt into view (2026-11-17).
  const [railOn, setRailOn] = useState(false)
  const [railTicks, setRailTicks] = useState<{ i: number; pct: number; text: string }[]>([])
  useEffect(() => {
    const view = logRef.current, content = logContentRef.current
    if (!view || !content) return
    const measure = () => {
      const prompts = [...content.querySelectorAll<HTMLElement>("[data-prompt]")]
      const h = view.scrollHeight || 1
      setRailTicks(prompts.map((el) => {
        const top = el.getBoundingClientRect().top - view.getBoundingClientRect().top + view.scrollTop
        const i = Number(el.dataset.prompt || 0)
        const text = linesRef.current[i]?.text?.replace(/\s+/g, " ").trim().slice(0, 120) || "Prompt"
        return { i, pct: Math.min(1, Math.max(0, top / h)), text }
      }))
    }
    const frame = requestAnimationFrame(measure)
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null
    ro?.observe(content)
    return () => { cancelAnimationFrame(frame); ro?.disconnect() }
  }, [lines, sessionID, agentView])
  const jumpToPrompt = (i: number) => {
    const el = logContentRef.current?.querySelector<HTMLElement>(`[data-prompt="${i}"]`)
    if (!el) return
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    el.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" })
  }
  const busyRef = useRef(false)                       // a late poll must not overwrite a turn that started after it
  // Messages sent while kell is working wait here and go out one at a time, in order, as each turn ends (× drops one).
  const [queued, setQueued] = useState<QueuedPrompt[]>([])
  // Files staged in the composer. Paths stay paths: the API decides whether a path reference or an attachment is cheaper.
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [leavingAtts, setLeavingAtts] = useState<string[]>([])   // chips playing their exit before they leave the list
  const [dragFiles, setDragFiles] = useState(false)             // a file over the chat: the overlay says it will attach
  const [overComposer, setOverComposer] = useState(false)       // a file over the composer itself: its box lights up
  const fileInputRef = useRef<HTMLInputElement>(null)
  // The first open says what Direct / Auto / Ask do, once per machine (kell:modes-seen, kept by app/api/prefs because the
  // app's origin changes every start). The switch itself is a three-word control above the message box; a person who
  // never learns the difference leaves it on Ask and wonders why nothing runs.
  const [modesIntro, setModesIntro] = useState(false)
  useEffect(() => {
    try { if (!localStorage.getItem("kell:modes-seen")) setModesIntro(true) } catch { /* storage blocked: the app still works */ }
  }, [])
  const closeModesIntro = () => {
    try { localStorage.setItem("kell:modes-seen", "1") } catch { /* kept for this run only */ }
    setModesIntro(false)
  }
  // "Submit a Way" lives in the right sidebar (components/SubmitWayPanel), in the place the read-only Procedure record
  // used to take. There is no second copy over the chat: one form, one place to write a Way. It used to hop to the
  // website's rooms with ?panel=submit, and that request left before their view existed, so the button looked dead.
  // The command palette (Ctrl/Cmd-K): app actions and "go to file", with the last few choices first.
  const [palette, setPalette] = useState(false)
  const [paletteQ, setPaletteQ] = useState("")
  const [paletteIdx, setPaletteIdx] = useState(0)
  const [fileHits, setFileHits] = useState<{ name: string; path: string }[]>([])
  // Chats from every folder that contain the query (app/api/search), and the hit a click is opening so the chat can
  // scroll to that line once it has loaded (2026-11-17).
  type ChatHit = { id: string; sessionID: string; title: string; directory: string | null; at: number; snippet: string }
  const [chatHits, setChatHits] = useState<ChatHit[]>([])
  const pendingHit = useRef<{ sid: string; at: number } | null>(null)
  const scrollHit = (hit: { at: number }): boolean => {
    const el = logContentRef.current?.querySelector<HTMLElement>(`[data-at="${hit.at}"]`)
    if (!el) return false
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    el.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" })
    el.classList.add("dk-found")
    window.setTimeout(() => el.classList.remove("dk-found"), 1600)
    return true
  }
  const [stopping, setStopping] = useState(false)
  const [stopNote, setStopNote] = useState("")
  // The chats folder (lib/home.ts): kell without a project. Its path comes from the server; it shows as "Chats".
  const [homePath, setHomePath] = useState("")
  const homeRef = useRef("")
  const samePath = (a: string, b: string) => !!a && !!b && a.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() === b.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
  const isChats = (d: string) => samePath(d, homePath || homeRef.current)
  const projName = (d: string) => (isChats(d) ? "Chats" : d.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || d)
  const loadHome = async (): Promise<string> => {
    if (homeRef.current) return homeRef.current
    try {
      const j = await (await fetch("/api/home")).json()
      if (j.path) { homeRef.current = j.path; setHomePath(j.path) }
    } catch { /* the folder picker still works */ }
    return homeRef.current
  }

  useEffect(() => {
    fetch("/api/engine").then((r) => r.json()).then(async (j) => {
      setEngine({ up: !!j.up })
      // The folder and the chat to reopen come from the app's own store (app/api/last): the page's localStorage is
      // wiped on every start, which is why the app used to open on nothing and look like the chats were gone. This runs
      // whether the engine is already up or not: returning early on a warm start left the app with no project and no
      // session, so the sidebar showed another project's chats and opening one looked like a switch that did nothing
      // (2026-11-16).
      let last: { directory?: string | null; sessionID?: string | null } | null = null
      try { last = (await (await fetch("/api/last")).json()).last } catch { /* none yet */ }
      if (last?.directory) {
        setDirectory(last.directory)
        dirRef.current = last.directory   // the local list can load before the engine has finished starting
        setSideView("project")            // the restored project's sessions are the point of opening it
        void loadSessions()
        void openProject(last.directory, last.sessionID || undefined)
        void loadHome()
        return
      }
      // Nothing to reopen (a first start): open the chats folder, so the first thing a person sees is a box to ask in,
      // not "open a folder" (P0 #1, 2026-11-17).
      const home = await loadHome()
      if (home) { setSideView("project"); void openProject(home); return }
      if (j.up) void loadSessions()
    })
    fetch("/api/settings").then((r) => r.json()).then(setSettings).catch(() => undefined)
    // The model list can be empty on the first read (a personal lane fetches it from the gateway when the engine is up),
    // and an empty list is why a model picker could look like it had nothing to offer. Read again until it has one.
    let tries = 0
    const t = window.setInterval(() => {
      tries += 1
      if (tries > 20) { window.clearInterval(t); return }             // ~1 min: then the next sign-in or Settings read fills it
      void fetch("/api/settings").then((r) => r.json()).then((j) => {
        setSettings(j)
        if (Array.isArray(j?.catalog) && j.catalog.length) window.clearInterval(t)
      }).catch(() => undefined)
    }, 3_000)
    return () => window.clearInterval(t)
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
  // The middle column follows new chat content only while the chat is showing. An agent view is a fixed report, not a
  // stream: opening one starts at its head, and going back to the chat returns to the latest message (2026-11-16).
  useEffect(() => {
    if (agentView === "main") { scrollToEnd({ instant: true }); return }
    const el = logRef.current
    if (el) el.scrollTop = 0
  }, [agentView, scrollToEnd])
  useEffect(() => { if (agentView === "main") follow() }, [lines, steps, busy, agentView, follow])
  // A view whose agent is gone (a new session, a new turn, the picker opening) returns to the chat instead of leaving a
  // stale selection in the panel.
  useEffect(() => {
    if (agentView === "delegated" && !delegation?.model) setAgentView("main")
    else if (agentView.startsWith("sub:") && !subagents.some((s) => `sub:${s.id}` === agentView)) setAgentView("main")
  }, [agentView, delegation?.model, subagents])
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
      // Backfill once from the engine, then read the app's own store (docs/localdb.md). Timeouts keep a starting or
      // dead engine from holding the sidebar and the last chat behind it (2026-11-16).
      await fetch("/api/local/sync", { method: "POST", signal: AbortSignal.timeout(8_000) }).catch(() => undefined)
      const dir = dirRef.current
      const j = await (await fetch(`/api/local/sessions${dir ? `?directory=${encodeURIComponent(dir)}` : ""}`, { signal: AbortSignal.timeout(10_000) })).json()
      setSessions(Array.isArray(j.sessions) ? j.sessions : [])
      const p = await (await fetch("/api/sessions?projects=1", { signal: AbortSignal.timeout(8_000) })).json()
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
        // The engine's own "UnexpectedStatus: 500" says nothing a person can act on; the usual cause was a retry after a
        // half-written import, which fresh message ids now avoid (2026-11-17).
        const why = (e: string) => /UnexpectedStatus:\s*500/.test(e)
          ? "the engine refused it (500). Try importing it again; if it keeps failing, restart kell."
          : String(e || "")
        setImportNote(`${ids.length - failed.length} imported; ${failed.length} could not be: ${why(String(failed[0].error || ""))}`)
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
    if (id === sessionID) return
    // Opening a chat never stops a turn: a turn that is still running belongs to the chat it started in, and this pane
    // simply shows the chat the person picked (2026-11-17, several chats may answer at once).
    const seq = ++sessionSeq.current
    onScreen.current = id                                // the stream stops writing to the pane in this same tick
    // On a narrow window the project list is a drawer over the chat: picking a chat closes it, or the drawer keeps
    // covering the conversation the click just opened (2026-11-16).
    if (typeof window !== "undefined" && window.innerWidth <= 760) setLeftOpen(false)
    setSideView("project")                                  // the open chat belongs to a project, and its list is there
    setQueued([]); setSteps([]); setSubagents([]); subagentIds.current.clear(); setSessionModel(null); setLedger([]); setSpend(0)
    setDelegateLog([]); setDelegation(null); setDelegateStatus(""); planRef.current = null
    setSessLoading(id)
    setSessionID(id); setLines([])
    void fetch("/api/last", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: id }) }).catch(() => undefined)
    try {
      // The app's own history first (docs/localdb.md); the engine is only the one-time backfill source. A timeout keeps
      // one dead engine call from making every click look like a switch that does nothing (2026-11-16).
      let j = await (await fetch(`/api/local/sessions?id=${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(15_000) })).json()
      if (j.error || !Array.isArray(j.lines)) {
        j = await (await fetch(`/api/sessions?id=${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(15_000) })).json()
      }
      // The local store can hold the session row with no messages yet (older and imported chats): read the engine's own
      // record before showing an empty conversation. The local route returns `lines: []`, not an error, in that case.
      if (!Array.isArray(j.lines) || j.lines.length === 0) {
        const e = await fetch(`/api/sessions?id=${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(15_000) })
          .then((r) => r.json()).catch(() => null)
        if (e && Array.isArray(e.lines) && e.lines.length) j = e
      }
      if (seq !== sessionSeq.current) return          // a newer click won
      setLines(Array.isArray(j.lines) ? j.lines : [])
      // A chat that is still answering shows what it has written so far, then any card that arrived while it was in the
      // background (a permission waits there for the person's answer).
      if (runningRef.current[id] && turnText.current[id]) {
        const sofar = turnText.current[id]
        setLines((l) => [...l, { who: "assistant", text: sofar, at: Date.now() }])
      }
      const queued = pendingLines.current.filter((x) => x.sid === id)
      if (queued.length) {
        pendingLines.current = pendingLines.current.filter((x) => x.sid !== id)
        setLines((l) => [...l, ...queued.map((x) => x.line)])
      }
      // A search hit opened this chat: scroll to that line once the pane has drawn it.
      const hit = pendingHit.current
      if (hit?.sid === id) {
        pendingHit.current = null
        let tries = 0
        const go = () => { if (scrollHit(hit) || ++tries > 12) return; window.setTimeout(go, 120) }
        window.setTimeout(go, 80)
      }
      // The agents panel is rebuilt from the engine's own record (the app's local history keeps text, not tool calls), so
      // the subagents this chat already ran are still listed after a restart or when an older chat is opened.
      if (Array.isArray(j.subagents)) setSubagents(j.subagents)
      else {
        try {
          const s = await (await fetch(`/api/sessions?id=${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(15_000) })).json()
          if (Array.isArray(s.subagents)) setSubagents(s.subagents)
          if (typeof s.model === "string") setSessionModel(s.model)
        } catch { /* the panel is an addition; the chat still opens */ }
      }
      if (typeof j.model === "string") setSessionModel(j.model)
    } catch { if (seq === sessionSeq.current) setLines([{ who: "assistant", text: "kell could not read this session.", at: Date.now() }]) }
    finally { if (seq === sessionSeq.current) setSessLoading(null) }
    if (seq === sessionSeq.current) loadChanges(id)
  }

  // A new session: an empty chat in this project. Every per-turn and per-search state goes with it. Without that, the
  // last chat's picker, delegation, queue, or "opening" state stayed over the empty chat, and the `+` looked like it did
  // nothing while an Ask-mode picker was open (2026-11-16). A turn still running keeps running in its own chat.
  const newSession = () => {
    setNewKey(`new:${Date.now().toString(36)}`)          // this empty chat is its own chat, even while another answers
    sessionSeq.current++                                  // a chat still loading must not fill this one
    planSeq.current++                                     // a procedure search still running must not land here
    turnGen.current++                                     // nor may its picker open over the new chat
    onScreen.current = null
    setSessionID(null)
    setLines([])
    setSteps([])
    setSubagents([]); subagentIds.current.clear()
    setSessionModel(null); setLedger([]); setSpend(0)
    setDelegateLog([]); setDelegateAnswer(""); setDelegateErr(""); setDelegateNote(""); setDelegateAsk("")
    setDelegation(null); planRef.current = null; planErr.current = ""
    setDelegateOptions([]); setDelegateGoals([]); setPendingTurn(null); setDelegateStatus("")
    setQueued([]); setStopNote(""); setSessLoading(null)
    setAgentView("main")
    loadChanges(null)
  }

  // A rooms ask running in a session (lib/remote.ts): followed live when it is the chat on screen, and when it finishes that chat
  // and the sessions list are read again, so what was asked from the rooms shows here too.
  const [remoteLive, setRemoteLive] = useState<{ sessionID: string; question: string; text: string; from: string } | null>(null)
  const remoteSeen = useRef(0)
  const onScreen = useRef<string | null>(null)
  onScreen.current = sessionID
  // Chats answering right now, by chat key: a session id, or "new:<n>" until the engine names a new chat. More than one
  // can run at once (2026-11-17): each turn owns its own stream, and only the chat on screen draws it.
  const [running, setRunning] = useState<Record<string, { sid: string | null }>>({})
  const runningRef = useRef<Record<string, { sid: string | null }>>({})
  const runningWrite = (next: Record<string, { sid: string | null }>) => { runningRef.current = next; setRunning(next) }
  const [newKey, setNewKey] = useState(() => `new:${Date.now().toString(36)}`)
  const chatKey = sessionID ?? newKey
  const chatKeyRef = useRef(chatKey)
  chatKeyRef.current = chatKey
  const runningIds = useMemo(
    () => new Set(Object.values(running).map((t) => t.sid).filter((x): x is string => !!x)),
    [running],
  )
  // The answer each running chat has written so far, so opening one mid-turn shows what has been said, not a blank pane.
  const turnText = useRef<Record<string, string>>({})
  const pendingLines = useRef<Array<{ sid: string; line: Line }>>([])   // a blocking event seen while another chat was open
  // A finished chat's notice in the app, and the device notification that goes with it.
  const [doneNote, setDoneNote] = useState<{ id: string; title: string; why?: string } | null>(null)
  const doneTimer = useRef(0)
  const chatTitle = (sid: string) => sessionsRef.current.find((s) => s.id === sid)?.title || "Your chat"
  const notifyDone = (id: string, title: string, why?: string) => {
    setDoneNote({ id, title, why })
    if (doneTimer.current) window.clearTimeout(doneTimer.current)
    doneTimer.current = window.setTimeout(() => setDoneNote(null), 12_000)
    try {
      if (typeof Notification === "undefined") return
      const show = () => {
        const n = new Notification(why ? "kell needs you" : "The chat finished", { body: title, tag: `kell-${id}` })
        n.onclick = () => { window.focus(); void openSession(id); n.close() }
      }
      if (Notification.permission === "granted") show()
      else if (Notification.permission === "default") void Notification.requestPermission().then((p) => { if (p === "granted") show() })
    } catch { /* a notification is an addition */ }
  }
  // The chat on screen is the one whose turn the composer's state (busy, Stop, Thinking) follows. A turn in another chat
  // never blocks this one.
  useEffect(() => {
    const b = !!running[chatKey]
    setBusy(b); busyRef.current = b
  }, [running, chatKey])
  // Each running turn's request, so Stop can end it here at once (2026-11-18), keyed by the chat's key and its session id.
  const turnCtl = useRef<{ key: string; sid: string; ctl: AbortController }[]>([])
  const beginTurn = (key: string, sid: string | null) => { if (!runningRef.current[key]) runningWrite({ ...runningRef.current, [key]: { sid } }) }
  const endTurn = (key: string) => {
    if (!key || !runningRef.current[key]) return
    const next = { ...runningRef.current }
    delete next[key]
    runningWrite(next)
  }
  // A new chat gets its id from the engine on its first prompt: the running entry follows the new name.
  const moveTurn = (from: string, to: string) => {
    if (!from || !to || from === to || !runningRef.current[from]) return
    const next = { ...runningRef.current }
    next[to] = { ...next[from], sid: to }
    delete next[from]
    runningWrite(next)
  }
  useEffect(() => {
    const tick = async () => {
      try {
        const j = await (await fetch("/api/remote")).json()
        setRemoteLive(j.live?.sessionID ? j.live : null)
        const at = Number(j.touched?.at || 0)
        if (at > remoteSeen.current) {
          const first = remoteSeen.current === 0
          remoteSeen.current = at
          if (first) return
          void loadSessions()
          const id = String(j.touched.sessionID || "")
          if (!j.live && id && id === onScreen.current && !busyRef.current) {
            const r = await (await fetch(`/api/local/sessions?id=${encodeURIComponent(id)}`)).json()
            if (Array.isArray(r.lines) && onScreen.current === id && !busyRef.current) setLines(r.lines)
          }
        }
      } catch { /* the app's server is restarting */ }
    }
    const t = window.setInterval(() => void tick(), 2500)
    return () => window.clearInterval(t)
  }, [loadSessions])

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

  // A prompt to send once a chat is ready: the quick-ask box and "edit and resend" open a chat first (a new one, a fork, or
  // the chats folder) and the prompt goes when that chat is the one on screen with its engine up (2026-11-17).
  const [autoSend, setAutoSend] = useState<{ text: string; sid: string | null; dir?: string } | null>(null)
  useEffect(() => {
    if (!autoSend || opening || !engine?.up || !directory || sessLoading) return
    if (autoSend.dir && !samePath(autoSend.dir, directory)) return
    if ((autoSend.sid ?? null) !== (sessionID ?? null)) return
    const t = autoSend.text
    setAutoSend(null)
    void send(t, [])
  })

  // Edit and resend / regenerate (P0 #9): the chat is forked just before the prompt (the original stays as it was, its own
  // chat in the list) and the new or same text is sent there. The first prompt of a chat has nothing before it: a new chat.
  const [editing, setEditing] = useState<{ i: number; value: string } | null>(null)
  const branchSend = async (i: number, newText: string) => {
    const t = newText.trim()
    if (!t || busy) return
    setEditing(null)
    let j = i - 1
    while (j >= 0 && lines[j].who !== "assistant") j--
    if (!sessionID || j < 0) { newSession(); setAutoSend({ text: t, sid: null }); return }
    try {
      const r = await fetch("/api/fork", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ sessionID, upTo: { who: "assistant", text: lines[j].text, at: lines[j].at } }) })
      const out = await r.json()
      if (!r.ok || !out.sessionID) { appendNote(`edit: ${out.error || "could not branch this chat"}`); return }
      void loadSessions()
      setAutoSend({ text: t, sid: out.sessionID })
      await openSession(out.sessionID)
    } catch (err: any) { appendNote(`edit: ${String(err?.message || err)}`) }
  }

  // Documents kell made in this chat (components/DocPanel.tsx): read from kell-docs' log after every turn, shown as cards under the
  // answer that made them, opened in the preview pane.
  const [docs, setDocs] = useState<Doc[]>([])
  const [docOpen, setDocOpen] = useState<Doc | null>(null)
  const firstAt = lines.find((l) => l.at)?.at
  useEffect(() => {
    if (!directory || !firstAt) { setDocs([]); return }
    let live = true
    fetch(`/api/docs?since=${firstAt - 60_000}&dir=${encodeURIComponent(directory)}`).then((r) => r.json())
      .then((j) => { if (live) setDocs(Array.isArray(j.docs) ? j.docs : []) }).catch(() => undefined)
    return () => { live = false }
  }, [directory, sessionID, busy, firstAt])
  useEffect(() => { setDocOpen(null) }, [sessionID, directory])
  /** The documents made during the turn this answer ends: only the last answer before the next prompt carries them. */
  const docsAfter = (i: number): Doc[] => {
    if (!docs.length || lines[i]?.who !== "assistant") return []
    let n = i + 1
    while (n < lines.length && lines[n].who !== "user") { if (lines[n].who === "assistant") return []; n++ }
    let u = i - 1
    while (u >= 0 && lines[u].who !== "user") u--
    const lo = u >= 0 ? (lines[u].at ?? 0) : 0
    const hi = n < lines.length ? (lines[n].at ?? Infinity) : Infinity
    return docs.filter((d) => d.at > lo && d.at <= hi)
  }

  // What waits on the person (components/Attention.tsx): polled every 15 s; a new item is announced once (notification + badge).
  const [inboxItems, setInboxItems] = useState<InboxItem[]>([])
  const [showInbox, setShowInbox] = useState(false)
  const [showSched, setShowSched] = useState(false)
  const [showConn, setShowConn] = useState(false)
  const seenInbox = useRef<Set<string> | null>(null)
  const loadInbox = useCallback(async () => {
    try {
      const j = await (await fetch("/api/inbox", { signal: AbortSignal.timeout(8_000) })).json()
      const items: InboxItem[] = Array.isArray(j.items) ? j.items : []
      setInboxItems(items)
      const before = seenInbox.current
      seenInbox.current = new Set(items.map((x) => x.id))
      if (!before) return                                    // the first read only sets what is already known
      const fresh = items.filter((x) => !before.has(x.id) && !(x.kind === "permission" && x.sessionID === sessionIDRef.current))
      if (fresh.length && typeof Notification !== "undefined" && Notification.permission === "granted") {
        const x = fresh[0]
        const n = new Notification("kell needs you", { body: x.kind === "permission" ? `${x.title}: ${x.what}` : `${x.title}: ${x.status === "needs_you" ? "needs you" : x.status}`, tag: `kell-inbox-${x.id}` })
        n.onclick = () => { window.focus(); setShowInbox(true); n.close() }
      }
    } catch { /* the badge keeps its last count */ }
    // A scheduled run that finished since the last look: its chat joins the sidebar, and the person hears about it once.
    try {
      const sj = await (await fetch("/api/schedules", { signal: AbortSignal.timeout(8_000) })).json()
      const ends = (Array.isArray(sj.schedules) ? sj.schedules : []).map((s: any) => ({ s, r: s.runs?.[0] })).filter((x: any) => x.r?.ended)
      const newest = Math.max(0, ...ends.map((x: any) => Number(x.r.ended)))
      const before = schedSeen.current
      schedSeen.current = Math.max(before, newest)
      if (before && newest > before) {
        void loadSessions()
        const done = ends.find((x: any) => Number(x.r.ended) === newest)
        if (done && done.r.status === "done" && typeof Notification !== "undefined" && Notification.permission === "granted") {
          const n = new Notification(`Scheduled: ${done.s.title}`, { body: String(done.r.summary || "Finished").slice(0, 180), tag: `kell-sched-${done.r.at}` })
          n.onclick = () => { window.focus(); if (done.r.sessionID) void openAnyChatRef.current(done.r.sessionID); n.close() }
        }
      }
    } catch { /* next time */ }
  }, [loadSessions])
  const schedSeen = useRef(0)
  const openAnyChatRef = useRef<(sid: string) => Promise<void>>(async () => {})
  useEffect(() => { void loadInbox(); const t = window.setInterval(() => void loadInbox(), 15_000); return () => window.clearInterval(t) }, [loadInbox])
  /** Open a chat from anywhere (the inbox, a schedule): switch to its folder first when it lives in another one. */
  const openAnyChat = async (sid: string) => {
    setShowInbox(false); setShowSched(false)
    try {
      const j = await (await fetch(`/api/local/sessions?id=${encodeURIComponent(sid)}`)).json()
      const dir = (sessions.find((x) => x.id === sid)?.directory) || j?.session?.directory
      if (dir && (!directory || !samePath(dir, directory))) { setSideView("project"); await openProject(dir, sid); return }
    } catch { /* open it here */ }
    void openSession(sid)
  }
  openAnyChatRef.current = openAnyChat

  // The quick-ask box (Ctrl+Shift+Space, electron/presence.ts): a new chat in the chats folder, or in the open folder while
  // a turn is running there (switching folders would end it).
  const quickRef = useRef<(t: string) => void>(() => {})
  quickRef.current = (t: string) => {
    const running = Object.keys(runningRef.current).length > 0
    if (directory && (isChats(directory) || running)) { newSession(); setAutoSend({ text: t, sid: null }); return }
    void (async () => {
      const h = await loadHome()
      if (!h) { if (directory) { newSession(); setAutoSend({ text: t, sid: null }) } return }
      setSideView("project")
      setAutoSend({ text: t, sid: null, dir: h })
      await openProject(h)
    })()
  }
  useEffect(() => desktop()?.onQuick?.((t) => quickRef.current(t)), [])

  // Renaming a chat: the pencil on its row, double-clicking the row, or the command palette puts an input in its place.
  // Enter or leaving the box saves, Escape cancels. The app's store is the name the sidebar reads; the route also tells
  // the engine, so its own header agrees (2026-11-17).
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null)
  const renamingRef = useRef<{ id: string; value: string } | null>(null)
  renamingRef.current = renaming
  const renameCancelled = useRef(false)
  const renameSaving = useRef(false)
  const startRename = (x: { id: string; title?: string }) => {
    renameCancelled.current = false
    setRenaming({ id: x.id, value: x.title ?? sessions.find((s) => s.id === x.id)?.title ?? "" })
  }
  const commitRename = async () => {
    const cur = renamingRef.current
    if (!cur || renameSaving.current) return
    setRenaming(null)
    if (renameCancelled.current) { renameCancelled.current = false; return }
    const title = cur.value.replace(/\s+/g, " ").trim()
    if (!title) return
    renameSaving.current = true
    setSessions((list) => list.map((x) => (x.id === cur.id ? { ...x, title } : x)))     // the row changes at once
    try {
      const r = await fetch("/api/sessions/rename", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: cur.id, title }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || `the rename answered ${r.status}`)
      void loadSessions()
    } catch (err) {
      appendNote(`Could not rename the chat: ${String((err as any)?.message || err)}`)
      void loadSessions()
    } finally { renameSaving.current = false }
  }

  // The row's "..." menu: Rename and Delete. It is fixed at the button's place, so the sidebar's own scroll cannot
  // clip it (2026-11-17).
  const [sessMenu, setSessMenu] = useState<{ id: string; x: number; y: number } | null>(null)
  const [sessDelete, setSessDelete] = useState<string | null>(null)
  // Filing a chat (P1, 2026-11-17): pin, archive, folder. The sidebar shows Pinned, then each folder, then the rest; archived chats
  // sit behind "Archived" at the end. The change shows at once and is saved in the app's store.
  const [folderAsk, setFolderAsk] = useState<string | null>(null)
  const [folderName, setFolderName] = useState("")
  const [showArchived, setShowArchived] = useState(false)
  const [closedFolders, setClosedFolders] = useState<Set<string>>(new Set())
  const fileChat = async (id: string, patch: { pinned?: boolean; archived?: boolean; folder?: string | null }) => {
    setSessions((list) => list.map((x) => (x.id === id ? { ...x, ...patch, ...(patch.archived ? { pinned: false } : {}) } : x)))
    try { await fetch("/api/sessions/organize", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id, ...patch }) }) }
    catch { /* the next refresh shows what the store has */ }
  }
  const openSessMenu = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setSessMenu({
      id,
      x: Math.min(window.innerWidth - 196, Math.max(8, r.right - 184)),
      y: Math.min(window.innerHeight - 280, r.bottom + 4),
    })
    setSessDelete(null)
  }
  useEffect(() => {
    if (!sessMenu) return
    const away = (e: PointerEvent) => { if (!(e.target as Element | null)?.closest?.(".dk-sess-menu")) setSessMenu(null) }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setSessMenu(null); setSessDelete(null) } }
    document.addEventListener("pointerdown", away)
    document.addEventListener("keydown", esc)
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc) }
  }, [sessMenu])

  const sessionRow = (x: { id: string; title: string; updated: number }) => (
    renaming?.id === x.id ? (
      <form key={x.id} className="dk-sess-row dk-sess-renaming" onSubmit={(e) => { e.preventDefault(); void commitRename() }}>
        <input className="dk-sess-name" autoFocus value={renaming.value} aria-label="Chat name" placeholder="Name this chat"
          onChange={(e) => setRenaming({ id: x.id, value: e.target.value })}
          onBlur={() => void commitRename()}
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); renameCancelled.current = true; setRenaming(null) }
          }} />
      </form>
    ) : (
      <div className="dk-sess-row" key={x.id} data-here={x.id === sessionID || undefined} data-live={runningIds.has(x.id) || undefined} onDoubleClick={() => startRename(x)}>
        <button type="button" className="dk-sess-open" title={`${x.title || "Untitled session"} (double-click to rename)`}
          onClick={() => void openSession(x.id)}>
          <span className="nm">{x.title || "Untitled session"}</span>
          {runningIds.has(x.id)
            ? <span className="dk-sess-live" role="img" aria-label="Answering" title="kell is still answering in this chat"><span className="dg-pulse" aria-hidden="true" /></span>
            : <span className="when">{x.updated ? when(x.updated) : ""}</span>}
        </button>
        <button type="button" className="dk-sess-more-btn" aria-label={`Actions for ${x.title || "this chat"}`}
          title="More actions" aria-haspopup="menu" aria-expanded={sessMenu?.id === x.id || undefined}
          onClick={(e) => openSessMenu(e, x.id)} onDoubleClick={(e) => e.stopPropagation()}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <circle cx="12" cy="5" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="12" cy="19" r="1.6" />
          </svg>
        </button>
      </div>
    )
  )

  // Open project: with no folder typed, the system's folder picker first (in the app). Says what it is doing and what happened:
  // it used to change nothing on screen when it worked, so it looked broken (2026-11-06).
  const openProject = async (picked?: string, session?: string) => {
    let dir = picked ?? directory
    if (!dir.trim()) {
      const chosen = await desktop()?.pickFolder?.()
      if (!chosen) return
      dir = chosen; setDirectory(chosen)
    }
    // The pane belongs to the new folder from this moment: an empty chat and its own sessions, never the last project's,
    // while the engine boots. Waiting for the engine left the old conversation under the new name and looked like the
    // folder never opened (2026-11-16). A running turn is ended by the engine switch itself, so say so.
    const endedTurn = Object.keys(runningRef.current).length > 0
    sessionSeq.current++                                  // a chat still loading must not fill the new project
    planSeq.current++                                     // a procedure search must not open its picker there
    turnGen.current++
    onScreen.current = null
    setSideView("project")                                // an open project shows its sessions, not the project list
    setEngine(null); setOpening(true)
    dirRef.current = dir
    // The engine serves one folder: the old project's turns cannot outlive the switch. Their streams will end on their
    // own; the pane starts clean, with no stale running dots or notices from the previous project (2026-11-17).
    runningWrite({}); turnText.current = {}; pendingLines.current = []; setDoneNote(null)
    setNewKey(`new:${Date.now().toString(36)}`)
    setDirectory(dir); setSessionID(null); setSessions([])
    setQueued([]); setLines([]); setSteps([]); setSubagents([]); subagentIds.current.clear(); setSessionModel(null); setLedger([]); setSpend(0); setDelegateLog([]); loadChanges(null)
    setPendingTurn(null); setDelegateOptions([]); setDelegateGoals([]); setDelegation(null); planRef.current = null
    setDelegateAnswer(""); setDelegateErr(""); setDelegateNote(""); setDelegateStatus(""); setDelegateAsk(""); setSessLoading(null)
    setStopNote(endedTurn ? "Opening another folder ended the turn that was running. Steps that already ran stay." : "")
    setAgentView("main")
    let j: { up?: boolean; error?: string }
    try {
      const r = await fetch("/api/engine", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory: dir }) })
      j = await r.json()
    } catch (err) { j = { up: false, error: err instanceof Error ? err.message : String(err) } }
    finally { setOpening(false) }
    setEngine(j.up ? { up: true } : { up: false, error: j.error || "the engine did not start" })
    if (j.up) remember(dir)
    if (j.up) {
      // The folder (and the chat to reopen, when the app is restoring the last one) is kept in the app's store.
      void fetch("/api/last", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory: dir, sessionID: session ?? null }) }).catch(() => undefined)
    }
    // The app's own store is read even when the engine is down: the chats are still there to read, and the last chat
    // opens from that store without waiting for an engine that may never come up.
    await loadSessions()
    if (session) void openSession(session)
  }

  // Open a folder: the picker, a recent project, or File > Open folder. The engine serves one folder, so switching ends a
  // running turn; the button is never dead while a model works (2026-11-16).
  const openFolder = async (dir?: string) => {
    let d = dir
    if (!d) {
      if (!desktop()?.pickFolder) { setSideView("project"); void openProject(); return }
      d = (await desktop()?.pickFolder?.()) ?? undefined
      if (!d) return
    }
    await openProject(d)
  }

  const appendAssistant = (delta: string) => setLines((l) => {
    const last = l.at(-1)
    if (last?.who === "assistant") { const c = l.slice(0, -1); c.push({ who: "assistant", text: last.text + delta, at: last.at }); return c }
    return [...l, { who: "assistant", text: delta, at: Date.now() }]
  })
  // What the model thought before it answered: its own block in the transcript, collapsed under "Thoughts". Kept apart
  // from the reply so a copied answer never carries the reasoning, and hidden by the bar's Thoughts switch.
  const appendThought = (delta: string) => setLines((l) => {
    const last = l.at(-1)
    if (last?.who === "thought") { const c = l.slice(0, -1); c.push({ ...last, text: last.text + delta }); return c }
    return [...l, { who: "thought", text: delta, at: Date.now() }]
  })

  // The prompt an answer came from, for "Ask again".
  const lastUserBefore = (i: number) => { for (let k = i - 1; k >= 0; k--) if (lines[k].who === "user") return lines[k].text; return undefined }
  const copyText = async (t: string) => { try { await navigator.clipboard.writeText(t); return true } catch { return false } }

  // ---- Attachments -----------------------------------------------------------------------------------------------
  // The cheapest path wins: a text file inside the project is sent as a path reference (the agent reads it with its
  // own tools); only images, PDFs and files outside the project are attached as bytes, and a picture's bytes are read
  // properly (downscaled, lib/attach.ts) whatever its path. Files on disk reach the model through /api/attach/read.
  const addPaths = async (paths: string[]) => {
    if (!paths.length || !directory) return
    try {
      const r = await fetch("/api/attach", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ directory, paths }) })
      const j = await r.json()
      if (!r.ok) { setConsoleNote(String(j.error || "Could not attach that file")); return }
      // Read the pictures and PDFs first, in one call: an image's path is not its picture, and the engine needs the bytes.
      let blobs: Record<string, { dataUrl: string; mime: string }> = {}
      try {
        const read = await fetch("/api/attach/read", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ paths }) })
        const rb = await read.json()
        if (read.ok && Array.isArray(rb.files)) for (const f of rb.files) blobs[String(f.name)] = { dataUrl: String(f.dataUrl), mime: String(f.mime) }
      } catch { /* the text references below still work */ }
      const next: Attachment[] = (Array.isArray(j.files) ? j.files : []).map((f: any) => ({
        id: crypto.randomUUID(), name: String(f.name || "attachment"), size: Number(f.size) || 0,
        mime: String(f.mime || "application/octet-stream"), path: String(f.path),
        ...(f.rel ? { rel: String(f.rel) } : {}),
        ...(blobs[String(f.name)] ? { mime: blobs[String(f.name)].mime, dataUrl: blobs[String(f.name)].dataUrl } : {}),
      }))
      setAttachments((a) => [...a, ...next].slice(0, MAX_ATTACHMENTS))
    } catch { setConsoleNote("Could not attach that file") }
  }
  const addBrowserFiles = async (files: File[]) => {
    const room = MAX_ATTACHMENTS - attachments.length
    if (files.length > room) setConsoleNote(`A message takes ${MAX_ATTACHMENTS} files at a time.`)
    const next: Attachment[] = []
    for (const f of files.slice(0, Math.max(0, room))) {
      if (f.size > MAX_ATTACH_BYTES) { setConsoleNote(`${f.name} is larger than 25 MB`); continue }
      const mime = f.type || "application/octet-stream"
      // A picture is never a path reference: the model has to see the bytes, wherever the file lives (lib/attach.ts).
      if (isImage(mime)) {
        try { next.push({ id: crypto.randomUUID(), name: f.name, size: f.size, mime, dataUrl: await imageDataUrl(f, mime) }) }
        catch { setConsoleNote(`Could not read ${f.name}`) }
        continue
      }
      const p = desktop()?.pathForFile?.(f) || (f as any).path
      if (p) { await addPaths([p]); continue }
      try { next.push({ id: crypto.randomUUID(), name: f.name, size: f.size, mime, dataUrl: await readDataUrl(f) }) }
      catch { setConsoleNote(`Could not read ${f.name}`) }
    }
    if (next.length) setAttachments((a) => [...a, ...next].slice(0, MAX_ATTACHMENTS))
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

  // Ask the router for the task's Goal and the Ways it offers. The route streams its progress, so the panel shows
  // "asking the router" instead of going silent. Nothing runs yet: the person picks a Way from the cards (or chooses to
  // run without one), and only then does the turn start.
  const planFor = async (text: string, sid: string | null, always = false): Promise<{ goal: Delegation["goal"]; options: WayOption[]; goals: GoalOption[]; skip?: boolean } | null> => {
    const seq = ++planSeq.current
    planErr.current = ""
    let settle: (p: { goal: Delegation["goal"]; options: WayOption[]; goals: GoalOption[]; skip?: boolean } | null) => void = () => undefined
    const planP = new Promise<{ goal: Delegation["goal"]; options: WayOption[]; goals: GoalOption[]; skip?: boolean } | null>((ok) => { settle = ok })
    void (async () => {
      let routed: { goal: Delegation["goal"]; options: WayOption[]; goals: GoalOption[]; skip?: boolean } | null = null
      try {
        const r = await fetch("/api/delegate", {
          method: "POST",
          headers: { "content-type": "application/json", accept: "text/event-stream" },
          body: JSON.stringify({ text, models: settings?.models || {}, timeoutMs: 20_000, always }),
        })
        if (!r.ok || !r.body) { planErr.current = `the procedure search answered ${r.status}`; settle(null); return }
        const reader = r.body.getReader()
        const dec = new TextDecoder()
        let buf = ""
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
            if (ev.type === "status" && seq === planSeq.current && onScreen.current === sid) setDelegateStatus(String(ev.text || ""))
            else if (ev.type === "plan" && ev.plan) {
              routed = { goal: ev.plan.goal ?? null,
                options: Array.isArray(ev.options) ? ev.options as WayOption[] : [],
                goals: Array.isArray(ev.goals) ? ev.goals as GoalOption[] : [],
                skip: ev.skip === true }
              if (seq === planSeq.current) {
                planRef.current = ev.plan as Delegation
                setDelegation(null)                    // nothing is applied until the person picks
                setDelegateLog([])                     // direct answers belonged to the plan being replaced
                setDelegateOptions(routed.options)
                setDelegateGoals(routed.goals)
                setDelegateNote("")
              }
              settle(routed)
            } else if (ev.type === "error" && seq === planSeq.current) {
              planErr.current = String(ev.error || "the router did not answer")
              setDelegateNote(planErr.current)
            }
          }
        }
        settle(routed)
      } catch (err) { planErr.current = err instanceof Error ? err.message : "the procedure search could not run"; settle(null) }
      finally { if (seq === planSeq.current && !routed) setDelegateStatus("") }
    })()
    // The router can take ~20 s cold; the turn is waiting on the choice anyway, so give it the full window.
    return await Promise.race([planP, new Promise<null>((ok) => setTimeout(() => ok(null), 60_000))])
  }

  // The palette's "Submit a Way": bring the sidebar's panel into view. It is always the same panel, so nothing is
  // opened twice and a submission already on screen is not replaced by a blank form.
  const openSubmitWay = () => {
    if (!rightOpen) toggleSidebar("right")
  }

  const send = async (again?: string, filesOverride?: Attachment[], asGoal = false) => {
    const body = again ?? text
    const outgoing = again === undefined ? attachments : (filesOverride ?? [])
    if (!asGoal && /^\/goal\b/i.test(body.trim())) { goalCommand.current(body.trim()); return }
    // /clear: a fresh chat with an empty context, in the same folder; the old chat stays in the list (2026-11-17).
    if (!asGoal && /^\/clear\s*$/i.test(body.trim())) {
      if (again === undefined) { setText(""); histPos.current = null }
      newSession()
      return
    }
    // /compact: the engine summarises the older turns of this chat and goes on from the summary.
    if (!asGoal && /^\/compact\s*$/i.test(body.trim())) {
      if (again === undefined) { setText(""); histPos.current = null }
      if (!sessionID) { appendNote("Nothing to compact yet: this chat has no turns."); return }
      appendNote("Compacting this chat: the older turns are being summarised…")
      void fetch("/api/compact", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID }) })
        .then(async (r) => { const j = await r.json().catch(() => ({})); appendNote(r.ok ? "Compacted. kell continues from a summary of the earlier turns." : `Couldn't compact: ${j.error || r.status}`) })
        .catch((err) => appendNote(`Couldn't compact: ${String(err?.message || err)}`))
      return
    }
    if (!asGoal && /^\/btw\b/i.test(body.trim())) { handleBtwCommand(body.trim()); if (again === undefined) { setText(""); histPos.current = null } return }
    // /model: the composer's own model pick, by name. `/model` alone lists what this lane offers; `/model chat` goes back
    // to kell's router. It never spends a turn.
    if (!asGoal && /^\/model\b/i.test(body.trim())) {
      const want = body.trim().replace(/^\/model\s*/i, "").trim()
      const list = settings?.catalog || []
      if (!want) {
        appendNote(list.length
          ? `Models on this lane: ${list.slice(0, 24).map(shortModel).join(", ")}${list.length > 24 ? ", …" : ""}. Type /model <name>, or /model chat to let kell's router choose.`
          : "No model list on this machine yet: Chat (kell's router) is the choice available.")
      } else if (/^(chat|auto|default)$/i.test(want)) {
        chooseChatModel("")
        appendNote("Model: Chat. kell's router picks the model for each task.")
      } else {
        const hit = list.find((m) => m.toLowerCase() === want.toLowerCase())
          ?? list.find((m) => shortModel(m).toLowerCase() === want.toLowerCase())
          ?? list.find((m) => shortModel(m).toLowerCase().includes(want.toLowerCase()))
        if (hit) { chooseChatModel(hit); appendNote(`Model: ${shortModel(hit)}. It runs the next turn, and this chat keeps it.`) }
        else appendNote(list.length
          ? `No model here is called "${want}". /model lists them.`
          : `No model list on this machine yet, so "${want}" cannot be chosen. Sign in again, or check Settings > Models.`)
      }
      if (again === undefined) { setText(""); histPos.current = null }
      return
    }
    if (!asGoal) {
      // Direct / Auto / Ask, switchable from the composer or by typing the command (the same names, one dispatcher).
      const m = /^\/(direct|agent|auto|ask)\s*$/i.exec(body.trim())
      if (m) {
        const mode = m[1].toLowerCase() as ProcMode
        chooseProcMode(mode)
        if (again === undefined) { setText(""); histPos.current = null }
        appendNote(mode === "direct"
          ? "Procedure mode: Direct. Prompts go straight to the model; kell does not search for a procedure."
          : mode === "agent"
            ? "Procedure mode: Agent. The model decides: it may search the web with kell's tools, run several sweeps and iterate. kell applies no stored Way."
            : mode === "auto"
              ? "Procedure mode: Auto. When a procedure fits, kell uses the best fit and says which one."
              : "Procedure mode: Ask. When a procedure fits, the ways appear here and kell waits for your pick.")
        return
      }
    }
    setAgentView("main")                                    // a new prompt is for the session, not the agent view
    // /model is answered above and needs no project; a prompt still does.
    if ((!body.trim() && !outgoing.length) || !directory) return
    // Only this chat's own turn blocks a send. Another chat answering is not this chat's business (2026-11-17).
    const key = sessionID ?? newKey
    if (runningRef.current[key]) {                            // still working here: keep it for when this turn ends
      setQueued((q) => [...q, { text: body, files: outgoing }])
      if (again === undefined) { setText(""); setAttachments([]); histPos.current = null }
      return
    }
    const sent = body
    const gen = turnGen.current
    beginTurn(key, sessionID)
    // The procedure search starts now, before the model, unless the person chose Direct or this is small talk. When it
    // runs, every task shows the Goals/Ways it found, even when none has a written Way. The model never starts the
    // search itself (2026-11-16). Ask searches whatever the task sounds like (the person asked to see the Ways); Auto
    // still lets JEV judge, because a Way applied on its own to ordinary work is a surprise, not a help.
    const skipSearch = procMode === "direct" || procMode === "agent" || CHAT_ONLY.test(sent.trim())
    const routedP = skipSearch ? null : planFor(sent, sessionID, procMode === "ask")
    if (asGoal) setLines((l) => [...l, { who: "goal", text: sent, at: Date.now() }])
    else setLines((l) => [...l, { who: "user", text: sent, at: Date.now(), files: outgoing }])
    setSteps([])
    setSubagents([]); subagentIds.current.clear(); setSessionModel(null)                                        // the agents panel is this turn's workers
    if (again === undefined) { setText(""); setAttachments([]); histPos.current = null }   // sending it again keeps what you were typing
    setBusy(true); busyRef.current = true
    setStopNote("")
    // The cards are for this request: ask while the turn runs (the tool router is fast; the model is not).
    if (skipSearch) { await runTurn(sent, outgoing, chatModel ? withChatModel(planFromOption(sent, settings?.models || {}, null, null)) : null, key, sessionID); return }
    const routed = await routedP
    // A new session was started while the search ran: its picker, card, or error belongs to the chat the person left.
    if (turnGen.current !== gen) { endTurn(key); return }
    // JEV judged this ordinary work: no picker, no card, the model starts as if kell held nothing.
    if (routed?.skip) { if (onScreen.current === sessionID) setDelegateStatus(""); await runTurn(sent, outgoing, null, key, sessionID); return }
    if (routed && procMode === "auto") {
      // Auto: the best-fitting Way is applied without a click. No Way means the model does the work, with no card.
      const best = routed.options[0] ?? null
      const plan = withChatModel(planFromOption(sent, settings?.models || {}, routed.goal, best))
      setDelegateOptions(routed.options)
      setDelegateGoals(routed.goals)
      setPendingTurn(null)
      if (onScreen.current === sessionID) {
        planRef.current = plan
        setDelegation(plan)
        setDelegateAnswer(""); setDelegateErr(""); setDelegateNote("")
        setDelegateStatus(best
          ? `Auto: using the best fit, ${best.name}${suggestLine(plan)}`
          : "Auto: no stored procedure fits - the model does the work")
      }
      await runTurn(sent, outgoing, plan, key, sessionID)
      return
    }
    if (routed) {
      // Nothing runs yet: the chat shows the Ways, or the Goals that matched with no written Way. The person picks a
      // Way or says to work on it without one.
      setPendingTurn({ text: sent, files: outgoing, asGoal, goal: routed.goal, sid: sessionID })
      setDelegateOptions(routed.options)
      setDelegateGoals(routed.goals)
      endTurn(key)
      if (onScreen.current === sessionID) setDelegateStatus(routed.options.length
        ? `${routed.options.length} ${routed.options.length === 1 ? "Way" : "Ways"} found - choose one in the chat`
        : "No stored procedure for this task - kell can still work on it")
      return
    }
    // The search could not run (a signed-out token, the server, the network). Do not let the model answer as if it
    // searched: say what happened and offer the sign-in / retry in the chat.
    endTurn(key)
    const why = planErr.current || "kell could not search the procedure catalog"
    if (onScreen.current === sessionID) setLines((l) => [...l, { who: "catalog", text: sent, server: why, at: Date.now() }])
    setDelegateStatus("")
    setDelegateNote("")
  }

  // Run one turn on the chosen Way (or on the engine's default when plan is null). `turn` is this chat's running entry:
  // its key (a session id, or the pending name of a new chat), its session id once known, and the text it has written.
  // Only the chat on screen draws the stream; the others keep their entry for the row's pulse and the notice when they
  // finish (2026-11-17: several chats can answer at once).
  const runTurnInner = async (sent: string, outgoing: Attachment[], plan: Delegation | null, turn: { key: string; sid: string; text: string }) => {
    if (plan && onScreen.current === turn.sid) { setDelegateAnswer(""); setDelegateErr("") }
    const live = () => !!turn.sid && onScreen.current === turn.sid
    const ctl = new AbortController()
    const mine = { key: turn.key, sid: turn.sid, ctl }
    turnCtl.current.push(mine)
    const payload = { sessionID: turn.sid || undefined, directory, text: sent, mode: procMode, model: plan?.model ?? undefined, plan: plan ?? undefined, files: outgoing.map(({ name, path, dataUrl, mime, rel, size }) => ({ name, path, dataUrl, mime, rel, size })) }
    const r = await fetch("/api/prompt", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload), signal: ctl.signal })
    if (!r.ok || !r.body) {
      const err = await r.text()
      if (live()) setLines((l) => [...l, { who: "assistant", text: `kell could not reach the engine: ${err.slice(0, 200)}`, at: Date.now() }])
      else if (turn.sid) notifyDone(turn.sid, chatTitle(turn.sid), "could not reach the engine")
      return
    }
    let buffer = ""
    let ended = false
    let failed = false
    let said = false
    // What the person asked for in the composer, and whether the engine ran something else (said once, not per step).
    const wantModel = chatModel
    let modelMismatch = false
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    // A blocking event (a permission, missing credit, an error) in a chat that is not on screen: keep its card for when
    // the person opens it, and tell them there. Never yank them out of the chat they are working in (2026-11-17).
    const forLater = (line: Line, why: string) => {
      if (!turn.sid) return
      pendingLines.current.push({ sid: turn.sid, line })
      notifyDone(turn.sid, chatTitle(turn.sid), why)
    }
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
          const nextSid = String(ev.sessionID || "")
          if (nextSid && nextSid !== turn.sid) {
            const from = turn.key
            const here = chatKeyRef.current === from
            turn.sid = nextSid
            mine.sid = nextSid
            turn.key = nextSid
            moveTurn(from, nextSid)
            // A new session's id belongs to the chat the turn was started in. If the person is still there, show it.
            if (here) { onScreen.current = nextSid; setSessionID(nextSid) }
            // app/api/prompt has already written the local row for this session, so the list can show the chat NOW --
            // while it answers -- instead of only when the turn ends (owner, 2026-11-20: a new chat was missing from the
            // sidebar until it finished). The row carries the live pulse while it runs.
            void loadSessions()
          }
          void fetch("/api/last", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: ev.sessionID }) }).catch(() => undefined)
        }
        else if (t === "session.text.delta") {
          const delta = String(d.delta ?? d.text ?? "")
          if (turn.sid && delta) { turnText.current[turn.sid] = (turnText.current[turn.sid] || "") + delta; said = true }
          if (live()) appendAssistant(delta)
        }
        else if (t === "session.reasoning.delta" || t === "session.thought.delta") {
          // The model's own words before the answer. The engine names it "reasoning"; the older name is kept so a
          // different engine build still shows it. Never merged into the reply (2026-11-19).
          const delta = String(d.delta ?? d.text ?? "")
          if (delta && live()) appendThought(delta)
        }
        else if (t === "session.tool.input.started") {
          // The tool's name comes only here; the called / success / failed events carry the call's id (2026-11-07: every chip read "tool").
          const id = String(d.id || ""), label = String(d.name || "tool")
          if (live()) {
            setSteps((s) => s.some((x) => x.id === id) ? s : [...s, { kind: "tool", label, id, state: "running" }])
            // A subagent shows in the agents panel from its first moment; `called` below fills in what it was asked to do.
            if (SUBAGENT_TOOL.test(label) && id) {
              subagentIds.current.add(id)
              setSubagents((a) => a.some((x) => x.id === id) ? a : [...a, { id, title: "Subagent", state: "running" }])
            }
          }
        }
        else if (t === "session.tool.called" || t === "session.tool.success" || t === "session.tool.failed") {
          const id = String(d.id || "")
          const state = t === "session.tool.called" ? "running" : t === "session.tool.success" ? "done" : "failed"
          const hint = t === "session.tool.called" ? hintOf(d.input) : undefined
          if (live()) setSteps((s) => s.some((x) => x.id === id)
            ? s.map((x) => (x.id === id ? { ...x, state, hint: hint ?? x.hint, note: state === "failed" ? "failed" : x.note } : x))
            : [...s, { kind: "tool", label: String(d.name || "tool"), id, state, hint, note: state === "failed" ? "failed" : undefined }])
          // The subagent's row: its one-line task while it runs, its answer (or failure) when it ends. The call's name is
          // not repeated on every event, so a known id is enough (2026-11-14: the row never filled in).
          if (t === "session.tool.success" && /run_id/.test(contentText(d.content))) {
            const at = runAtOf(contentText(d.content))
            if (at || /"status":\s*"(done|failed|cancelled)"/.test(contentText(d.content))) lastRun.current = at
          }
          if (id && SUBAGENT_TOOL.test(String(d.name || ""))) subagentIds.current.add(id)
          if (live() && id && subagentIds.current.has(id)) {
            if (t === "session.tool.called") {
              const title = subagentTitle(d.input)
              const brief = typeof d.input?.prompt === "string" ? d.input.prompt : undefined
              setSubagents((a) => a.some((x) => x.id === id)
                ? a.map((x) => (x.id === id ? { ...x, title, ...(brief ? { brief } : {}) } : x))
                : [...a, { id, title, state: "running", ...(brief ? { brief } : {}) }])
            } else {
              const result = t === "session.tool.success" ? contentText(d.content) : ""
              setSubagents((a) => a.map((x) => (x.id === id
                ? { ...x, state: t === "session.tool.success" ? "done" : "failed", ...(result ? { result } : {}) }
                : x)))
            }
          }
        }
        else if (t === "session.step.started") {
          const id = String(d.assistantMessageID || "")
          if (live() && id && d.model?.id) setLedger((rows) => [...rows.filter((r) => r.id !== id), { id, model: d.model.id, providerID: d.model.providerID }])
          // The model the person chose in the composer is a promise, not a hint: when the engine ran a different one (the
          // engine keeps its own model list and a choice from outside it is ignored), say so in one line here, once per
          // turn, rather than letting the chat claim a model that never ran (2026-11-18).
          if (live() && d.model?.id && wantModel && !modelMismatch) {
            const ran = `${d.model.providerID ? `${d.model.providerID}/` : ""}${d.model.id}`
            const same = ran === wantModel || d.model.id === wantModel.split("/").pop()
            if (!same) {
              modelMismatch = true
              appendNote(`You chose ${shortModel(wantModel)}, but the engine ran ${ran}: it does not offer that model on this lane. The turn is priced and reported as ${ran}.`)
            }
          }
        }
        else if (t === "session.step.ended") {
          const id = String(d.assistantMessageID || "")
          const patch = { tokens: tokensOf(d), cost: d.cost != null ? Number(d.cost) : undefined }
          if (live() && id) setLedger((rows) => rows.some((r) => r.id === id) ? rows.map((r) => (r.id === id ? { ...r, ...patch } : r)) : [...rows, { id, ...patch }])
          if (live() && d.cost != null) setSpend((c) => c + Number(d.cost))
        }
        else if (t === "session.compaction.started") {
          // automatic compaction (95% of the window) or /compact: say so, so a pause in the answer is explained
          if (live()) setLines((l) => [...l, { who: "assistant", text: "_Compacting the conversation: the older turns are being summarised so the chat fits the model's window…_", at: Date.now() }])
        }
        else if (t === "session.compaction.ended") {
          if (live()) setLines((l) => [...l, { who: "assistant", text: "_Compacted. kell goes on from a summary of the earlier turns._", at: Date.now() }])
        }
        else if (t === "permission.asked") {
          // the engine waits for this answer (reading a .env file asks first); without it the turn hung (2026-11-08, the e2e test)
          const what = [d.action, ...(Array.isArray(d.resources) ? d.resources.slice(0, 3) : [])].filter(Boolean).join(" ")
          const line: Line = { who: "permission", text: String(d.message || what || "a tool call"), sessionID: d.sessionID, requestID: String(d.id), at: Date.now() }
          if (live()) { said = true; setLines((l) => l.some((x) => x.requestID === d.id) ? l : [...l, line]) }
          else forLater(line, "needs your permission")
        }
        else if (t === "need_plan") {
          failed = true
          const run = lastRun.current ?? undefined
          const line: Line = { who: "plan", text: "", at: Date.now(), ...(run ? { run } : {}) }
          if (live()) { said = true; setLines((l) => [...l, line]) }
          else forLater(line, "needs your attention")
        }
        else if (t === "low_balance") {
          const line: Line = { who: "plan", text: "", at: Date.now(), low: { left: Number(ev.left) || 0, steps: Number(ev.steps) || 0 } }
          if (live()) { said = true; setLines((l) => [...l, line]) }
          else forLater(line, "is low on credit")
        }
        else if (t === "error") {
          failed = true
          if (needsWebSignIn(ev.error)) setEngine({ up: false, error: String(ev.error) })
          const line: Line = { who: "assistant", text: needsWebSignIn(ev.error) ? "Sign in to run models: the buttons are on the left." : `\n(error: ${ev.error})`, at: Date.now() }
          if (live()) { said = true; setLines((l) => [...l, line]) }
          else forLater(line, "stopped with an error")
        }
        else if (t === "done") ended = true
      }
    }
    // A turn that ends with nothing said is never silent: say so (2026-11-06: a failed model call showed an empty turn).
    if (live()) {
      if (!said) setLines((l) => [...l, { who: "assistant", text: ended ? "kell got no answer from the model for this. Try again, or check Settings > Models."
        : "The connection to the engine ended before an answer came. Try again.", at: Date.now() }])
      // Let React flush the last line, then let an active goal judge the turn (app/api/goal). A failed turn pauses it.
      setTimeout(() => { if (onScreen.current === turn.sid) goalAfterTurn.current(turn.sid, failed) }, 80)
      // The session's snapshot record lands shortly after the last stream event, and how long is not fixed: check now, then
      // poll a few times, so the panel fills itself instead of waiting for a Refresh click.
      if (turn.sid) loadChanges(turn.sid)
      for (const ms of [600, 1200, 2500, 5000]) setTimeout(() => { if (onScreen.current === turn.sid) loadChanges(turn.sid) }, ms)
      setDelegateStatus("")
    } else if (turn.sid) {
      notifyDone(turn.sid, chatTitle(turn.sid), failed ? "stopped with an error" : undefined)
    }
    loadSessions()
    setTimeout(loadSessions, 4000)                           // the engine names a new session a moment after its first turn
  }

  // A turn always leaves the running set, even when its stream breaks; only this chat's entry goes, so other chats keep
  // answering. A finished background chat gets the notice in the app and the device notification (2026-11-17).
  const runTurn = async (sent: string, outgoing: Attachment[], plan: Delegation | null, key: string, startSid: string | null) => {
    const turn = { key, sid: startSid ?? "", text: "" }
    try { await runTurnInner(sent, outgoing, plan, turn) }
    catch (err) {
      if ((err as any)?.name === "AbortError") return     // Stop: the note says so; this is not a broken connection
      const why = String((err as any)?.message || err).slice(0, 200)
      if (turn.sid && onScreen.current === turn.sid) appendAssistant(`The connection to the engine broke: ${why}`)
      else if (turn.sid) notifyDone(turn.sid, chatTitle(turn.sid), "the connection broke")
    }
    finally {
      turnCtl.current = turnCtl.current.filter((x) => x.key !== turn.key)
      endTurn(turn.key)
      if (turn.key !== turn.sid) endTurn(turn.sid)
      if (turn.sid) delete turnText.current[turn.sid]
      // A permission card for a turn that has ended is stale. A credit/plan or error notice is still worth showing
      // when the person returns to that chat.
      if (turn.sid) pendingLines.current = pendingLines.current.filter((x) => x.sid !== turn.sid || x.line.who !== "permission")
      if (onScreen.current === turn.sid) setDelegateStatus("")
    }
  }

  // A plan with the composer's model, when the person named one: the router's rung becomes this choice, and the reason
  // says whose choice it was so the panel never claims kell picked it.
  const withChatModel = (plan: Delegation): Delegation => chatModel
    ? { ...plan, model: toModelRef(chatModel), rung: null, why: `you chose ${chatModel} in the composer` }
    : plan

  // The person picked one of the Ways: build its plan (model rung, brief) and run the waiting turn.
  const applyOption = (option: WayOption) => {
    const pending = pendingTurn
    const key = pending?.sid ?? chatKeyRef.current
    if (!pending || runningRef.current[key]) return           // a double click must not start the turn twice
    const plan = withChatModel(planFromOption(pending.text, settings?.models || {}, pending.goal, option))
    planRef.current = plan
    setDelegation(plan)
    setDelegateOptions([])
    setDelegateGoals([])
    setInspectWay(null)
    setPendingTurn(null)
    setDelegateAnswer(""); setDelegateErr(""); setDelegateNote(""); setDelegateLog([])
    beginTurn(key, pending.sid)
    setDelegateStatus(`Working with ${plan.model?.id || "the engine's default"}…${suggestLine(plan)}`)
    void runTurn(pending.text, pending.files, plan, key, pending.sid)
  }

  // The procedure editor's steps: one shared list per Way, so the right panel and the card's sheet edit the same record.
  const procKey = (o: WayOption) => o.id || o.name
  const stepsFor = (o: WayOption) => procDrafts[procKey(o)] ?? o.steps
  const setProcSteps = (o: WayOption, steps: DelegationStep[]) =>
    setProcDrafts((d) => ({ ...d, [procKey(o)]: steps }))
  const editProcStep = (o: WayOption, i: number, patch: Partial<DelegationStep>) =>
    setProcSteps(o, stepsFor(o).map((s, n) => (n === i ? { ...s, ...patch } : s)))
  const addProcStep = (o: WayOption) =>
    setProcSteps(o, [...stepsFor(o), { order: stepsFor(o).length + 1, title: "New step", kind: "prompt" }])
  const removeProcStep = (o: WayOption, i: number) =>
    setProcSteps(o, stepsFor(o).filter((_, n) => n !== i).map((s, n) => ({ ...s, order: n + 1 })))
  const replaceProcSteps = (o: WayOption, steps: DelegationStep[]) =>
    setProcSteps(o, steps.map((s, n) => ({ ...s, order: n + 1 })))
  const suggestProcModels = (o: WayOption) => {
    const models = settings?.models
    if (!models) return
    // One change for all steps: editProcStep per step would each read the same stale draft and keep only the last.
    setProcSteps(o, stepsFor(o).map((s) => {
      if (s.model) return s                                   // a person's pick is never overwritten
      const rec = recommendStepModel(s, models)
      return rec ? { ...s, model: rec.model } : s
    }))
  }
  const useProcWay = (o: WayOption) => {
    if (!pendingTurn) return
    applyOption({ ...o, steps: stepsFor(o) })
    setInspectWay(null)
  }
  // Ask kell to rewrite the steps (title and note) from a sentence: the editor's input, like the canvas's. The bar's
  // pictures and its model go with it (app/api/procedure/edit); files still being read travel as undefined, and the
  // route says so rather than describing a picture it never received.
  const askProcedure = async (o: WayOption, text: string, files?: BarAttachment[], model?: string) => {
    const steps = stepsFor(o)
    const r = await fetch("/api/procedure/edit", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: o.name, goal: o.goal?.name || "",
        steps: steps.map(({ title, note, kind, tool, model }) => ({ title, note, kind, tool, model })),
        instruction: text,
        files: (files || []).filter((f) => f.dataUrl).map(({ name, mime, dataUrl, size }) => ({ name, mime, dataUrl, size })),
        ...(model ? { model: toModelRef(model) } : {}),
      }),
    })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(j.error || `the edit answered ${r.status}`)
    if (Array.isArray(j.steps)) replaceProcSteps(o, j.steps as DelegationStep[])
    return typeof j.note === "string" ? j.note : undefined
  }

  // "Run without a Way": the turn still runs, on the model the ladder would pick for the task, with no procedure
  // contract. Nothing is hidden: the panel says no Way was applied.
  const runWithoutWay = () => {
    const pending = pendingTurn
    const key = pending?.sid ?? chatKeyRef.current
    if (!pending || runningRef.current[key]) return
    const plan = withChatModel(planFromOption(pending.text, settings?.models || {}, pending.goal, null))
    planRef.current = plan
    setDelegation(plan)
    setDelegateOptions([])
    setDelegateGoals([])
    setInspectWay(null)
    setPendingTurn(null)
    setDelegateAnswer(""); setDelegateErr(""); setDelegateNote(""); setDelegateLog([])
    beginTurn(key, pending.sid)
    setDelegateStatus("Running without a Way — the engine's model does the work")
    void runTurn(pending.text, pending.files, plan, key, pending.sid)
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
    // Stop the turn in the chat on screen. Other chats keep answering: their Stop is in their own chat (2026-11-17).
    // The turn ends HERE at once: its request is aborted (the server then interrupts the engine, even for a new chat
    // whose session id is not known yet), a procedure search it may still be in is dropped, and the composer is free for
    // the next message. It used to wait for the engine's stream to end, which an interrupt never did (2026-11-18).
    const key = chatKeyRef.current
    const t = runningRef.current[key]
    if (!t || stopping) return
    const sid = t.sid || ""
    setStopping(true)
    turnGen.current++                                       // a search still running for this turn is dropped
    const hit = (x: { key: string; sid: string }) => x.key === key || (!!sid && x.sid === sid)
    for (const x of turnCtl.current.filter(hit)) x.ctl.abort()
    turnCtl.current = turnCtl.current.filter((x) => !hit(x))
    endTurn(key)
    if (sid && sid !== key) endTurn(sid)
    setDelegateStatus("")
    try {
      if (sid) {
        const r = await fetch("/api/stop", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: sid }) })
        const j = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(j.error || `stop failed (${r.status})`)
      }
      setStopNote("Stopped. Steps that already ran stay; nothing is undone.")
    } catch (e: any) {
      setStopNote(`Stopped here; the engine said: ${String(e?.message || e)}`)
    } finally { setStopping(false) }
  }

  const answerPermission = async (l: Line, decision: "once" | "always" | "reject") => {
    setLines((all) => all.map((x) => (x.requestID === l.requestID ? { ...x, answer: decision } : x)))
    const r = await fetch("/api/permission", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ sessionID: l.sessionID, requestID: l.requestID, decision }) }).catch(() => null)
    if (!r?.ok) setLines((all) => all.map((x) => (x.requestID === l.requestID ? { ...x, answer: undefined } : x)))
  }

  const appendNote = (text: string) => setLines((l) => [...l, { who: "assistant", text, at: Date.now() }])

  // Delete a chat: stop its turn if it is running, remove it from the app's store and the engine, and leave the pane on
  // a new chat when it was the one open (2026-11-17).
  const deleteChat = async (id: string) => {
    setSessMenu(null); setSessDelete(null)
    const title = sessionsRef.current.find((s) => s.id === id)?.title || "the chat"
    if (runningRef.current[id]) {
      try { await fetch("/api/stop", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sessionID: id }) }) } catch { /* it goes anyway */ }
    }
    try {
      const r = await fetch("/api/sessions/delete", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || `the delete answered ${r.status}`)
      endTurn(id)
      if (id === sessionID) newSession()
      setSessions((list) => list.filter((x) => x.id !== id))   // the sidebar is the app's store; no reload can re-add it
    } catch (err) {
      appendNote(`Could not delete ${title}: ${String((err as any)?.message || err)}`)
    }
  }

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

  // The next waiting message goes out once the turn has ended.
  useEffect(() => {
    if (busy || !queued.length) return
    const [next, ...rest] = queued
    setQueued(rest)
    void send(next.text, next.files)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queued, busy])

  // Ctrl/Cmd-K opens the command palette from anywhere in the app (reqs §1: "same shortcut in every context").
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); setPalette((p) => !p); setPaletteQ(""); setPaletteIdx(0) }
      // Ctrl/Cmd+J shows or hides the terminal (the Inspector's first tab), as in other editors.
      else if ((e.ctrlKey || e.metaKey) && (e.key === "j" || e.key === "J")) { e.preventDefault(); togglePanel("terminal") }
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

  // Search every chat (not just this folder) from the palette: FTS5 in the app's own store, newest first.
  useEffect(() => {
    if (!palette || paletteQ.trim().length < 2) { setChatHits([]); return }
    let live = true
    const timer = setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(paletteQ)}`)
        .then((r) => r.json())
        .then((j) => { if (live) setChatHits(Array.isArray(j.results) ? j.results.slice(0, 8) : []) })
        .catch(() => { if (live) setChatHits([]) })
    }, 180)
    return () => { live = false; clearTimeout(timer) }
  }, [palette, paletteQ])

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

  const project = directory ? projName(directory) : "No project"
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
    { id: "new-session", title: "New session", hint: "a fresh chat in this project", run: newSession },
    { id: "rename-session", title: "Rename this chat", hint: "give the open chat your own name", run: () => { if (sessionID) startRename({ id: sessionID }) } },
    { id: "open-folder", title: "Open a folder…", hint: "choose the project kell works in", run: () => void openFolder() },
    { id: "refresh", title: "Refresh the changes", run: () => loadChanges(sessionID) },
    { id: "undo", title: "Undo kell's last changes", hint: "put the files back as they were", run: () => void undo() },
    { id: "fork", title: "Fork this session", hint: "a new session with this whole chat; the original stays as it is", run: () => void fork() },
    { id: "left", title: "Show or hide the project sidebar", run: () => toggleSidebar("left") },
    { id: "right", title: "Show or hide the changes panel", run: () => toggleSidebar("right") },
    { id: "terminal", title: "Show or hide the terminal", hint: "Ctrl+J — a shell in the project folder", run: () => togglePanel("terminal") },
    { id: "browser", title: "Show or hide the browser", hint: "open your dev server or any page beside the chat", run: () => togglePanel("browser") },
    { id: "stop", title: "Stop kell", hint: "end the running turn; nothing is undone", run: () => void stop() },
    { id: "code", title: "Go to the coding agent", run: () => setPane("code") },
    { id: "rooms", title: "Go to the team rooms", run: () => setPane("rooms") },
    { id: "submit-way", title: "Submit a Way", hint: "write a goal and its steps; each step is reviewed", run: () => openSubmitWay() },
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
  // A hit opens its chat (switching folder when it lives elsewhere) and scrolls that message into view.
  const openHit = async (h: ChatHit) => {
    setPalette(false); setPaletteQ(""); setPaletteIdx(0)
    const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
    if (h.sessionID === sessionID) {
      pendingHit.current = { sid: h.sessionID, at: h.at }
      let n = 0
      const go = () => { if (scrollHit(h) || ++n > 12) { pendingHit.current = null; return } window.setTimeout(go, 120) }
      window.setTimeout(go, 80)
      return
    }
    pendingHit.current = { sid: h.sessionID, at: h.at }
    if (h.directory && (!directory || norm(h.directory) !== norm(directory))) await openProject(h.directory, h.sessionID)
    else await openSession(h.sessionID)
  }
  const paletteItems = [
    ...orderedCommands.map((c) => ({ id: c.id, title: c.title, hint: c.hint as string | undefined, kind: "command" as const, run: () => runCommand(c.run, c.id) })),
    ...(paletteQ2.length >= 2 ? chatHits.map((h) => ({ id: `chat:${h.id}`, title: h.snippet || "(empty message)", hint: `${h.title || "Untitled chat"} · ${when(h.at)}`, kind: "chat" as const, run: () => void openHit(h) })) : []),
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

  // The graph in the right sidebar: the chosen Way, or a preview of the best Way while the choice is open in the chat.
  // The cards themselves live in the middle chat now (2026-11-16); the sidebar is always the procedure, never a list.
  const previewPlan = !delegation && pendingTurn && pendingTurn.sid === sessionID
    ? planFromOption(pendingTurn.text, settings?.models || {}, pendingTurn.goal, delegateOptions[0] ?? null)
    : null
  const shownPlan = delegation ?? previewPlan
  // The record the right panel shows: the card open in the chat, else the best fit while choosing, else the chosen Way.
  const panelWay: WayOption | null = inspectWay ?? delegateOptions[0] ?? (delegationWay(delegation))

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
      <nav className="dk-switch" aria-label="Code, rooms and help">
        {desktop() && (
          <button type="button" className="dk-icon dk-icon-sm dk-menubtn" aria-label="File, Edit and View menu" title="File, Edit, View and Help" onClick={() => desktop()?.showMenu?.()}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
        )}
        <div role="tablist">
          <button type="button" role="tab" aria-selected={pane === "code"} onClick={() => setPane("code")}>Code</button>
          <button type="button" role="tab" aria-selected={pane === "rooms"} onClick={() => setPane("rooms")}>Rooms</button>
        </div>
        {/* The top bar keeps only the app menu, the Code/Rooms tabs and the window controls. Importing other agents'
            chats lives in Sessions; who is signed in lives in the account button at the foot of the sidebar. */}
        <span className="dk-help" ref={helpRef} style={{ marginLeft: "auto" }}>
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

          {/* Two views (owner, 2026-11-14): the projects you have opened, and once you open one, that project alone: its
              sessions, then its files, each folding away, with a way back to the projects. */}
          {sideView === "projects" || !directory ? (
            <>
              <div className="dk-sec-head">
                <h2>Projects</h2>
              </div>
              {/* One way in: the folder picker (the app), or a typed path where there is no picker (a browser during development). */}
              {!desktop()?.pickFolder && (
                <div style={{ margin: "0 6px 8px" }}>
                  <input value={directory} onChange={(e) => setDirectory(e.target.value)} disabled={!!sessionID}
                    placeholder="a folder path to work in" className="dk-field mono" aria-label="Project folder" />
                </div>
              )}
              <div className="dk-row dk-pair">
                <button type="button" className="btn-ink" disabled={opening}
                  onClick={() => void openFolder()}>{opening ? "Opening…" : "Open folder"}</button>
                <button type="button" className="btn-line" disabled={opening} title="Chat without a project"
                  onClick={() => void (async () => {
                    const h = await loadHome(); if (!h) return
                    setSideView("project")
                    if (directory && isChats(directory) && engine?.up) return
                    await openProject(h)
                  })()}>Chats</button>
              </div>
              {recents.length === 0 ? <p className="dk-note">Projects you open show here.</p> : (
                <ul className="dk-recents" aria-label="Projects">
                  {recents.map((d) => {
                    const name = projName(d)
                    const here = !!directory && d.toLowerCase() === directory.toLowerCase()
                    return (
                      <li key={d} data-here={here || undefined}>
                        <button type="button" className="dk-recent" title={d} disabled={opening}
                          onClick={() => {
                            setSideView("project")
                            if (here && engine?.up) return                 // already open: just show it
                            void openFolder(d)
                          }}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
                          <span className="nm">{name}</span>
                        </button>
                        <button type="button" className="dk-recent-more" aria-haspopup="menu" aria-expanded={projMenu?.path === d}
                          aria-label={`Actions for ${name}`} title="More actions" onClick={(e) => openProjectMenu(e, d)}>
                          {/* Lucide's ellipsis-vertical (ISC), drawn the way the account / console icons at the foot are. */}
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <circle cx="12" cy="5" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="12" cy="19" r="1" />
                          </svg>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </>
          ) : (
            <>
              <div className="dk-proj-head">
                <button type="button" className="dk-icon dk-icon-sm" aria-label="Back to the projects" title="Back to the projects" onClick={() => setSideView("projects")}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6" /></svg>
                </button>
                <b title={directory}>{projName(directory)}</b>
                <button type="button" className="dk-icon dk-icon-sm" aria-haspopup="menu" aria-expanded={projMenu?.path === directory}
                  aria-label="Project actions" title="More actions" onClick={(e) => openProjectMenu(e, directory)}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="5" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="12" cy="19" r="1" />
                  </svg>
                </button>
              </div>
              <p className="dk-note mono" title={directory} style={{ overflowWrap: "anywhere" }}>{directory}</p>
              {opening && <p className="dk-note" role="status">Starting the coding engine… the first start after an update can take a minute or two.</p>}
              {engine && !engine.up && !opening && (engine.error || sessionID) && !needsWebSignIn(engine.error) && (
                <div className="dk-err" role="status">
                  <p style={{ margin: "0 0 6px" }}>The coding engine didn&rsquo;t start{engine.error ? `: ${engine.error}` : "."}</p>
                  <button type="button" className="btn-line" onClick={() => void openProject(directory)}>Retry</button>
                </div>
              )}
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

              {/* Sessions first, then files; each folds away (the choice is kept for this run). */}
              <div className="dk-sec-head">
                <button type="button" className="dk-fold" aria-expanded={showSessions} onClick={() => setShowSessions((o) => !o)}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg><h2>Sessions</h2>{sessions.length > 0 && <span className="dk-count">{sessions.length}</span>}
                </button>
                <button type="button" className="dk-icon dk-icon-sm" aria-label="New session" title="New session"
                  onClick={newSession}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
                </button>
              </div>
              <div className="dk-sec-body" data-open={showSessions}>
                <div className="dk-sec-body-in">
                  <div className="dk-sess dk-sess-list">
                    {sessions.length === 0 ? <p className="dk-note">No sessions yet. Write a message to start one.</p> : (() => {
                      const live = sessions.filter((x) => !x.archived)
                      const pinned = live.filter((x) => x.pinned)
                      const rest = live.filter((x) => !x.pinned)
                      const folders = [...new Set(rest.map((x) => x.folder).filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b))
                      const loose = rest.filter((x) => !x.folder)
                      const archived = sessions.filter((x) => x.archived)
                      const toggle = (f: string) => setClosedFolders((s) => { const n = new Set(s); if (n.has(f)) n.delete(f); else n.add(f); return n })
                      return (
                        <>
                          {pinned.length > 0 && <><p className="dk-sess-group">Pinned</p>{pinned.map(sessionRow)}</>}
                          {folders.map((f) => (
                            <div key={`f:${f}`}>
                              <button type="button" className="dk-sess-group dk-sess-folderhead" aria-expanded={!closedFolders.has(f)} onClick={() => toggle(f)}>
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d={closedFolders.has(f) ? "m9 6 6 6-6 6" : "m6 9 6 6 6-6"} /></svg>
                                {f} <span className="dk-count">{rest.filter((x) => x.folder === f).length}</span>
                              </button>
                              {!closedFolders.has(f) && rest.filter((x) => x.folder === f).map(sessionRow)}
                            </div>
                          ))}
                          {(pinned.length > 0 || folders.length > 0) && loose.length > 0 && <p className="dk-sess-group">Chats</p>}
                          {loose.map(sessionRow)}
                          {archived.length > 0 && (
                            <>
                              <button type="button" className="dk-sess-group dk-sess-folderhead" aria-expanded={showArchived} onClick={() => setShowArchived((o) => !o)}>
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d={showArchived ? "m6 9 6 6 6-6" : "m9 6 6 6-6 6"} /></svg>
                                Archived <span className="dk-count">{archived.length}</span>
                              </button>
                              {showArchived && archived.map(sessionRow)}
                            </>
                          )}
                        </>
                      )
                    })()}
                    <button type="button" className="dk-sess-import" onClick={() => openImport()}>Import other agent chats</button>
                  </div>
                </div>
              </div>

              {/* The project's files: a folder opens on click and asks the app for its entries then; a file opens in the
                  right panel's file view. */}
              <div className="dk-sec-head">
                <button type="button" className="dk-fold" aria-expanded={showFiles} onClick={() => setShowFiles((o) => !o)}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg><h2>Files</h2>
                </button>
                <button type="button" className="dk-icon dk-icon-sm" aria-label="Refresh the file list" title="Refresh the file list"
                  onClick={() => setTreeEpoch((n) => n + 1)}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>
                </button>
              </div>
              <div className="dk-sec-body" data-open={showFiles}>
                <div className="dk-sec-body-in">
                  <div className="dk-tree" key={`${directory}:${treeEpoch}`}>
                    <TreeLevel directory={directory} rel="" depth={0} onOpen={openFile} selected={filePath || undefined} />
                  </div>
                </div>
              </div>
            </>
          )}
          </div>

          <div className="dk-foot">
            {projNote && <p className="dk-note" role="status">{projNote}</p>}
            {signinNote && <p className="dk-note" role="status">{signinNote}</p>}
            {consoleNote && <p className="dk-note">{consoleNote}</p>}
            {accountOpen && settings && accountAt && createPortal(
              <div className="dk-page-float dk-account" role="dialog" aria-label="Account" style={{ left: accountAt.left, bottom: accountAt.bottom }}>
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
              </div>, document.body)}
            <div className="dk-iconrow">
              {settings && !settings.signedIn ? (
                <button type="button" className="btn-ink dk-signin" onClick={() => void signIn()}>Sign in</button>
              ) : (
                <button type="button" ref={accountBtnRef} className="dk-icon" aria-label="Account" title={settings?.account ? `Signed in as ${settings.account}` : "Account"}
                  aria-expanded={accountOpen} onClick={() => setAccountOpen((o) => !o)}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
                </button>
              )}
              <button type="button" className="dk-icon dk-badged" aria-label={`Waiting on you${inboxItems.length ? `: ${inboxItems.length}` : ""}`} title="Waiting on you"
                onClick={() => { setShowInbox(true); void loadInbox() }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /></svg>
                {inboxItems.length > 0 && <span className="dk-badge" aria-hidden="true">{inboxItems.length}</span>}
              </button>
              <button type="button" className="dk-icon" aria-label="Scheduled tasks" title="Scheduled tasks" onClick={() => setShowSched(true)}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
              </button>
              <button type="button" className="dk-icon" aria-label="Connectors" title="Connectors: Notion, Slack, Jira and more" onClick={() => setShowConn(true)}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z" /></svg>
              </button>
              <button type="button" className="dk-icon" aria-label="Route a task to a tool" title="Route a task to a tool" onClick={() => window.location.assign("/route")}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
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
            <button type="button" className="dk-icon dk-icon-sm dk-head-tool" aria-pressed={panel.open && panel.tab === "terminal"}
              aria-label="Terminal" title="Terminal (Ctrl+J)" onClick={() => togglePanel("terminal")}>
              <TerminalIcon size={16} />
            </button>
            <button type="button" className="dk-icon dk-icon-sm dk-head-tool" aria-pressed={panel.open && panel.tab === "browser"}
              aria-label="Browser" title="Browser" onClick={() => togglePanel("browser")}>
              <GlobeIcon size={16} />
            </button>
            <ThemeToggle className="dg-theme" />
            {!rightOpen && (
              <button type="button" className="dk-icon dk-icon-sm dk-head-toggle dk-show-right" aria-label="Show the changes panel" title="Show the changes panel"
                onClick={() => toggleSidebar("right")}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></svg>
              </button>
            )}
          </header>

          <div className="dg-scroll"
            onPointerMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect()
              setRailOn(e.clientX > r.right - 34)
            }}
            onPointerLeave={() => setRailOn(false)}
            onDragEnter={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); setDragFiles(true) } }}
            onDragOver={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy" } }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragFiles(false) }}
            onDrop={(e) => { e.preventDefault(); setDragFiles(false); void addBrowserFiles(Array.from(e.dataTransfer.files)) }}>
            {dragFiles && <div className="dg-drop" aria-hidden="true"><span>Drop files to attach</span></div>}
            {docOpen && <DocPreview doc={docOpen} onClose={() => setDocOpen(null)}
              onAskChange={(t) => void send(`Change the document "${docOpen.name}" (${docOpen.path}): ${t}

Remake it with kell-docs at the same path; keep everything else as it is.`, [])} />}
            {railTicks.length > 0 && (
              // ChatGPT's prompt rail: one tick per prompt at its place in the conversation; click to go there.
              <div className="dk-rail" data-on={railOn || undefined} aria-hidden={!railOn}>
                {railTicks.map((t) => (
                  <button key={t.i} type="button" style={{ top: `${t.pct * 100}%` }} title={t.text}
                    aria-label={`Go to prompt: ${t.text}`} tabIndex={railOn ? 0 : -1}
                    onClick={() => jumpToPrompt(t.i)} />
                ))}
              </div>
            )}
            <div className="dg-viewport" ref={logRef} aria-label="Messages">
              <div className={`dg-content${agentView !== "main" ? " dk-agent-content" : ""}`} ref={logContentRef}>
                {agentView.startsWith("sub:") ? (
                  // A subagent's own view: the task it was given and the answer it returned. Its steps ran in a worker of
                  // their own, so the engine does not stream them here; this is what it was asked and what came back.
                  (() => {
                    const s = subagents.find((x) => `sub:${x.id}` === agentView)
                    return (
                      <div className="dk-agent-view">
                        <div className="dk-agent-view-head">
                          <span className="dk-agent-dot" data-state={s?.state || "running"} aria-hidden="true" />
                          <b title={s?.title}>{s?.title || "Subagent"}</b>
                          <span className="dk-agent-view-sub">subagent{s ? ` · ${s.state}` : ""}</span>
                          <button type="button" className="btn-line dg-mini" onClick={() => setAgentView("main")}>Back to main</button>
                        </div>
                        {!s ? (
                          <p className="dk-note">This subagent is not in this turn&apos;s list any more.</p>
                        ) : (
                          <>
                            {s.brief && (
                              <div className="dk-agent-turn">
                                <div className="dk-agent-turn-label">What it was asked</div>
                                <pre className="dk-code" tabIndex={0}>{s.brief}</pre>
                              </div>
                            )}
                            {s.result ? (
                              <div className="dk-agent-turn">
                                <div className="dk-agent-turn-label">What it answered</div>
                                <Markdown text={s.result} />
                              </div>
                            ) : (
                              <p className="dk-delegate-status" role="status" aria-live="polite">
                                <span className="dg-pulse" aria-hidden="true" />
                                {s.state === "running" ? "Working…" : s.state === "failed" ? "It failed: nothing came back." : "No answer came back."}
                              </p>
                            )}
                            <p className="dk-note">Its steps ran in a worker of their own; the chat shows its tool calls as they happened.</p>
                          </>
                        )}
                      </div>
                    )
                  })()
                ) : agentView === "delegated" ? (
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
                {sessLoading && lines.length === 0 && !busy && (
                  // A switch reads the app's store, then the engine: say so, so a slow read is not a blank chat.
                  <div className="dk-empty" role="status" aria-live="polite">
                    <p className="dk-empty-line">Opening the chat…</p>
                  </div>
                )}
                {!sessLoading && lines.length === 0 && !busy && (
                  // The empty state is not a blank box (reqs §8): one line states the boundary, and three prompts that
                  // are known to work here fill the composer rather than a highlight reel.
                  <div className="dk-empty">
                    {directory && isChats(directory) ? (
                      <>
                        <p className="dk-empty-line">Ask anything: questions, advice, writing, analysis. Attach files from anywhere, and ask for a Word, Excel, PowerPoint or PDF file when you want one.</p>
                        <div className="dk-empty-prompts">
                          {["Explain a topic to me simply", "Draft an email I describe", "Make a spreadsheet from data I paste"].map((p) => (
                            <button key={p} type="button" className="btn-line dg-mini dk-empty-prompt" onClick={() => { setText(p); textRef.current?.focus() }}>{p}</button>
                          ))}
                        </div>
                      </>
                    ) : directory ? (
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
                        <p className="dk-empty-line">Ask kell anything, or open a folder to work on a project — changes to a project come back as a diff you can undo.</p>
                        <div className="dk-row" style={{ margin: 0 }}>
                          <button type="button" className="btn-ink" onClick={() => void (async () => { const h = await loadHome(); if (h) { setSideView("project"); await openProject(h) } })()}>Just chat</button>
                          <button type="button" className="btn-line" onClick={() => void openFolder()}>Open a folder</button>
                        </div>
                      </>
                    )}
                  </div>
                )}
                {lines.map((l, i) => {
                  if (l.who === "catalog") {
                    const canSignIn = /sign in|401|expired/i.test(l.server || "")
                    return (
                      <div className="dg-run" data-agent="true" key={i}>
                        <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                        <div className="dg-col">
                          <div className="dg-meta"><b>Kell</b><span className="dg-badge">procedure search</span><time>{time(l.at)}</time></div>
                          <div className="dk-signin" data-state="ready">
                            <p>{canSignIn ? "kell's sign-in expired, so the procedure search could not run. Sign in, then search again."
                              : `The procedure search could not run: ${l.server}`}</p>
                            <div className="dk-row">
                              {canSignIn && <button type="button" className="btn-ink" onClick={() => void signIn()}>Sign in to kell</button>}
                              <button type="button" className="btn-line" onClick={() => void send(l.text)}>Search again</button>
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  }
                  if (l.who === "thought") {
                    if (!showThoughts) return null
                    return (
                      <details className="dk-thought" key={i} open={i === lines.length - 1}>
                        <summary>Thoughts</summary>
                        <div className="dk-thought-text">{l.text}</div>
                      </details>
                    )
                  }
                  if (l.who === "plan") {
                    return (
                      <div className="dg-run" data-agent="true" key={i}>
                        <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                        <div className="dg-col">
                          <div className="dg-meta"><b>Kell</b><span className="dg-badge">plan</span><time>{time(l.at)}</time></div>
                          <div className="dk-signin">
                            {l.low ? (
                              <p>Your balance is ${l.low.left.toFixed(2)}, and this Way has {l.low.steps} steps: it may stop partway. If it
                                does, the run is kept; add credit and say "continue" to pick it up where it stopped.</p>
                            ) : l.run ? (
                              <p>Out of credit: your Way run is paused at step {l.run.step} of {l.run.of}
                                {l.run.title ? <> ({l.run.title})</> : null}. Nothing is lost. Add a plan or credit, then say
                                "continue" and kell picks it up from step {l.run.step}.</p>
                            ) : (
                              <p>You need a plan to run models. Starter, Pro and Plus include usage, and you can add credit any time.</p>
                            )}
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
                    <div className={`dg-run${mine ? " dk-you" : ""}`} data-agent={!mine} key={i}
                      data-at={l.at} data-prompt={mine ? i : undefined}>
                      {mine
                        ? <span className="dg-avatar" data-kind="human" aria-hidden="true">You</span>
                        : <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>}
                      <div className="dg-col">
                        <div className="dg-meta"><b>{mine ? "You" : "Kell"}</b>{!mine && <span className="dg-badge">agent</span>}<time>{time(l.at)}</time></div>
                        <div className="dg-msg">
                          <div className="dg-body">{mine && editing?.i === i ? (
                            <div className="dk-edit">
                              <textarea className="dk-field" value={editing.value} rows={Math.min(10, Math.max(2, editing.value.split("\n").length))} autoFocus
                                aria-label="Edit your message" onChange={(e) => setEditing({ i, value: e.target.value })}
                                onKeyDown={(e) => {
                                  if (e.key === "Escape") { e.preventDefault(); setEditing(null) }
                                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void branchSend(i, editing.value) }
                                }} />
                              <div className="dk-row" style={{ margin: "6px 0 0" }}>
                                <button type="button" className="btn-ink dg-mini" disabled={!editing.value.trim() || busy} onClick={() => void branchSend(i, editing.value)}>Send</button>
                                <button type="button" className="btn-line dg-mini" onClick={() => setEditing(null)}>Cancel</button>
                                <span className="dk-note" style={{ margin: 0 }}>Sends from here in a new branch; this chat stays as it is.</span>
                              </div>
                            </div>
                          ) : mine ? <p>{l.text}</p> : live ? <SmoothText text={l.text} markdown /> : <Markdown text={l.text} />}</div>
                          {mine && l.files?.length ? attachmentChips(l.files) : null}
                          {l.text && !live ? (
                            <MessageActions copyText={l.text} retryText={mine ? l.text : lastUserBefore(i)}
                              onCopy={copyText}
                              onRetry={(t) => {
                                if (mine) { void send(t); return }
                                // Regenerate: the same prompt again in a branch from just before it.
                                let u = i - 1
                                while (u >= 0 && lines[u].who !== "user") u--
                                if (u >= 0 && !busy) void branchSend(u, t); else void send(t)
                              }}
                              retryLabel={mine ? "Send this again" : "Regenerate"}
                              onEdit={mine && !busy && editing?.i !== i ? () => setEditing({ i, value: l.text }) : undefined}
                              onFork={sessionID && !busy ? () => void fork({ who: l.who, text: l.text, at: l.at }) : undefined} />
                          ) : null}
                          {!mine && !live ? <DocCards docs={docsAfter(i)} onOpen={setDocOpen} /> : null}
                        </div>
                      </div>
                    </div>
                  )
                })}
                {pendingTurn && pendingTurn.sid === sessionID && (
                  // The procedure search result, in the conversation itself: the Ways, or the Goals that matched with
                  // no written Way. kell is stopped until the person picks one or says to work without one (2026-11-16).
                  <div className="dg-run dk-pick-run" data-agent="true">
                    <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                    <div className="dg-col">
                      <div className="dg-meta">
                        <b>Kell</b><span className="dg-badge">procedure</span>
                        <time>{time(lines.at(-1)?.at || Date.now())}</time>
                      </div>
                    </div>
                    <div className="dk-pick-wide">
                      <ProcedurePicker goal={pendingTurn.goal} options={delegateOptions} goals={delegateGoals} onInspect={setInspectWay}
                        stepsFor={stepsFor} onEdit={editProcStep} onAdd={addProcStep} onRemove={removeProcStep}
                        onAsk={askProcedure} catalog={settings?.catalog || []}
                        models={settings?.models} onSuggest={suggestProcModels}
                        initialAsk={pendingTurn.text} initialFiles={pendingTurn.files}
                        onPick={applyOption} onSkip={runWithoutWay} />
                    </div>
                  </div>
                )}
                {remoteLive && remoteLive.sessionID === sessionID && !busy && (
                  <div className="dk-remote-live" role="status" aria-live="polite">
                    <div className="dg-run dk-you" data-agent={false}>
                      <span className="dg-avatar" data-kind="human" aria-hidden="true">You</span>
                      <div className="dg-col">
                        <div className="dg-meta"><b>{remoteLive.from === "you, from the rooms" ? "You" : remoteLive.from}</b><span className="dg-badge">from the rooms</span></div>
                        <div className="dg-msg"><div className="dg-body"><p>{remoteLive.question}</p></div></div>
                      </div>
                    </div>
                    <div className="dg-run" data-agent="true">
                      <span className="dg-avatar dg-avatar-kell" data-kind="agent" aria-hidden="true"><KellMark size={26} /></span>
                      <div className="dg-col">
                        <div className="dg-meta"><b>Kell</b><span className="dg-badge">agent</span></div>
                        <div className="dg-msg"><div className="dg-body">{remoteLive.text ? <SmoothText text={remoteLive.text} markdown /> : <p className="dk-note">Working…</p>}</div></div>
                      </div>
                    </div>
                  </div>
                )}
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
            {agentView === "main" && !atEnd && (
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
          <form className="dg-composer" onSubmit={(e) => { e.preventDefault(); send() }}
            onDragEnter={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); setOverComposer(true) } }}
            onDragOver={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy" } }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverComposer(false) }}
            onDrop={(e) => { e.preventDefault(); setOverComposer(false); void addBrowserFiles(Array.from(e.dataTransfer.files || [])) }}
            data-over={overComposer || undefined}>
            <span className="dg-composer-grip" role="separator" aria-orientation="horizontal" aria-label="Resize the message box" onPointerDown={startComposerResize} />
            {/* The chips, pictures as thumbnails: the same strip the procedure's ask bar draws (components/AttachStrip). */}
            <AttachStrip files={attachments} onRemove={removeAttachment} onOpen={openAttachment} leaving={leavingAtts} />
            <input ref={fileInputRef} type="file" multiple hidden accept="image/*,.pdf,.txt,.md,.json,.csv,.log,text/*,application/pdf"
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
            {editsOpen && (files.length > 0 || otherFiles.length > 0) && (
              <div className="dk-edits" role="region" aria-label="Edits in this chat">
                <div className="dk-edits-head">
                  <b>Edits</b>
                  <span>{files.length} file{files.length === 1 ? "" : "s"} in the last turn{otherMore ? ` · ${otherMore} more earlier` : ""}</span>
                  <button type="button" className="dk-icon dk-icon-sm" aria-label="Close edits" onClick={() => setEditsOpen(false)}>×</button>
                </div>
                <FileList files={files} />
                {filesNote && <p className="dk-note">{filesNote}</p>}
              </div>
            )}
            <div className="dk-composer-top">
              <div className="dk-mode" role="group" aria-label="Procedure mode" data-mode={procMode}>
                <span className="dk-mode-thumb" aria-hidden="true" />
                {PROC_MODES.map((m) => (
                  <button key={m.id} type="button" className="dk-mode-btn" data-on={procMode === m.id || undefined}
                    data-tip={m.hint} aria-pressed={procMode === m.id} aria-label={`${m.label}: ${m.hint}`}
                    title={m.hint} onClick={() => chooseProcMode(m.id)}>
                    {m.label}
                  </button>
                ))}
              </div>
              {/* The same bar's other control: which model answers, from this machine's catalog (app/api/settings). Chat
                  means kell's router picks the rung, which is what the app did before there was a bar to choose on. */}
              <ModelPicker value={chatModel} catalog={settings?.catalog || []} onChange={chooseChatModel}
                label="Model for the main chat" />
              {/* Two things the turn produces that used to be invisible: the model's own reasoning, and the files it
                  changed. Both are one click from the bar that sent the message (2026-11-19). */}
              <button type="button" className="dk-bar-btn" aria-pressed={showThoughts} data-on={showThoughts || undefined}
                title={showThoughts ? "Thoughts are shown: click to hide them" : "Thoughts are hidden: click to show them"}
                onClick={toggleThoughts}>Thoughts</button>
              <button type="button" className="dk-bar-btn" aria-pressed={editsOpen} data-on={editsOpen || undefined}
                title={files.length ? `${files.length} file${files.length === 1 ? "" : "s"} changed in this chat` : "No edits in this chat yet"}
                disabled={!files.length && !otherFiles.length} onClick={() => setEditsOpen((o) => !o)}>
                Edits{files.length ? ` ${files.length}` : ""}
              </button>
              {busy && (
                // The stop sits with the mode choice, above the message box: the turn it stops is the one the mode
                // applies to, and it stays reachable without taking the send button's place.
                <button type="button" className="dk-stop" onClick={() => void stop()} disabled={stopping}
                  aria-label="Stop kell"
                  title="Stop this chat's turn. Steps that already ran stay; nothing is undone.">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
                </button>
              )}
            </div>
            <textarea ref={textRef} rows={1} data-big={big || undefined} value={text}
              role="combobox" aria-autocomplete="list" aria-expanded={slashCmds.length > 0}
              aria-controls={slashCmds.length ? "dk-slash" : undefined}
              aria-activedescendant={slashCmds.length ? `dk-slash-${slashAt}` : undefined}
              onChange={(e) => { setText(e.target.value); histPos.current = null; setCmdIdx(0) }}
              placeholder={directory ? "Tell kell what to do… (/ for commands)" : "Open a project first … or /model to choose a model"}
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
            <button type="submit" className="dk-send" disabled={(!text.trim() && !attachments.length) || !directory || (engine != null && !engine.up)}
              aria-label={busy ? "Queue this message" : "Send"} title={busy ? "kell is working: this waits and goes out when it finishes" : "Send"}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          </form>
          {panel.used && (
            <BottomPanel directory={directory || ""} open={panel.open} tab={panel.tab}
              onTab={(tab) => setPanel((p) => ({ ...p, tab }))} onClose={() => setPanel((p) => ({ ...p, open: false }))}
              active={pane === "code" && !palette} />
          )}
        </section>

        <aside className="dk-panel" aria-label="File, changes, steps and tools">
          {/* While the divider is dragged, a transparent shield keeps a file view or an embedded frame from swallowing the pointer. */}
          {resizing === "right" && <div className="dk-shield" aria-hidden="true" />}
          {/* The agents in this session: the session itself and the model the router delegated to. Clicking one swaps the
              middle column between the chat and that agent's own output. The panel's own actions (hide, refresh, undo) sit
              here at the top, not buried in the third fold. */}
          <div className="dk-panel-head">
            <h2>Agents</h2>
            <span className="dk-iconrow">
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Hide the changes panel" title="Hide the changes panel"
                onClick={() => toggleSidebar("right")}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m10 6 6 6-6 6" /></svg>
              </button>
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Refresh the changes" title="Refresh" onClick={() => loadChanges(sessionID)} disabled={!readEngine}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>
              </button>
              <button type="button" className="dk-icon dk-icon-sm" aria-label="Undo kell's last changes" title="Undo kell's last changes" onClick={() => undo()} disabled={!directory || busy || (!!sessionID && pending === 0)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></svg>
              </button>
            </span>
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
          <div className="dk-agents" role="tablist" aria-label="Agents">
            <button type="button" role="tab" className="dk-agent" aria-selected={agentView === "main"} onClick={() => setAgentView("main")}>
              <span className="dk-agent-dot" aria-hidden="true" />
              {/* The chat's own model, from this turn's steps or the chat's history: the panel should say what the main
                  agent is, not a bare "this chat" (2026-11-14). */}
              <span><b>Main agent</b><small>{ledger.length ? ledger[ledger.length - 1].model || "this chat" : sessionModel || "this chat"}</small></span>
            </button>
            {delegation?.model ? (
              <button type="button" role="tab" className="dk-agent" aria-selected={agentView === "delegated"} onClick={() => setAgentView("delegated")}>
                <span className="dk-agent-dot" data-rung={delegation.rung || "cheap"} aria-hidden="true" />
                <span><b>{delegation.model.id}</b><small>delegated{delegation.rung ? ` · ${delegation.rung}` : ""}</small></span>
              </button>
            ) : subagents.length === 0 && !busy ? (
              // Nothing has run in this chat yet: say so. With a subagent or a turn in flight there is something, and a
              // "No delegated agent" row beside it only contradicted the panel.
              <div className="dk-agent dk-agent-empty">
                <span className="dk-agent-dot" aria-hidden="true" />
                <span><b>No delegated agent</b></span>
                <InfoTip align="end" text="Send a task: the router picks the model each step needs, and the delegated agent shows here." />
              </div>
            ) : null}
            {subagents.map((s) => (
              // A subagent the engine ran for this turn: its one-line task and live state, and its own view on click.
              <button key={s.id} type="button" role="tab" className="dk-agent" aria-selected={agentView === `sub:${s.id}`}
                title={s.title} onClick={() => setAgentView(`sub:${s.id}`)}>
                <span className="dk-agent-dot" data-state={s.state} aria-hidden="true" />
                <span><b>{s.title}</b><small>subagent · {s.state}</small></span>
              </button>
            ))}
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
          {/* The procedure record, editable in the panel: the card open in the chat, else the best fit, else the chosen Way. */}
          {/* The right sidebar is Submit a Way: written here, drafted here, or submitted for review. It replaced the
              read-only Procedure record (2026-11-18, owner's call): one place to write a Way, and the same panel the
              rooms use, so a Way looks the same wherever it is written. The record of the Way a turn is running is still
              in the chat, as its card. */}
          <section className="dk-proc" data-glass={glassGraph ? "on" : "off"} aria-label="Submit a Way">
            <div className="dk-proc-head">
              <span className="dk-proc-title">
                <h2>Submit a Way</h2>
                <InfoTip align="start" text="Write a new Way step by step. Drafts stay on this computer and run here; submitting sends it to kell's reviewer, which checks every step." />
              </span>
              <span className="dk-proc-actions">
                <button type="button" className="dk-icon dk-icon-sm dk-proc-toggle" aria-pressed={glassGraph}
                  aria-label={glassGraph ? "Turn the glass effect off" : "Turn the glass effect on"}
                  title={glassGraph ? "Glass effect on" : "Glass effect off"} onClick={toggleGlassGraph}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 3.5l1.7 4.3 4.3 1.7-4.3 1.7L12 15.5l-1.7-4.3L6 9.5l4.3-1.7z" />
                    <path d="M18.6 14.4l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
                  </svg>
                </button>
              </span>
            </div>
            <div className="dk-proc-graph" key="submit">
              <span className="dk-proc-aurora" aria-hidden="true" />
              <SubmitWayPanel api={submissionsApi} compact />
            </div>
          </section>
        </aside>
        <div className="dk-scrim-right" onClick={() => setRightOpen(false)} aria-hidden="true" />
      </div>

      {palette && (
        <div className="dk-palette-backdrop" onMouseDown={() => setPalette(false)}>
          <div className="dk-palette" role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}>
            <input className="dk-palette-input" autoFocus value={paletteQ} placeholder="Search chats, commands and files…"
              aria-label="Search chats, commands and files" aria-controls="dk-palette-list"
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
                    <span className="k">{it.kind === "file" ? "file" : it.kind === "chat" ? "chat" : ""}</span>
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
      {modesIntro && <ModesIntro onClose={closeModesIntro} />}
      {showInbox && <InboxPanel items={inboxItems} onClose={() => setShowInbox(false)} onOpenChat={(sid) => void openAnyChat(sid)} onAnswered={() => void loadInbox()} />}
      {showConn && <ConnectorsPanel onClose={() => setShowConn(false)} />}
      {showSched && <SchedulesPanel onClose={() => { setShowSched(false); void loadInbox() }} onOpenChat={(sid) => void openAnyChat(sid)} homeDir={homePath || homeRef.current} projectDir={directory} />}
      {sessMenu && (
        // A session row's actions: Rename or Delete. Delete asks once, in place, because it cannot be undone.
        <div className="dk-sess-menu" role="menu" style={{ left: sessMenu.x, top: sessMenu.y }}>
          {sessDelete === sessMenu.id ? (
            <div className="dk-sess-confirm">
              <p>Delete this chat?</p>
              <span>Its messages are removed from this app and the engine. This cannot be undone.</span>
              <div>
                <button type="button" className="btn-line dg-mini" autoFocus onClick={() => setSessDelete(null)}>Cancel</button>
                <button type="button" className="dk-danger dg-mini" onClick={() => void deleteChat(sessMenu.id)}>Delete</button>
              </div>
            </div>
          ) : (
            <>
              <button type="button" role="menuitem" autoFocus onClick={() => { const id = sessMenu.id; setSessMenu(null); startRename({ id }) }}>Rename</button>
              {(() => {
                const cur = sessions.find((x) => x.id === sessMenu.id)
                const folders = [...new Set(sessions.map((x) => x.folder).filter(Boolean) as string[])].sort()
                return (
                  <>
                    <button type="button" role="menuitem" onClick={() => { void fileChat(sessMenu.id, { pinned: !cur?.pinned }); setSessMenu(null) }}>{cur?.pinned ? "Unpin" : "Pin to top"}</button>
                    {folderAsk === sessMenu.id ? (
                      <form className="dk-sess-folder" onSubmit={(e) => { e.preventDefault(); void fileChat(sessMenu.id, { folder: folderName.trim() || null }); setFolderAsk(null); setSessMenu(null) }}>
                        <input autoFocus list="dk-folders" value={folderName} onChange={(e) => setFolderName(e.target.value)} placeholder="Folder name" aria-label="Folder name" />
                        <datalist id="dk-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
                        <button type="submit" className="btn-ink dg-mini">Move</button>
                      </form>
                    ) : (
                      <button type="button" role="menuitem" onClick={() => { setFolderName(cur?.folder || ""); setFolderAsk(sessMenu.id) }}>{cur?.folder ? "Move to another folder…" : "Move to a folder…"}</button>
                    )}
                    {cur?.folder && <button type="button" role="menuitem" onClick={() => { void fileChat(sessMenu.id, { folder: null }); setSessMenu(null) }}>Take out of “{cur.folder}”</button>}
                    <button type="button" role="menuitem" onClick={() => { void fileChat(sessMenu.id, { archived: !cur?.archived }); setSessMenu(null) }}>{cur?.archived ? "Unarchive" : "Archive"}</button>
                  </>
                )
              })()}
              <button type="button" role="menuitem" className="dk-danger" onClick={() => setSessDelete(sessMenu.id)}>Delete</button>
            </>
          )}
        </div>
      )}
      {doneNote && (
        // A chat that finished while another was on screen: a quiet notice here, with the same news sent to the device
        // by notifyDone. Opening it reads the answer (2026-11-17).
        <div className="dk-done-toast" role="status" aria-live="polite">
          <span className="dk-done-dot" aria-hidden="true" />
          <span className="dk-done-text"><b>{doneNote.title}</b> {doneNote.why || "finished answering"}.</span>
          <button type="button" className="dk-done-open" onClick={() => { const id = doneNote.id; setDoneNote(null); void openSession(id) }}>Open</button>
          <button type="button" className="dk-done-x" aria-label="Dismiss" title="Dismiss" onClick={() => setDoneNote(null)}>×</button>
        </div>
      )}
    </section>
  )
}
