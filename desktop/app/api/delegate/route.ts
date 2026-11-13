// The delegation plan for one prompt. POST { text, models } -> the router's Goal/Way/Tool, the model this turn runs on,
// and the least-privilege brief. Read-only: nothing is called and no plan is written.
//
// The models come from the page (app/api/settings): a route may not ask the engine for its ladder on every keystroke.
import { NextResponse } from "next/server"
import { planDelegation } from "@/lib/delegate"

export const dynamic = "force-dynamic"

// A prompt repeated within a few minutes (a retry, a re-send, a queued message) should not pay the router twice. The plan
// is a pure read of the task and the model ladder, so caching it cannot go stale in a way that matters.
const CACHE_TTL_MS = 5 * 60_000
const CACHE_MAX = 64
const cache = new Map<string, { at: number; plan: unknown }>()

export async function POST(req: Request) {
  let body: any = {}
  try { body = await req.json() } catch { /* empty */ }
  const text = String(body?.text || "").slice(0, 4000)
  if (!text.trim()) return NextResponse.json({ error: "nothing to route" }, { status: 400 })
  const models = body?.models || {}
  const key = `${text}\u0000${JSON.stringify(models)}`
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return NextResponse.json(hit.plan)
  try {
    const plan = await planDelegation(text, models, Number(body?.timeoutMs) || 20_000)
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string)
    cache.set(key, { at: Date.now(), plan })
    return NextResponse.json(plan)
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: err?.status || 502 })
  }
}
