// Scheduled tasks (lib/schedules.ts).
//   GET                                    -> { schedules, running }
//   POST { action: "save", ...schedule }   -> { schedule }   (new, or an edit with id)
//   POST { action: "run" | "pause" | "resume" | "delete" | "seen", id, at? }
import { listSchedules, markSeen, removeSchedule, runNow, saveSchedule, scheduleRunning, setEnabled, startSchedules } from "@/lib/schedules"

export const dynamic = "force-dynamic"

export async function GET() {
  startSchedules()
  return Response.json({ schedules: listSchedules(), running: scheduleRunning() })
}

export async function POST(req: Request) {
  startSchedules()
  const b = await req.json().catch(() => ({} as any))
  const id = String(b.id || "")
  switch (b.action) {
    case "save":
      if (!String(b.prompt || "").trim()) return Response.json({ error: "write what kell should do" }, { status: 400 })
      return Response.json({ schedule: saveSchedule(b) })
    case "run": return Response.json({ schedule: runNow(id) ?? null })
    case "pause": return Response.json({ schedule: setEnabled(id, false) ?? null })
    case "resume": return Response.json({ schedule: setEnabled(id, true) ?? null })
    case "seen": return Response.json({ schedule: markSeen(id, Number(b.at) || 0) ?? null })
    case "delete": removeSchedule(id); return Response.json({ ok: true })
    default: return Response.json({ error: "action: save, run, pause, resume, seen or delete" }, { status: 400 })
  }
}
