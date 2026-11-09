// Importing chats from other agents (owner, 2026-11-08: old chats should not just appear; the app should offer to import
// them): Claude Code's history and another opencode store on this machine, brought into this project's engine as sessions.
//
//   GET  /api/import?source=claude|opencode&directory=<abs path>   list what that source has for this folder (nothing is written)
//   POST /api/import { source, directory, ids: [id, ...] }          import the chosen ones; answers what worked and what did not
//
// Claude Code keeps one JSONL per chat under ~/.claude/projects/<slug>/<uuid>.jsonl (each line an event; `cwd` names the
// folder). The opencode v2 store moved to one SQLite file, ~/.local/share/opencode/opencode.db (session, session_message),
// read here with Node's own sqlite, read-only. A chat becomes a session through the engine's import endpoint
// (`client.experimental.session.import`), so the engine owns it like any other: it lists, opens and continues normally.
// Matching is exact on the folder: only chats worked in this project are offered.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import type { DatabaseSync as Db } from "node:sqlite"
import { clientFor, engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

type Chat = { id: string; title: string; updated: number; messages: number; preview: string }
type Msg = { role: "user" | "assistant"; text: string; at: number }

const MAX_TEXT = 20_000        // one imported message's text; long chats are capped so a single import stays small
const norm = (d: string) => path.resolve(d).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
const isToolNoise = (t: string) => /^<(local-command|command-name|command-message|command-args|system-reminder)/.test(t.trim())
const titleOf = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, 80)

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
      let cwd = "", title = "", preview = "", count = 0
      for (const line of text.split("\n")) {
        if (!line.startsWith("{")) continue
        let j: any
        try { j = JSON.parse(line) } catch { continue }
        if (!cwd && typeof j.cwd === "string") cwd = j.cwd
        if (j.type === "custom-title" && typeof j.customTitle === "string") title = j.customTitle
        if (j.type !== "user" && j.type !== "assistant") continue
        count++
        if (j.type === "user" && !preview && !j.isSidechain) {
          const c = j.message?.content
          const t = typeof c === "string" ? c : Array.isArray(c) ? c.filter((x: any) => x?.type === "text").map((x: any) => x.text).join(" ") : ""
          if (t && !isToolNoise(t)) preview = t
        }
      }
      // The recorded folder decides; the folder's own name (Claude Code's own slug for it) is the fallback for old files.
      const mine = cwd ? norm(cwd) === want : norm(folder).includes(want.replace(/[^a-z0-9]+/g, "-"))
      if (!mine || !count) continue
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

// ---- Routes -------------------------------------------------------------------------------------------------------
export async function GET(req: Request) {
  const q = new URL(req.url).searchParams
  const directory = q.get("directory") || engineStatus()?.directory || ""
  if (!directory) return Response.json({ chats: [] })
  try {
    return Response.json({ chats: q.get("source") === "claude" ? ccChats(directory).chats : ocChats(directory) })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), chats: [] }, { status: 500 })
  }
}

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "Open a project first: chats are imported into an open project.", done: [], failed: [] }, { status: 502 })
  const body = await req.json().catch(() => ({}))
  const source = body.source === "claude" ? "claude" : "opencode"
  const ids: string[] = Array.isArray(body.ids) ? body.ids.map(String).slice(0, 200) : []
  const directory = String(body.directory || e.directory)
  const client = await clientFor(e)
  const cc = source === "claude" ? ccChats(directory) : null
  const listing = source === "claude" ? cc!.chats : ocChats(directory)
  const done: string[] = []
  const failed: { id: string; error: string }[] = []
  for (const id of ids) {
    try {
      const msgs = source === "claude" ? (cc!.byId.has(id) ? ccMessages(cc!.byId.get(id)!) : []) : ocMessages(id)
      if (!msgs.length) throw new Error("nothing was said in this chat that could be imported")
      const title = listing.find((c) => c.id === id)?.title || titleOf(msgs[0].text)
      // The engine's import owns the session from here: it gives it its id, keeps the turns, and continuation works as usual.
      const info: any = { projectID: undefined, title, agent: "build" }
      await (client as any).experimental.session.import({
        info: { ...info, id: undefined },
        messages: msgs.map((m, k) => source === "claude"
          ? { type: m.role, id: `msg_import_${id.replace(/\W/g, "")}_${k}`, time: { created: m.at }, text: m.text, ...(m.role === "assistant" ? { agent: "build", model: { providerID: "opencode", id: "imported" }, content: [{ type: "text", text: m.text }], finish: "stop", cost: 0, tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } } } : {}) }
          : { type: m.role, id: `msg_import_${id.replace(/\W/g, "")}_${k}`, time: { created: m.at }, text: m.text, ...(m.role === "assistant" ? { agent: "build", model: { providerID: "opencode", id: "imported" }, content: [{ type: "text", text: m.text }], finish: "stop", cost: 0, tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } } } : {}) }),
        location: { directory },
      })
      done.push(id)
    } catch (err: any) { failed.push({ id, error: String(err?.message || err).slice(0, 300) }) }
  }
  return Response.json({ done, failed })
}
