// Scheduled tasks (docs/desktop_requirements_2026-11.md, P1 #6, 2026-11-17): a prompt that runs by itself (every day at 9, on
// weekdays, weekly, every few hours, or once), in the chats folder or a project. Each run is a chat of its own, so its answer can be
// read and continued like any other. Runs are headless in the Next server, so they go on while the window is closed (kell stays in
// the tray). A run that waits for a permission shows as "needs you" in the inbox (lib/inbox.ts); a failed run is tried again twice,
// five minutes apart. Kept in the app's store (meta "schedules"); runs of the last few times are kept with each schedule.
import { randomUUID } from "node:crypto"
import { clientFor, engineStart, engineStatus } from "./engine"
import { appendMessage, ensureSession, getMeta, setMeta } from "./localdb"
import { ensureHome, isHome } from "./home"
import { rulesFor } from "./tools"
import { summarize } from "./summarize"

export type Repeat = "once" | "hourly" | "daily" | "weekdays" | "weekly"
export type Run = { at: number; status: "running" | "needs_you" | "done" | "failed" | "missed"; sessionID?: string; error?: string; summary?: string; attempt?: number; ended?: number; seen?: boolean }
export type Schedule = {
  id: string; title: string; prompt: string; directory: string; repeat: Repeat; time: string; day?: number; everyHours?: number
  enabled: boolean; created: number; nextAt: number | null; runs: Run[]
}

const TICK_MS = 30_000
const RETRY_MS = 5 * 60_000
const MAX_ATTEMPTS = 3
const TURN_LIMIT_MS = 30 * 60_000
const MISSED_AFTER_MS = 12 * 60 * 60_000

type G = typeof globalThis & { __kellSchedules?: { started: boolean; running: string | null } }
const g = globalThis as G
const state = g.__kellSchedules || (g.__kellSchedules = { started: false, running: null })

export function listSchedules(): Schedule[] {
  try { const v = JSON.parse(getMeta("schedules") || "[]"); return Array.isArray(v) ? v : [] } catch { return [] }
}
function save(list: Schedule[]) { setMeta("schedules", JSON.stringify(list)) }
function patch(id: string, f: (s: Schedule) => void) {
  const list = listSchedules()
  const s = list.find((x) => x.id === id)
  if (s) { f(s); save(list) }
  return s
}

/** The next time `s` is due after `from`, or null for a "once" that has run. */
export function nextRun(s: Pick<Schedule, "repeat" | "time" | "day" | "everyHours">, from: number, ranOnce = false): number | null {
  const [hh, mm] = (s.time || "09:00").split(":").map((x) => Number(x) || 0)
  const d = new Date(from)
  if (s.repeat === "hourly") {
    const every = Math.max(1, Math.min(24, Number(s.everyHours) || 1))
    return from + every * 3600_000
  }
  const at = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hh, mm, 0, 0)
  if (s.repeat === "once") {
    if (ranOnce) return null
    return at.getTime() > from ? at.getTime() : at.getTime() + 86400_000
  }
  for (let i = 0; i < 8; i++) {
    const t = new Date(at.getTime() + i * 86400_000)
    if (t.getTime() <= from) continue
    const wd = t.getDay()
    if (s.repeat === "weekdays" && (wd === 0 || wd === 6)) continue
    if (s.repeat === "weekly" && wd !== (Number.isInteger(s.day) ? s.day : 1)) continue
    return t.getTime()
  }
  return null
}

export function saveSchedule(input: Partial<Schedule> & { prompt: string }): Schedule {
  const list = listSchedules()
  const now = Date.now()
  const existing = input.id ? list.find((x) => x.id === input.id) : undefined
  const s: Schedule = {
    id: existing?.id || randomUUID(),
    title: String(input.title || existing?.title || input.prompt).replace(/\s+/g, " ").trim().slice(0, 80),
    prompt: String(input.prompt).trim().slice(0, 8000),
    directory: String(input.directory || existing?.directory || ensureHome()),
    repeat: (["once", "hourly", "daily", "weekdays", "weekly"] as Repeat[]).includes(input.repeat as Repeat) ? input.repeat as Repeat : existing?.repeat || "daily",
    time: /^\d{1,2}:\d{2}$/.test(String(input.time || "")) ? String(input.time) : existing?.time || "09:00",
    day: Number.isInteger(input.day) ? Number(input.day) : existing?.day,
    everyHours: Number(input.everyHours) || existing?.everyHours,
    enabled: input.enabled ?? existing?.enabled ?? true,
    created: existing?.created || now,
    nextAt: null,
    runs: existing?.runs || [],
  }
  s.nextAt = s.enabled ? nextRun(s, now) : null
  save(existing ? list.map((x) => (x.id === s.id ? s : x)) : [...list, s])
  return s
}

