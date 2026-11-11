// Importing chats from other agents (owner, 2026-11-08: old chats should not just appear; the app should offer to import
// them): Claude Code's, Codex's and another opencode store's history on this machine, brought into this project's engine
// as sessions.
//
//   GET  /api/import?source=claude|codex|opencode&directory=<abs path>   list what that source has for this folder (nothing is written)
//   POST /api/import { source, directory, ids: [id, ...] }          import the chosen ones; answers what worked and what did not
//
// Claude Code keeps one JSONL per chat under ~/.claude/projects/<slug>/<uuid>.jsonl (each line an event; `cwd` names the
// folder). Codex keeps one rollout per chat under ~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl (or .jsonl.zst
// once it compresses them); each line is an event, `session_meta` names the folder, and the chat's name lives in
// ~/.codex/session_index.jsonl. The opencode v2 store moved to one SQLite file, ~/.local/share/opencode/opencode.db
// (session, session_message), read here with Node's own sqlite, read-only. A chat becomes a session through the engine's
// import endpoint (`client.experimental.session.import`), so the engine owns it like any other: it lists, opens and
// continues normally. It is then marked `metadata.kell = true` so the sidebar shows it (app/api/sessions.ts keeps only
// kell sessions). Matching is exact on the folder: only chats worked in this project are offered.
import { randomBytes } from "node:crypto"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import type { DatabaseSync as Db } from "node:sqlite"
import { clientFor, engineStatus } from "@/lib/engine"
import { importSession } from "@/lib/localdb"
import { summarize } from "@/lib/summarize"

export const dynamic = "force-dynamic"

type Chat = { id: string; title: string; updated: number; messages: number; preview: string }
type Msg = { role: "user" | "assistant"; text: string; at: number }

const MAX_TEXT = 20_000        // one imported message's text; long chats are capped so a single import stays small
const norm = (d: string) => path.resolve(d).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
const isToolNoise = (t: string) => /^<(local-command|command-name|command-message|command-args|system-reminder)/.test(t.trim())
const titleOf = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, 80)

// The engine's session import takes a complete Session.Info and refuses one without an id. Mirror the engine's own
// Session.ID shape (`ses_` + a descending 26-character identifier, @opencode/schema/identifier) so imported sessions
// sort alongside the ones the app creates. The counter keeps ids unique when several are imported in the same ms.
const ID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
let lastIDTime = 0
let idCounter = 0
function newSessionID(): string {
  const now = Date.now()
  if (now !== lastIDTime) { lastIDTime = now; idCounter = 0 }
  idCounter++
  const value = ~(BigInt(now) * 0x1000n + BigInt(idCounter))
  const time = Array.from({ length: 6 }, (_, i) => Number((value >> BigInt(40 - 8 * i)) & 0xffn).toString(16).padStart(2, "0")).join("")
  return "ses_" + time + Array.from(randomBytes(14), (b) => ID_CHARS[b % 62]).join("")
}

// ---- Claude Code ------------------------------------------------------------------------------------------------
const ccRoot = () => path.join(os.homedir(), ".claude", "projects")

