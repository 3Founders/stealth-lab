// POST { plan, question } -> one scoped answer from the delegated model: the brief and the question, no tools, no files,
// no project path, no chat history. This is the "employee" call: it sees only what the task needs (lib/delegate.ts).
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
  try {
    return NextResponse.json(await askDelegated(plan, question))
  } catch (err: any) {
    const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300)
    return NextResponse.json({ error: msg || "the delegated model failed" }, { status: err?.status || 502 })
  }
}
