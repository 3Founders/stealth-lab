// GET  /api/executions            -> { executions }                 the list, newest first
// GET  /api/executions?id=<id>    -> { execution, run }             one, with the run's steps, outputs and files
// POST /api/executions {action: "start", submissionId, title, input} -> { execution }
// POST /api/executions {action: "answer", id, note}                 -> { execution }   a person's step
// POST /api/executions {action: "cancel" | "remove", id}            -> { execution }
//
// The page's door to lib/executions.ts, which runs on the server (it reads the sign-in and drives the engine).
import { NextResponse } from "next/server"

import { answerStep, cancelExecution, getExecution, listExecutions, removeExecution, resumeExecutions, runView,
  startExecution } from "@/lib/executions"

export const dynamic = "force-dynamic"

let resumed = false

export async function GET(req: Request) {
  if (!resumed) { resumed = true; try { resumeExecutions() } catch { /* the list still answers */ } }
  const id = new URL(req.url).searchParams.get("id")
  if (!id) return NextResponse.json({ executions: listExecutions() })
  const execution = getExecution(id)
  if (!execution) return NextResponse.json({ error: "no such execution" }, { status: 404 })
  try {
    return NextResponse.json({ execution, run: await runView(id) })
  } catch (err: any) {
    // The execution is real even when kell cannot be reached: show it, and say why its run could not be read.
    return NextResponse.json({ execution, run: null, run_error: String(err?.message || err).slice(0, 300) })
  }
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  try {
    switch (String(body?.action || "")) {
      case "start": {
        const submissionId = String(body.submissionId || "")
        if (!submissionId) return NextResponse.json({ error: "give the Way's submission id" }, { status: 400 })
        return NextResponse.json({ execution: startExecution({ submissionId, title: String(body.title || "A Way"),
          input: String(body.input || "") }) })
      }
      case "answer": return NextResponse.json({ execution: await answerStep(String(body.id), String(body.note || "")) })
      case "cancel": return NextResponse.json({ execution: await cancelExecution(String(body.id)) })
      case "remove": removeExecution(String(body.id)); return NextResponse.json({ ok: true })
      default: return NextResponse.json({ error: "unknown action" }, { status: 400 })
    }
  } catch (err: any) {
    const status = Number(err?.status) || 502
    return NextResponse.json({ error: String(err?.message || err).slice(0, 400) }, { status: status >= 400 && status < 600 ? status : 502 })
  }
}