/** Every chat in Claude Code's history whose folder is `dir`, newest first. */
function ccChats(dir: string) {
  const want = norm(dir)
  const out: Chat[] = []
  const byId = new Map<string, string>()          // chat id (<uuid>) -> file
  let dirs: string[] = []
  try { dirs = fs.readdirSync(ccRoot()) } catch { return { chats: out, byId } }
  for (const d of dirs) {
    const folder = path.join(ccRoot(), d)
    try { if (!fs.statSync(folder).isDirectory()) continue } catch { continue }
    let files: string[] = []
    try { files = fs.readdirSync(folder) } catch { continue }
    for (const f of files) {
      if (!f.endsWith(".jsonl")) continue
      const file = path.join(folder, f)
      let text: string
      let mtime: number
      try { text = fs.readFileSync(file, "utf8"); mtime = fs.statSync(file).mtimeMs } catch { continue }
      // Listing only needs the folder, the name and the first thing said. Parsing every line of every chat made the
      // picker wait ~20 s on a large history; a regex for the first `cwd` and a count, then a line-by-line parse that
      // stops as soon as it has both, keeps the same answer and reads the whole file only when it has to.
      const cwdRaw = text.match(/"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"/)?.[1]
      let cwd = ""
      if (cwdRaw) { try { cwd = JSON.parse(`"${cwdRaw}"`) } catch { cwd = cwdRaw } }
      const count = (text.match(/"type"\s*:\s*"(?:user|assistant)"/g) || []).length
      // The recorded folder decides; the folder's own name (Claude Code's own slug for it) is the fallback for old files.
      const mine = cwd ? norm(cwd) === want : norm(folder).includes(want.replace(/[^a-z0-9]+/g, "-"))
      if (!mine || !count) continue
      let title = "", preview = ""
      for (const line of text.split("\n")) {
        if (!line.startsWith("{")) continue
        if (!title && line.includes("custom-title")) {
          try { const j = JSON.parse(line); if (j.type === "custom-title" && typeof j.customTitle === "string") title = j.customTitle } catch { /* partial */ }
        }
        if (!preview && /"type"\s*:\s*"user"/.test(line)) {
          try {
            const j = JSON.parse(line)
            if (j.type === "user" && !j.isSidechain) {
              const c = j.message?.content
              const t = typeof c === "string" ? c : Array.isArray(c) ? c.filter((x: any) => x?.type === "text").map((x: any) => x.text).join(" ") : ""
              if (t && !isToolNoise(t)) preview = t
            }
          } catch { /* partial */ }
        }
        if (title && preview) break
      }
      const id = path.basename(f, ".jsonl")
      byId.set(id, file)
      out.push({ id, title: title || titleOf(preview) || id.slice(0, 8), updated: mtime, messages: count, preview: preview.slice(0, 140) })
    }
  }
  return { chats: out.sort((a, b) => b.updated - a.updated), byId }
}

/** One Claude Code chat's turns, in order; tool calls and sidechains are left out (only what was said). */
function ccMessages(file: string): Msg[] {
  const msgs: Msg[] = []
  let last: Msg | null = null
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    if (!line.startsWith("{")) continue
    let j: any
    try { j = JSON.parse(line) } catch { continue }
    if (j.isSidechain) continue
    const at = Date.parse(j.timestamp || "") || Date.now()
    if (j.type === "user" && j.message?.content !== undefined) {
      const c = j.message.content
      const body = typeof c === "string" ? c : Array.isArray(c) ? c.filter((x: any) => x?.type === "text").map((x: any) => String(x.text || "")).join("\n") : ""
      if (!body.trim() || isToolNoise(body)) continue
      msgs.push({ role: "user", text: body.slice(0, MAX_TEXT), at })
      last = null
    } else if (j.type === "assistant" && j.message?.content) {
      const body = (Array.isArray(j.message.content) ? j.message.content : []).filter((x: any) => x?.type === "text").map((x: any) => String(x.text || "")).join("")
      if (!body.trim()) continue
      if (last) last.text = (last.text + "\n\n" + body).slice(0, MAX_TEXT)     // one answer over several steps: one message
      else { last = { role: "assistant", text: body.slice(0, MAX_TEXT), at }; msgs.push(last) }
    }
  }
  return msgs
}

// ---- The opencode v2 store ----------------------------------------------------------------------------------------
const ocDbPath = () => path.join(os.homedir(), ".local", "share", "opencode", "opencode.db")

function ocOpen(): Db | null {
  if (!fs.existsSync(ocDbPath())) return null
  const { DatabaseSync } = require("node:sqlite") as typeof import("node:sqlite")
  return new DatabaseSync(ocDbPath(), { readOnly: true })
}

function ocChats(dir: string): Chat[] {
  let db: Db | null = null
  try {
    db = ocOpen()
    if (!db) return []
    const rows = db.prepare(
      `select s.id, s.title, s.time_updated t, s.directory d,
              (select count(*) from session_message m where m.session_id = s.id and m.type in ('user','assistant')) n,
              (select json_extract(m.data, '$.text') from session_message m where m.session_id = s.id and m.type = 'user' order by m.seq limit 1) first
         from session s where s.parent_id is null order by s.time_updated desc limit 1000`).all() as any[]
    const want = norm(dir)
    return rows.filter((r) => r.d && norm(String(r.d)) === want).map((r) => ({
      id: String(r.id), title: String(r.title || "") || String(r.first || "").slice(0, 80),
      updated: Number(r.t) || 0, messages: Number(r.n) || 0, preview: String(r.first || "").slice(0, 140),
    }))
  } catch { return [] } finally { try { db?.close() } catch { /* already closed */ } }
}

