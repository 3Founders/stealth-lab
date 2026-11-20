// POST { action, ... } -> the kell submission routes, for the browser.
//
// The panel runs in the page, so it cannot import lib/submissions-api: that module goes through lib/mcp.ts, which reads
// the person's sign-in from disk with node:fs/node:os/node:path. Importing it into a client component made `next build`
// fail with "Reading from node:fs is not handled by plugins" -- the bundler was asked to put server code in the browser.
// (Found 2026-11-18, the first time the panel was actually built into the app.)
//
// So the page calls this route, this route calls the real API on the server, and the two halves stay where they belong.
// The actions are exactly the methods of SubmissionsApi, so components/SubmitWayPanel needs no change beyond which
// module it imports.
import { NextResponse } from "next/server"
import { submissionsApi } from "@/lib/submissions-api"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  const action = String(body?.action || "")
  try {
    const api = submissionsApi
    switch (action) {
      case "preview": return NextResponse.json({ result: await api.preview(body.payload) })
      case "refine": {
        const mode = String(body.mode || "")
        if (mode !== "improve" && mode !== "specialize" && mode !== "abstract") {
          return NextResponse.json({ error: "mode is improve, specialize or abstract" }, { status: 400 })
        }
        return NextResponse.json({ result: await api.refine(body.payload, mode, String(body.instruction || "")) })
      }
      case "submit": return NextResponse.json({ result: await api.submit(body.payload) })
      case "list": return NextResponse.json({ result: await api.list() })
      case "get": return NextResponse.json({ result: await api.get(String(body.id)) })
      case "fix": return NextResponse.json({ result: await api.fix(String(body.id), Number(body.order), body.step) })
      case "removeStep": return NextResponse.json({ result: await api.removeStep(String(body.id), Number(body.order)) })
      case "suggestions": return NextResponse.json({ result: await api.suggestions(String(body.id)) })
      case "run": return NextResponse.json({ result: await api.run(String(body.id), String(body.input || "")) })
      case "getRun": return NextResponse.json({ result: await api.getRun(String(body.runId)) })
      case "done": return NextResponse.json({ result: await api.done(String(body.runId), String(body.note || "")) })
      case "runs": return NextResponse.json({ result: await api.runs() })
      case "cancel": return NextResponse.json({ result: await api.cancel(String(body.runId)) })
      default: return NextResponse.json({ error: `unknown action ${action || "(none)"}` }, { status: 400 })
    }
  } catch (err: any) {
    // The API's own message is what the panel shows (it is written for a person), so pass it through.
    const status = Number(err?.status) || 502
    return NextResponse.json({ error: String(err?.message || err).slice(0, 400) }, { status: status >= 400 && status < 600 ? status : 502 })
  }
}
