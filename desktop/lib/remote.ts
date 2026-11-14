// This desktop's sessions, callable from the rooms (owner, 2026-11-14; backend app/services/desktop_remote.py,
// docs/desktop_remote.md). While the person is signed in to the rooms, the app:
//   - reports its sessions every HEARTBEAT_MS (title, the project folder's name, a short summary of the latest answer:
//     never the conversation, never a full path);
//   - long-polls kell for asks, and runs each one:
//       owner    the person's own ask: in the session itself, as if they typed it;
//       answer   a teammate with read-only access: in a copy of the session (session.fork) that may read but not edit,
//                run commands or reach outside the folder, and every permission it would ask for is refused;
//       approve  the owner approved it in the rooms first; then like full;
//       full     a teammate with full access: in a copy of the session, normal permissions;
//     a permission the turn asks for goes back to the owner in the rooms (waiting_permission), and their reply comes here;
//   - sends the answer as it is written; kell posts the finished answer into the room.
// One ask runs at a time. Nothing runs while no one is signed in to the rooms.
import { randomUUID } from "node:crypto"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { clientFor, engineStart, engineStatus } from "./engine"
import { appendMessage, ensureSession, getAssistantMessages, getMeta, getSession, importSession, getMessages, listSessions, setMeta } from "./localdb"
import { gatewayOrigin } from "./pooled"
import { summarize } from "./summarize"
import { TOOL_RULES } from "./tools"
import { webToken } from "./websession"

export const HEARTBEAT_MS = 30_000
const PROGRESS_MS = 1_500
const TURN_LIMIT_MS = 20 * 60_000
const PERMISSION_WAIT_MS = 10 * 60_000

type Ask = { id: string; session_id: string; text: string; level: "owner" | "answer" | "approve" | "full"; asker_name?: string }

// A copy that may only look: everything is denied, then the read-only tools are allowed (the engine takes the last rule
// that matches). Anything that still asks is refused in runAsk.
export const READ_ONLY_RULES = [
  { action: "*", resource: "*", effect: "deny" },
  ...["read", "glob", "grep", "list", "lsp", "todoread", "todowrite"].map((action) => ({ action, resource: "*", effect: "allow" })),
  { action: "external_directory", resource: "*", effect: "deny" },
] as const

const kellHome = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
const origin = () => {
  try { return gatewayOrigin(JSON.parse(fs.readFileSync(path.join(kellHome(), "config.json"), "utf8"))) } catch { return gatewayOrigin(null) }
}

export function deviceId(): string {
  let id = getMeta("device_id")
  if (!id) { id = `desk-${randomUUID()}`; setMeta("device_id", id) }
  return id
}

async function api(token: string, method: string, url: string, body?: unknown, timeoutMs = 15_000): Promise<any> {
  const r = await fetch(`${origin()}${url}`, {
    method, headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(timeoutMs),
  })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(String(j?.error || `${url} answered ${r.status}`))
  return j
}

/** The sessions this desktop reports: the app's own chats, newest first, with the folder's name and the latest answer's gist. */
export function reportedSessions(limit = 60) {
  return listSessions(limit).map((s) => {
    const last = getAssistantMessages(s.id, 1)[0]
    return { id: s.id, title: s.title || "", project: s.directory ? path.basename(s.directory) : "", updated: s.updated_at,
      summary: last ? (last.summary || summarize(last.text) || "").slice(0, 400) : "" }
  })
}

const state = globalThis as typeof globalThis & { __kellRemote?: { started: boolean; busy: boolean; last: string } }

/** Start the heartbeat and the ask loop once per server. */
export function startRemote() {
  if (state.__kellRemote?.started || process.env.KELL_REMOTE === "off") return
  state.__kellRemote = { started: true, busy: false, last: "" }
  const beat = async () => {
    if (!remoteOn()) return
    const token = await webToken()
    if (!token) return
    try { await api(token, "POST", "/v1/desktop/heartbeat", { device_id: deviceId(), name: os.hostname(), sessions: reportedSessions() }) }
    catch (err: any) { remember(`heartbeat: ${err?.message || err}`) }
  }
  void beat()
  const t = setInterval(() => void beat(), HEARTBEAT_MS)
  ;(t as { unref?: () => void }).unref?.()
  void loop()
}

/** The person can turn this off (Settings): then nothing is reported and no ask is taken. On by default. */
export const remoteOn = (): boolean => { try { return getMeta("remote") !== "off" } catch { return true } }
export const setRemoteOn = (on: boolean) => setMeta("remote", on ? "on" : "off")

const remember = (note: string) => { if (state.__kellRemote) state.__kellRemote.last = note }
export const remoteStatus = () => ({ ...(state.__kellRemote || { started: false, busy: false, last: "" }), device: (() => { try { return deviceId() } catch { return "" } })() })

