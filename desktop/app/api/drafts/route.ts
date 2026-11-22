// POST { action: "list" | "get" | "save" | "delete", ... } -> the drafts kept on this machine (lib/procedures-local.ts).
//
// A draft is a Way being written: it lives in the app's own chats.db, never on the server, and is runnable only here, by
// this person, through their own engine. Nothing in this route talks to kell's servers, so a draft costs nothing and is
// invisible to anyone else. It leaves this machine only when the person submits it (app/api/submit).
import { NextResponse } from "next/server"
import { deleteDraft, getDraft, listDrafts, saveDraft, type Draft, type DraftStep } from "@/lib/procedures-local"

export const dynamic = "force-dynamic"

type Body = {
  action?: string
  id?: string
  goal?: string
  description?: string
  steps?: unknown
  model?: string
}

/** The steps as the form holds them, trimmed to what a step may carry. A person's step has no prompt or locator. */
function stepsOf(raw: unknown): DraftStep[] {
  return (Array.isArray(raw) ? raw : []).slice(0, 40).map((s: any) => {
    const kind: DraftStep["kind"] = s?.kind === "human" ? "human" : "automated"
    const out: DraftStep = { kind }
    const title = String(s?.title || "").trim().slice(0, 200)
    if (title) out.title = title
    if (kind === "human") {
      const description = String(s?.description || "").trim().slice(0, 4000)
      if (description) out.description = description
    } else {
      const prompt = String(s?.prompt || "").trim().slice(0, 4000)
      if (prompt) out.prompt = prompt
      const locator = String(s?.locator || "").trim().slice(0, 2000)
      if (locator) out.locator = locator
    }
    return out
  })
}

export async function POST(req: Request) {
  let body: Body
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  const action = String(body?.action || "")
  try {
    if (action === "list") return NextResponse.json({ drafts: listDrafts() })
    if (action === "get") {
      if (!body.id) return NextResponse.json({ error: "which draft?" }, { status: 400 })
      const d = getDraft(String(body.id))
      return d ? NextResponse.json({ draft: d }) : NextResponse.json({ error: "no draft with that id" }, { status: 404 })
    }
    if (action === "save") {
      // A draft needs a goal to be worth keeping, but its steps may be half written: that is the point of a draft.
      const goal = String(body.goal || "").trim().slice(0, 200)
      if (!goal) return NextResponse.json({ error: "Give the draft a goal so you can find it again." }, { status: 400 })
      const saved: Draft = saveDraft({
        id: String(body.id || crypto.randomUUID()),
        goal,
        description: String(body.description || "").trim().slice(0, 4000),
        steps: stepsOf(body.steps),
        ...(body.model ? { model: String(body.model).slice(0, 120) } : {}),
      })
      return NextResponse.json({ draft: saved })
    }
    if (action === "delete") {
      if (!body.id) return NextResponse.json({ error: "which draft?" }, { status: 400 })
      deleteDraft(String(body.id))
      return NextResponse.json({ ok: true })
    }
    return NextResponse.json({ error: `unknown action ${action || "(none)"}` }, { status: 400 })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err).slice(0, 300) }, { status: 500 })
  }
}
