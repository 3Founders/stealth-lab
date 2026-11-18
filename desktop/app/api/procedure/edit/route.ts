// POST { name, goal, steps, instruction, files?, model? }: ask kell to rewrite a Way's steps from a sentence and any
// pictures, like the canvas's "Change it". One model call (the engine's one-shot generate, no tools, no session): it
// returns the edited titles and notes, and the app puts them in the editor so the graph follows. Kinds, tools and models
// stay as they were unless the person changed them (2026-11-17).
//
// Pictures are named in the prompt and sent as the call's files (a data: URL, source inline: the reference schema's own
// shape). The route cannot know which models see pictures, so it does not pretend: when the request carried pictures and
// the model answered as if it never saw them, it says that in one line and the editor shows the steps anyway.
import { engineStatus, clientFor } from "@/lib/engine"

export const dynamic = "force-dynamic"

/** What /api/prompt inlines for a file too (12 MB); larger is refused here rather than sent and dropped by the engine. */
const MAX_FILE_CHARS = 12_000_000

const INSTRUCTIONS = `You edit the steps of a kell Way (a procedure) for the person who is about to run it.
- Reply with ONLY a JSON object: {"steps":[{"title":"...","note":"..."}]}. No prose, no code fences.
- Keep the same number of steps unless the person explicitly asks to add, remove, merge or split one.
- Rewrite titles so each makes one action clear, and notes to say what to do or what to watch for.
- Keep each step's meaning and order; do not invent tools, models, kinds or facts.
- Write in the person's own language when the request is in another language.
- Pictures attached with the request are part of it: read what they show (a screen, a form, a sketch, an error) and let it shape the steps.`

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
  // Pictures and PDFs the bar carried: only inline data is taken, and only up to the size the prompt route inlines.
  const files = (Array.isArray(body?.files) ? body.files : [])
    .filter((f: any) => f && typeof f.dataUrl === "string" && /^data:[^,]+,/.test(f.dataUrl))
    .slice(0, 4)
    .map((f: any) => ({ uri: String(f.dataUrl), source: { type: "inline" }, name: String(f.name || "attachment").slice(0, 200) }))
    .filter((f: any) => f.uri.length <= MAX_FILE_CHARS)
  if (!instruction && !files.length) {
    return Response.json({ error: "Say what to change, or add a picture to work from." }, { status: 400 })
  }
  if (!steps.length) return Response.json({ error: "This Way has no steps to edit." }, { status: 400 })
  // The bar's own model, when the person picked one; otherwise the engine's default for this one-shot call.
  const model = body?.model && typeof body.model.id === "string" && typeof body.model.providerID === "string"
    ? { id: body.model.id, providerID: body.model.providerID } : undefined
  const listed = files.length
    ? `\n\nAttached pictures (in order): ${files.map((f: any, i: number) => `${i + 1}. ${f.name}`).join(", ")}`
    : ""
  const prompt = `${INSTRUCTIONS}\n\nWay: ${name || "(untitled)"}\nGoal: ${goal || "(none)"}\n`
    + `Steps now (JSON):\n${JSON.stringify(steps, null, 2)}`
    + `\n\nWhat the person wants changed: ${instruction || "(nothing written: use the attached pictures)"}${listed}`
  try {
    const client = await clientFor(e)
    const out = await client.experimental.generate.text({ prompt, ...(model ? { model } : {}), ...(files.length ? { files } : {}) } as any)
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
    // Honest about the pictures: a text-only model answers from the words alone, and the person should know which line
    // shaped the steps rather than find out when the same picture failed to change anything.
    const saw = files.length
      ? "The steps were rewritten. If kell seemed to ignore the pictures, the model this bar is set to may not read them; pick one that does."
      : undefined
    return Response.json({ steps: merged, ...(saw ? { note: saw } : {}) })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err).slice(0, 300) }, { status: 502 })
  }
}
