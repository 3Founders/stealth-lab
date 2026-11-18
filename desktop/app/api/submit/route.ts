// POST { goal, description?, steps, preview?, submissionId? } -> the submit_goal answer, as data.
//
// "Submit a Way" was a hop into the website's rooms with a query parameter they do not read, so in the app the button
// looked like it did nothing. The panel is the app's own now (components/SubmitWay.tsx), and this is its one call:
// preview=true checks the goal and steps without storing anything (the mode it will be filed under is worked out from
// the steps themselves); anything else submits, and an AI reviewer approves or rejects each step. submissionId alone
// asks for that submission's state, step by step, so the panel can show the verdict without being reloaded.
//
// The panel is editable before the first submit, so a sentence the backend refuses comes back with its own reason
// (intake.py's), which is what the form shows. Nothing is retried or rewritten here.
import { NextResponse } from "next/server"
import { callMcpTool } from "@/lib/mcp"

export const dynamic = "force-dynamic"

/** What a step is, on the wire. A person's step takes a description; kell's takes a prompt, a locator, or both. */
type SubmitStepIn = { kind: "human" | "automated"; title: string; description?: string; prompt?: string; locator?: string }

export async function POST(req: Request) {
  let body: any = {}
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }

  if (typeof body?.submissionId === "string" && body.submissionId) {
    const id = body.submissionId.trim()
    if (!/^[0-9a-f-]{36}$/i.test(id)) return NextResponse.json({ error: "no submission id" }, { status: 400 })
    try {
      const sub = await callMcpTool("submit_goal", { submission_id: id }, 20_000)
      return NextResponse.json({ submission: sub })
    } catch (err: any) {
      const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300)
      return NextResponse.json({ error: msg || "the submission could not be read" }, { status: err?.status || 502 })
    }
  }

  const goal = typeof body?.goal === "string" ? body.goal.trim().slice(0, 200) : ""
  const description = typeof body?.description === "string" ? body.description.trim().slice(0, 4000) : ""
  const raw = Array.isArray(body?.steps) ? body.steps : []
  // Each step is trimmed here and checked by the backend, which owns the rules and their wording: a locator that is not
  // a website, file, script or tool is refused with the sentence that says so, not guessed at on this side.
  const steps = raw.slice(0, 40).map((s: any) => {
    const kind = s?.kind === "human" ? "human" : "automated"
    const title = String(s?.title || "").trim().slice(0, 200)
    const out: Record<string, string> = { kind, title }
    const desc = String(s?.description || "").trim().slice(0, 4000)
    if (desc) out.description = desc
    if (kind === "automated") {
      const prompt = String(s?.prompt || "").trim().slice(0, 4000)
      const locator = String(s?.locator || "").trim().slice(0, 2000)
      if (prompt) out.prompt = prompt
      if (locator) out.locator = locator
    }
    return out
  })
  if (!goal) return NextResponse.json({ error: "Say what this Way achieves, in a few words." }, { status: 400 })
  if (!steps.length) return NextResponse.json({ error: "Add at least one step." }, { status: 400 })

  try {
    const out = await callMcpTool("submit_goal", {
      goal, description, steps, ...(body?.preview ? { preview: true } : {}),
    }, body?.preview ? 20_000 : 60_000)                    // a submit waits on its reviewer; a preview does not
    return NextResponse.json(body?.preview ? { preview: out } : { submission: out })
  } catch (err: any) {
    const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 400)
    return NextResponse.json({ error: msg || "kell could not take the submission" }, { status: err?.status || 502 })
  }
}
