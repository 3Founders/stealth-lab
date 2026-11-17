// POST { name, goal, steps, instruction }: ask kell to rewrite a Way's steps from a sentence, like the canvas's
// "Change it". One model call (the engine's one-shot generate, no tools, no session): it returns the edited titles and
// notes, and the app puts them in the editor so the graph follows. Kinds, tools and models stay as they were unless the
// person changed them (2026-11-17).
import { engineStatus, clientFor } from "@/lib/engine"

export const dynamic = "force-dynamic"

const INSTRUCTIONS = `You edit the steps of a kell Way (a procedure) for the person who is about to run it.
- Reply with ONLY a JSON object: {"steps":[{"title":"...","note":"..."}]}. No prose, no code fences.
- Keep the same number of steps unless the person explicitly asks to add, remove, merge or split one.
- Rewrite titles so each makes one action clear, and notes to say what to do or what to watch for.
- Keep each step's meaning and order; do not invent tools, models, kinds or facts.
- Write in the person's own language when the request is in another language.`

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "The engine is down; open a project first." }, { status: 502 })
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "Bad request." }, { status: 400 }) }
  const name = String(body?.name || "").slice(0, 200)
  const goal = String(body?.goal || "").slice(0, 300)
  const instruction = String(body?.instruction || "").trim().slice(0, 2000)
  const steps = Array.isArray(body?.steps) ? body.steps.slice(0, 40).map((s: any, i: number) => ({
    order: i + 1,
    title: String(s?.title || "").slice(0, 300),
    note: String(s?.note || "").slice(0, 1000),
    kind: String(s?.kind || "").slice(0, 40),
    ...(s?.tool ? { tool: String(s.tool).slice(0, 120) } : {}),
    ...(s?.model ? { model: String(s.model).slice(0, 120) } : {}),
  })) : []
  if (!instruction || !steps.length) {
    return Response.json({ error: "Say what to change, with at least one step." }, { status: 400 })
  }
  const prompt = `${INSTRUCTIONS}\n\nWay: ${name || "(untitled)"}\nGoal: ${goal || "(none)"}\n`
    + `Steps now (JSON):\n${JSON.stringify(steps, null, 2)}\n\nWhat the person wants changed: ${instruction}`
  try {
    const client = await clientFor(e)
    const out = await client.experimental.generate.text({ prompt })
    const text = String((out as any)?.text || "")
    const at = text.indexOf("{"), end = text.lastIndexOf("}")
    if (at < 0 || end <= at) return Response.json({ error: "The model did not return steps." }, { status: 502 })
    const parsed = JSON.parse(text.slice(at, end + 1))
    const next = Array.isArray(parsed?.steps) ? parsed.steps : []
    if (!next.length) return Response.json({ error: "The model returned no steps." }, { status: 502 })
    // Titles and notes only: the kind, tool and model of each step are the Way's own (or the person's pick).
    const merged = next.slice(0, 40).map((s: any, i: number) => {
      const old = steps[i] || steps[steps.length - 1] || { kind: "prompt" }
      return {
        ...old,
        order: i + 1,
        title: String(s?.title ?? old.title ?? "").slice(0, 300),
        note: String(s?.note ?? old.note ?? "").slice(0, 1000),
      }
    })
    return Response.json({ steps: merged })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err).slice(0, 300) }, { status: 502 })
  }
}