function ocMessages(id: string): Msg[] {
  const db = ocOpen()
  if (!db) return []
  try {
    const rows = db.prepare(`select type, data from session_message where session_id=? and type in ('user','assistant') order by seq`).all(id) as any[]
    const msgs: Msg[] = []
    let last: Msg | null = null
    for (const r of rows) {
      let d: any
      try { d = JSON.parse(String(r.data)) } catch { continue }
      const at = Number(d?.time?.created) || Date.now()
      if (r.type === "user") {
        const body = String(d.text || "")
        if (!body.trim()) continue
        msgs.push({ role: "user", text: body.slice(0, MAX_TEXT), at })
        last = null
      } else {
        const body = (Array.isArray(d.content) ? d.content : []).filter((c: any) => c?.type === "text").map((c: any) => String(c.text || "")).join("")
        if (!body.trim()) continue
        if (last) last.text = (last.text + "\n\n" + body).slice(0, MAX_TEXT)
        else { last = { role: "assistant", text: body.slice(0, MAX_TEXT), at }; msgs.push(last) }
      }
    }
    return msgs
  } finally { try { db.close() } catch { /* already closed */ } }
}

// ---- Codex --------------------------------------------------------------------------------------------------------
const codexRoot = () => path.join(os.homedir(), ".codex", "sessions")
const codexIndexPath = () => path.join(os.homedir(), ".codex", "session_index.jsonl")
const UUID = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i

/** Codex compresses rollouts once they are old (`rollout-*.jsonl.zst`). Node 22.15+ has zstd in `zlib`. */
function unzstd(buf: Buffer): string {
  const z = require("node:zlib") as typeof import("node:zlib") & { zstdDecompressSync?: (b: Buffer) => Buffer }
  if (typeof z.zstdDecompressSync !== "function") throw new Error("this Node cannot read Codex's compressed chats")
  return z.zstdDecompressSync(buf).toString("utf8")
}

/** Every rollout file under ~/.codex/sessions, newest first. */
function codexFiles(): string[] {
  const out: { file: string; mtime: number }[] = []
  const walk = (dir: string) => {
    let entries: fs.Dirent[] = []
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = path.join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/^rollout-.*\.jsonl(?:\.zst)?$/.test(e.name)) continue
      try { out.push({ file: p, mtime: fs.statSync(p).mtimeMs }) } catch { /* vanished */ }
    }
  }
  walk(codexRoot())
  return out.sort((a, b) => b.mtime - a.mtime).map((x) => x.file)
}

/** Codex's own names for its chats (`session_index.jsonl`), keyed by session id. */
function codexIndex(): Map<string, string> {
  const map = new Map<string, string>()
  let text = ""
  try { text = fs.readFileSync(codexIndexPath(), "utf8") } catch { return map }
  for (const line of text.split("\n")) {
    if (!line.startsWith("{")) continue
    let j: any
    try { j = JSON.parse(line) } catch { continue }
    if (typeof j.id === "string" && typeof j.thread_name === "string") map.set(j.id, j.thread_name)
  }
  return map
}

/** A rollout's JSON lines. A truncated last line (the app is still writing) is skipped, not fatal. */
function codexLines(file: string): any[] {
  let text: string
  try { text = file.endsWith(".zst") ? unzstd(fs.readFileSync(file)) : fs.readFileSync(file, "utf8") } catch { return [] }
  const out: any[] = []
  for (const line of text.split("\n")) {
    if (!line.startsWith("{")) continue
    try { out.push(JSON.parse(line)) } catch { /* partial line */ }
  }
  return out
}