export function setEnabled(id: string, on: boolean) {
  return patch(id, (s) => { s.enabled = on; s.nextAt = on ? nextRun(s, Date.now()) : null })
}
/** The person has seen a failed or missed run (the inbox's "Dismiss"). */
export function markSeen(id: string, at: number) { return patch(id, (s) => { const r = s.runs.find((x) => x.at === at); if (r) r.seen = true }) }
export function removeSchedule(id: string) { save(listSchedules().filter((x) => x.id !== id)) }
export function runNow(id: string) {
  const s = patch(id, (x) => { x.nextAt = Date.now() - 1 })
  if (s) void tick()
  return s
}

function record(id: string, run: Run) {
  patch(id, (s) => {
    const i = s.runs.findIndex((r) => r.at === run.at)
    if (i >= 0) s.runs[i] = run; else s.runs.unshift(run)
    s.runs = s.runs.slice(0, 10)
  })
}

/** One run: a new chat in the schedule's folder, the prompt, the answer kept in the app's store. */
async function runOne(s: Schedule, attempt: number) {
  const run: Run = { at: Date.now(), status: "running", attempt }
  record(s.id, run)
  try {
    let e = engineStatus()
    if (!e) e = await engineStart(s.directory)
    const client: any = await clientFor(e)
    let created: any
    try { created = await client.session.create({ location: { directory: s.directory }, metadata: { kell: true, schedule: s.id } }) }
    catch { created = await client.session.create({ location: { directory: e.directory }, metadata: { kell: true, schedule: s.id } }) }
    const sessionID = String(created?.id || "")
    if (!sessionID) throw new Error("the engine did not start a chat")
    const title = `⏰ ${s.title} · ${new Date().toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
    try { await client.session.update({ sessionID, title }) } catch { /* the local title is what the sidebar shows */ }
    ensureSession({ id: sessionID, engineId: sessionID, source: "kell", title, directory: s.directory })
    appendMessage(sessionID, { role: "user", text: s.prompt })
    run.sessionID = sessionID
    record(s.id, run)
    let answer = "", failure = ""
    const pump = (async () => {
      for await (const ev of client.event.subscribe()) {
        const k = (ev as any).type as string
        const d = (ev as any).data ?? {}
        if (d.sessionID && d.sessionID !== sessionID) continue
        if (k === "session.text.delta") answer += String(d.delta ?? d.text ?? "")
        else if (k === "permission.asked") { run.status = "needs_you"; record(s.id, run) }
        else if (k === "permission.replied") { run.status = "running"; record(s.id, run) }
        else if (k === "session.execution.failed") { failure = String(d?.error?.data?.message ?? d?.error?.message ?? d?.message ?? "the run failed").slice(0, 300); break }
        else if (k === "session.execution.succeeded" || k === "session.idle") break
      }
    })()
    try { await client.experimental.session.instructions.entry.put({ sessionID, key: "kell-tools", value: rulesFor(isHome(s.directory)) }) } catch { /* runs anyway */ }
    await client.session.prompt({ sessionID, text: s.prompt })
    await Promise.race([pump, new Promise<void>((r) => setTimeout(() => { failure = failure || "the run took longer than 30 minutes"; r() }, TURN_LIMIT_MS))])
    if (answer.trim()) appendMessage(sessionID, { role: "assistant", text: answer, summary: summarize(answer) })
    if (failure && !answer.trim()) throw new Error(/buy credits|prepaid balance|wallet_empty|payment required/i.test(failure) ? "out of credit: add credit or a plan, then run it again" : failure)
    if (!answer.trim()) throw new Error("the run finished without an answer; open it to see what happened")
    Object.assign(run, { status: "done", summary: summarize(answer).slice(0, 240), ended: Date.now() })
    record(s.id, run)
    return true
  } catch (err: any) {
    Object.assign(run, { status: "failed", error: String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300), ended: Date.now() })
    record(s.id, run)
    return false
  }
}

export async function tick() {
  if (state.running) return
  const now = Date.now()
  const due = listSchedules().find((s) => s.enabled && s.nextAt != null && s.nextAt <= now)
  if (!due) return
  // A schedule that came due long ago (the computer was off) is noted as missed and moved on, not run hours late.
  if (now - (due.nextAt as number) > MISSED_AFTER_MS) {
    record(due.id, { at: now, status: "missed", error: "kell was not running at the time" })
    patch(due.id, (s) => { s.nextAt = nextRun(s, now, s.repeat === "once") })
    return
  }
  state.running = due.id
  try {
    const attempt = (due.runs[0]?.status === "failed" ? (due.runs[0].attempt || 1) + 1 : 1)
    const ok = await runOne(due, attempt)
    patch(due.id, (s) => {
      s.nextAt = !ok && attempt < MAX_ATTEMPTS ? Date.now() + RETRY_MS : nextRun(s, Date.now(), s.repeat === "once")
      if (s.repeat === "once" && (ok || attempt >= MAX_ATTEMPTS)) s.enabled = false
    })
  } finally { state.running = null }
}

export function startSchedules() {
  if (state.started || process.env.KELL_SCHEDULES === "off") return
  state.started = true
  const t = setInterval(() => void tick().catch(() => undefined), TICK_MS)
  ;(t as { unref?: () => void }).unref?.()
}
export const scheduleRunning = () => state.running
