// POST { plan, question } -> the delegated model's answer, streamed as status updates.
//
// The call itself is one `generate.text` (no tools, no files, no project path, no chat history): this route reports when
// the brief goes out and how long the worker has been thinking, then the answer. With `Accept: application/json` it is
// one JSON reply, for scripts.
import { NextResponse } from "next/server"
import { askDelegated, type Delegation } from "@/lib/delegate"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  let body: any = {}
  try { body = await req.json() } catch { /* empty */ }
  const plan = body?.plan as Delegation | undefined
  const question = String(body?.question || "").trim().slice(0, 4000)
  if (!plan?.brief) return NextResponse.json({ error: "no delegation plan" }, { status: 400 })
  if (!question) return NextResponse.json({ error: "ask a question" }, { status: 400 })
  const wantsStream = (req.headers.get("accept") || "").includes("text/event-stream")
  const who = plan.model?.id || "the delegated model"

  if (!wantsStream) {
    try {
      return NextResponse.json(await askDelegated(plan, question))
    } catch (err: any) {
      const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300)
      return NextResponse.json({ error: msg || "the delegated model failed" }, { status: err?.status || 502 })
    }
  }

  const stream = new ReadableStream<Uint8Array>({
    async start(ctrl) {
      const enc = new TextEncoder()
      const send = (obj: unknown) => ctrl.enqueue(enc.encode(`data: ${JSON.stringify(obj)}\n\n`))
      const t0 = Date.now()
      send({ type: "status", step: "sent", text: `Sent the brief to ${who} — no files, no shell, no history` })
      send({ type: "status", step: "waiting", text: `${who} is working…` })
      const timer = setInterval(() => {
        try {
          send({ type: "status", step: "waiting", text: `${who} is working… ${Math.round((Date.now() - t0) / 1000)}s` })
        } catch { /* the reader went away; the answer is still awaited */ }
      }, 2000)
      try {
        const out = await askDelegated(plan, question)
        send({ type: "answer", answer: out.answer, model: out.model })
      } catch (err: any) {
        const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300)
        send({ type: "error", error: msg || "the delegated model failed" })
      } finally {
        clearInterval(timer)
        send({ type: "done" })
        ctrl.close()
      }
    },
  })
  return new Response(stream, {
    headers: { "content-type": "text/event-stream", "cache-control": "no-store", "x-accel-buffering": "no" },
  })
}