/** Codex writes its environment into the conversation as a user turn; it is not something the person said. */
function cleanCodexUser(text: string): string {
  return text
    .replace(/<environment_context>[\s\S]*?<\/environment_context>/g, "")
    .replace(/<external_codex_apps_open_page>[\s\S]*?<\/external_codex_apps_open_page>/g, "")
    .replace(/<user_instructions>[\s\S]*?<\/user_instructions>/g, "")
    .trim()
}

function codexItemText(item: any): string {
  const parts = Array.isArray(item?.content) ? item.content : []
  return parts.filter((c: any) => c?.type === "text" || c?.type === "Text").map((c: any) => String(c.text || "")).join("\n")
}

/**
 * One Codex chat's turns, in order. Older rollouts record `event_msg` user_message/agent_message; newer ones record
 * `item_completed` items. A rollout has one or the other, so the two never double up. `response_item` messages are the
 * fallback for files that carry neither. Tool calls, reasoning and commentary are left out; only what was said.
 */
function codexMessages(lines: any[]): Msg[] {
  const msgs: Msg[] = []
  let last: Msg | null = null
  const push = (role: "user" | "assistant", text: string, at: number) => {
    const body = (role === "user" ? cleanCodexUser(text) : text.trim()).slice(0, MAX_TEXT)
    if (!body) return
    if (role === "assistant" && last?.role === "assistant") { last.text = (last.text + "\n\n" + body).slice(0, MAX_TEXT); return }
    last = { role, text: body, at }
    msgs.push(last)
  }
  let sawEvent = false
  for (const j of lines) {
    if (j.type !== "event_msg") continue
    const p = j.payload || {}
    const at = Number(p.completed_at_ms) || Number(p.started_at_ms) || Date.parse(j.timestamp || "") || Date.now()
    if (p.type === "user_message" && typeof p.message === "string") { sawEvent = true; push("user", p.message, at) }
    else if (p.type === "agent_message" && typeof p.message === "string") { sawEvent = true; push("assistant", p.message, at) }
    else if (p.type === "item_completed") {
      const it = p.item || {}
      if (it.type === "UserMessage") { sawEvent = true; push("user", codexItemText(it), at) }
      else if (it.type === "AgentMessage") { sawEvent = true; push("assistant", codexItemText(it), at) }
    }
  }
  if (sawEvent) return msgs
  for (const j of lines) {
    if (j.type !== "response_item" || j.payload?.type !== "message") continue
    const role = j.payload.role
    if (role !== "user" && role !== "assistant") continue
    const body = (Array.isArray(j.payload.content) ? j.payload.content : [])
      .filter((c: any) => c?.type === "input_text" || c?.type === "output_text" || c?.type === "text")
      .map((c: any) => String(c.text || "")).join("\n")
    push(role, body, Date.parse(j.timestamp || "") || Date.now())
  }
  return msgs
}

/** Every Codex chat whose recorded folder is `dir`, newest first. */
function codexChats(dir: string) {
  const want = norm(dir)
  const out: Chat[] = []
  const byId = new Map<string, string>()
  const names = codexIndex()
  for (const file of codexFiles()) {
    const lines = codexLines(file)
    const meta = lines.find((j) => j.type === "session_meta")?.payload || {}
    // Codex's own sub-agents and guardian reviews share the folder; they are not chats the person had.
    if (typeof meta.thread_source === "string" && meta.thread_source !== "user") continue
    const cwd = typeof meta.cwd === "string" ? meta.cwd : ""
    if (!cwd || norm(cwd) !== want) continue
    const id = typeof meta.id === "string" ? meta.id
      : typeof meta.session_id === "string" ? meta.session_id
      : path.basename(file).match(UUID)?.[1]
    if (!id) continue
    const msgs = codexMessages(lines)
    if (!msgs.length) continue
    const first = msgs.find((m) => m.role === "user")?.text || ""
    const title = names.get(id) || titleOf(first) || id.slice(0, 8)
    let mtime = Date.now()
    try { mtime = fs.statSync(file).mtimeMs } catch { /* keep now */ }
    byId.set(id, file)
    out.push({ id, title, updated: mtime, messages: msgs.length, preview: first.slice(0, 140) })
  }
  return { chats: out.sort((a, b) => b.updated - a.updated), byId }
}

