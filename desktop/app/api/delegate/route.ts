// The delegation plan for one prompt. POST { text, models } -> the router's Goal/Way/Tool, the model this turn runs on,
// and the least-privilege brief. Read-only: nothing is called and no plan is written.
//
// Two shapes: plain JSON for a one-shot caller, and (with `Accept: text/event-stream`) the same answer as live status
// updates -- ladder -> routing -> goal -> model -> plan. The desktop reads the stream so the panel can show what the
// delegated worker is doing instead of going silent for 20 seconds.
//
// The models come from the page (app/api/settings): a route may not ask the engine for its ladder on every keystroke.
import { NextResponse } from "next/server"
import { planFromRoute, routeTask, type Delegation, type Routed } from "@/lib/delegate"

export const dynamic = "force-dynamic"

// A prompt repeated within a few minutes (a retry, a re-send, a queued message) should not pay the router twice. The plan
// is a pure read of the task and the model ladder, so caching it cannot go stale in a way that matters.
const CACHE_TTL_MS = 5 * 60_000
const CACHE_MAX = 64
const cache = new Map<string, { at: number; plan: Delegation }>()

/** The app's model ladder: what the page sent, else this server's own Settings answer (the gateway list can arrive late). */
async function ladder(req: Request, given: any): Promise<Record<string, string | null>> {
  if (given?.strong || given?.cheap) return given
  try {
    const r = await fetch(new URL("/api/settings", req.url), { signal: AbortSignal.timeout(8000) })
    return ((await r.json())?.models) || given || {}
  } catch { return given || {} }
}

const cached = (key: string) => {
  const hit = cache.get(key)
  return hit && Date.now() - hit.at < CACHE_TTL_MS ? hit.plan : null
}

const remember = (key: string, plan: Delegation) => {
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string)
  cache.set(key, { at: Date.now(), plan })
}

/** One line for the panel: the Goal, the Way and the tool in the router's own words. */
function goalLine(routed: Routed): string {
  const bits = [
    routed.goal ? `Goal: ${routed.goal.name}` : "no Goal matched",
    routed.way ? `Way: ${routed.way.name} [${routed.way.approval}]` : null,
    routed.tool ? `Tool: ${routed.tool.name}` : null,
  ].filter(Boolean)
  return bits.join(" · ")
}

function sse(stream: ReadableStream<Uint8Array>): Response {
  return new Response(stream, {
    headers: { "content-type": "text/event-stream", "cache-control": "no-store", "x-accel-buffering": "no" },
  })
}

export async function POST(req: Request) {
  let body: any = {}
  try { body = await req.json() } catch { /* empty */ }
  const text = String(body?.text || "").slice(0, 4000)
  if (!text.trim()) return NextResponse.json({ error: "nothing to route" }, { status: 400 })
  const given = body?.models
  const timeoutMs = Number(body?.timeoutMs) || 20_000
  const wantsStream = (req.headers.get("accept") || "").includes("text/event-stream")

  if (!wantsStream) {
    const models = await ladder(req, given)
    const key = `${text}\u0000${JSON.stringify(models)}`
    const hit = cached(key)
    if (hit) return NextResponse.json(hit)
    try {
      const plan = planFromRoute(text, models, await routeTask(text, timeoutMs))
      remember(key, plan)
      return NextResponse.json(plan)
    } catch (err: any) {
      return NextResponse.json({ error: String(err?.message || err) }, { status: err?.status || 502 })
    }
  }

  const stream = new ReadableStream<Uint8Array>({
    async start(ctrl) {
      const enc = new TextEncoder()
      const send = (obj: unknown) => ctrl.enqueue(enc.encode(`data: ${JSON.stringify(obj)}\n\n`))
      const status = (step: string, line: string, extra: Record<string, unknown> = {}) =>
        send({ type: "status", step, text: line, ...extra })
      try {
        status("ladder", "Reading the model ladder…")
        const models = await ladder(req, given)
        const key = `${text}\u0000${JSON.stringify(models)}`
        const hit = cached(key)
        if (hit) {
          status("cached", "Using the plan from a moment ago")
          send({ type: "plan", plan: hit })
          send({ type: "done" })
          return
        }
        status("routing", "Asking kell's router (find_ways)…")
        const routed = await routeTask(text, timeoutMs)
        status("goal", goalLine(routed), { goal: routed.goal, way: routed.way, tool: routed.tool })
        const plan = planFromRoute(text, models, routed)
        status("model", plan.model ? `Model: ${plan.model.id} (${plan.rung}) — ${plan.why}` : `Model: the engine's default — ${plan.why}`,
               { model: plan.model, rung: plan.rung })
        remember(key, plan)
        send({ type: "plan", plan })
        send({ type: "done" })
      } catch (err: any) {
        send({ type: "error", error: String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300) })
        send({ type: "done" })
      } finally {
        ctrl.close()
      }
    },
  })
  return sse(stream)
}
