// The composer's Thinking control: GET the per-model levels, POST the one the person picked.
import { NextResponse } from "next/server"
import { THINKING_LEVELS, readThinking, writeThinking, type ThinkingLevel } from "@/lib/thinking"

export const dynamic = "force-dynamic"

export async function GET() {
  return NextResponse.json({ levels: THINKING_LEVELS, map: readThinking() })
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}))
  const level = String(body?.level || "")
  if (!THINKING_LEVELS.includes(level as ThinkingLevel)) {
    return NextResponse.json({ error: "unknown thinking level" }, { status: 400 })
  }
  const model = typeof body?.model === "string" && body.model ? String(body.model) : undefined
  return NextResponse.json({ map: writeThinking(level as ThinkingLevel, model) })
}