async function loop() {
  for (;;) {
    const token = remoteOn() ? await webToken() : null
    if (!token) { await sleep(15_000); continue }
    try {
      const j = await api(token, "GET", `/v1/desktop/next?device_id=${encodeURIComponent(deviceId())}&name=${encodeURIComponent(os.hostname())}`, undefined, 40_000)
      if (j?.ask) {
        state.__kellRemote!.busy = true
        try { await runAsk(token, j.ask as Ask) } finally { state.__kellRemote!.busy = false }
      }
    } catch (err: any) {
      remember(`ask loop: ${err?.message || err}`)
      await sleep(10_000)
    }
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Run one ask to the end and report it. Never throws: a failure is reported as the ask's failure. */
export async function runAsk(token: string, ask: Ask, deps = { api, now: Date.now }) {
  const report = (data: Record<string, unknown>) => deps.api(token, "POST", `/v1/desktop/asks/${ask.id}/update`, data).catch(() => undefined)
  try {
    const local = getSession(ask.session_id)
    let e = engineStatus()
    if (!e) e = await engineStart(local?.directory || os.homedir())
    const client: any = await clientFor(e)
    let sessionID = ask.session_id
    if (ask.level !== "owner") {
      // a teammate works in a copy, so the owner's own session and its context are never changed by someone else's ask
      const forked: any = await client.session.fork({ sessionID: ask.session_id })
      sessionID = String(forked?.id || "")
      if (!sessionID) throw new Error("the session could not be copied")
      const title = `From ${ask.asker_name || "a teammate"}: ${ask.text.replace(/\s+/g, " ").slice(0, 60)}`
      await client.session.update({ sessionID, title, metadata: { kell: true, remote: ask.id },
        ...(ask.level === "answer" ? { permissions: READ_ONLY_RULES } : {}) })
      importSession({ id: sessionID, engineId: sessionID, source: "kell", title, directory: local?.directory ?? e.directory,
        messages: getMessages(ask.session_id, 100_000).map((m) => ({ role: m.role as any, text: m.text, at: m.created_at })) })
    }
    await report({ status: "running", run_session: sessionID })
    ensureSession({ id: sessionID, engineId: sessionID, source: "kell", directory: local?.directory ?? e.directory })
    const who = ask.level === "owner" ? "" : ` (asked by ${ask.asker_name || "a teammate"} from a kell room)`
    appendMessage(sessionID, { role: "user", text: ask.text + who })

    let answer = ""
    let sent = 0
    let lastSent = 0
    let failure = ""
    const pump = (async () => {
      for await (const ev of client.event.subscribe()) {
        const k = (ev as any).type as string
        const d = (ev as any).data ?? {}
        if (d.sessionID && d.sessionID !== sessionID) continue
        if (k === "session.text.delta") {
          answer += String(d.delta ?? d.text ?? "")
          if (answer.length !== sent && deps.now() - lastSent > PROGRESS_MS) { sent = answer.length; lastSent = deps.now(); void report({ answer }) }
        } else if (k === "permission.asked") {
          const reply = ask.level === "answer" ? "reject" : await askOwner(token, ask, d, deps.api)
          await client.permission.reply({ sessionID, requestID: String(d.id), decision: reply })
          if (ask.level !== "answer") await report({ status: "running" })
        } else if (k === "session.execution.failed") {
          failure = String(d?.error?.data?.message ?? d?.error?.message ?? d?.message ?? "the turn failed").slice(0, 300)
          break
        } else if (k === "session.execution.succeeded" || k === "session.idle") break
      }
    })()
    try {
      await client.experimental.session.instructions.entry.put({ sessionID, key: "kell-tools", value: TOOL_RULES })
    } catch { /* the prompt still runs */ }
    await client.session.prompt({ sessionID, text: ask.text + who })
    const timer = new Promise<void>((r) => setTimeout(() => { failure = failure || "the turn took longer than 20 minutes"; r() }, TURN_LIMIT_MS))
    await Promise.race([pump, timer])
    if (answer.trim()) appendMessage(sessionID, { role: "assistant", text: answer, summary: summarize(answer) })
    if (failure && !answer.trim()) await report({ status: "failed", error: failure })
    else await report({ status: "done", answer: answer.trim() || "(the session finished without a written answer)" })
  } catch (err: any) {
    await report({ status: "failed", error: String(err?.message || err).slice(0, 300) })
  }
}

/** A permission the turn asks for, put to the owner in the rooms; their reply, or reject when none comes in time. */
async function askOwner(token: string, ask: Ask, d: any, call: typeof api): Promise<"once" | "always" | "reject"> {
  const detail = [d.action, ...(Array.isArray(d.resources) ? d.resources.slice(0, 3) : [])].filter(Boolean).join(" ")
  try {
    await call(token, "POST", `/v1/desktop/asks/${ask.id}/update`, { status: "waiting_permission",
      waiting: { request_id: String(d.id), tool: String(d.action || ""), detail: String(d.message || detail || "a tool call").slice(0, 300) } })
    const until = Date.now() + PERMISSION_WAIT_MS
    while (Date.now() < until) {
      await sleep(2_000)
      const a = await call(token, "GET", `/v1/desktop/asks/${ask.id}`)
      if (a?.status === "cancelled") return "reject"
      if (a?.decision === "once" || a?.decision === "always" || a?.decision === "reject") return a.decision
    }
  } catch { /* no answer: refuse */ }
  return "reject"
}