// ---- Routes -------------------------------------------------------------------------------------------------------
type Source = "claude" | "codex" | "opencode"
const sourceOf = (v: unknown): Source => v === "claude" ? "claude" : v === "codex" ? "codex" : "opencode"

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams
  const directory = q.get("directory") || engineStatus()?.directory || ""
  if (!directory) return Response.json({ chats: [] })
  const source = sourceOf(q.get("source"))
  try {
    const chats = source === "claude" ? ccChats(directory).chats : source === "codex" ? codexChats(directory).chats : ocChats(directory)
    return Response.json({ chats })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), chats: [] }, { status: 500 })
  }
}

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "Open a project first: chats are imported into an open project.", done: [], failed: [] }, { status: 502 })
  const body = await req.json().catch(() => ({}))
  const source = sourceOf(body.source)
  const ids: string[] = Array.isArray(body.ids) ? body.ids.map(String).slice(0, 200) : []
  const directory = String(body.directory || e.directory)
  const client = await clientFor(e)
  const cc = source === "claude" ? ccChats(directory) : null
  const cx = source === "codex" ? codexChats(directory) : null
  const listing = source === "claude" ? cc!.chats : source === "codex" ? cx!.chats : ocChats(directory)
  const done: string[] = []
  const imported: string[] = []
  const failed: { id: string; error: string }[] = []
  for (const id of ids) {
    try {
      const msgs = source === "claude" ? (cc!.byId.has(id) ? ccMessages(cc!.byId.get(id)!) : [])
        : source === "codex" ? (cx!.byId.has(id) ? codexMessages(codexLines(cx!.byId.get(id)!)) : [])
        : ocMessages(id)
      if (!msgs.length) throw new Error("nothing was said in this chat that could be imported")
      const title = listing.find((c) => c.id === id)?.title || titleOf(msgs[0].text)
      // The engine's import decodes `info` as a full Session.Info, not a title: id, projectID, cost, tokens, time and
      // location are required. `projectID: "global"` is resolved from `location` by the engine. `metadata.kell` is what
      // app/api/sessions uses to show a session in the sidebar; set it at creation and again below in case this engine
      // version drops it on import.
      const info: any = {
        id: newSessionID(),
        projectID: "global",
        agent: "build",
        cost: 0,
        tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
        time: { created: msgs[0]?.at || Date.now(), updated: Date.now() },
        title,
        location: { directory },
        metadata: { kell: true },
      }
      const session = await (client as any).experimental.session.import({
        info,
        messages: msgs.map((m, k) => ({ type: m.role, id: `msg_import_${id.replace(/\W/g, "")}_${k}`, time: { created: m.at }, text: m.text,
          ...(m.role === "assistant" ? { agent: "build", model: { providerID: "opencode", id: "imported" }, content: [{ type: "text", text: m.text }], finish: "stop", cost: 0, tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } } } : {}) })),
        location: { directory },
      })
      // Mark it as a kell session, exactly as app/api/sessions/import does, or the sidebar would keep hiding it. A marker
      // that cannot be written does not undo the import: the turns are already in the engine.
      const sid = typeof session?.id === "string" ? session.id : ""
      if (sid) {
        const metadata = session.metadata && typeof session.metadata === "object" ? { ...session.metadata, kell: true } : { kell: true }
        try { await client.session.update({ sessionID: sid as any, metadata }) } catch { /* imported anyway */ }
        // The app's own copy (docs/localdb.md): the engine can change or be replaced; this history stays.
        try {
          importSession({
            id: sid, engineId: sid, source, title, directory,
            createdAt: msgs[0]?.at, updatedAt: Date.now(),
            messages: msgs.map((m) => ({ role: m.role, text: m.text, at: m.at, ...(m.role === "assistant" ? { summary: summarize(m.text) } : {}) })),
          })
        } catch { /* local history is an addition; the engine import already succeeded */ }
        imported.push(sid)
      }
      done.push(id)
    } catch (err: any) { failed.push({ id, error: String(err?.message || err).slice(0, 300) }) }
  }
  return Response.json({ done, imported, failed })
}
