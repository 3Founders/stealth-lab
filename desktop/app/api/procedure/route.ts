// POST { submissionId } -> the steps of a submitted Way, for the procedure graph.
//
// A submitted Way's steps are sub-Way references: find_ways names each step's source (goal_submission:<id>) but not
// its title until the backend's title field is deployed. This reads the submission the person owns and returns the
// titles the graph needs. Read-only: it never fixes, approves or runs anything.
import { NextResponse } from "next/server"
import { callMcpTool } from "@/lib/mcp"

export const dynamic = "force-dynamic"

const CACHE_TTL_MS = 5 * 60_000
const CACHE_MAX = 32
const cache = new Map<string, { at: number; steps: any[] }>()

export async function POST(req: Request) {
  let body: any = {}
  try { body = await req.json() } catch { /* empty */ }
  const id = String(body?.submissionId || "").trim()
  if (!/^[0-9a-f-]{36}$/i.test(id)) return NextResponse.json({ error: "no submission id" }, { status: 400 })

  const hit = cache.get(id)
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return NextResponse.json({ steps: hit.steps })

  try {
    const sub = await callMcpTool("submit_goal", { submission_id: id }, 20_000)
    const steps = (Array.isArray(sub?.steps) ? sub.steps : []).map((s: any) => ({
      order: Number(s?.order ?? 0),
      kind: String(s?.kind || ""),
      title: String(s?.title || s?.description || s?.prompt || ""),
      status: String(s?.status || ""),
    })).filter((s: any) => s.title)
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string)
    cache.set(id, { at: Date.now(), steps })
    return NextResponse.json({ steps })
  } catch (err: any) {
    const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 240)
    return NextResponse.json({ error: msg || "the submission could not be read" }, { status: err?.status || 502 })
  }
}
