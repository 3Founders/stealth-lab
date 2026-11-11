// Tool approvals in the desktop app (docs/goal_router.md P1-11): list the pending calls, or approve/deny one.
// The card polls GET; the buttons POST { id, action }. The MCP token and the server address come from
// ~/.stealthlab/config.json (lib/tools.ts), never from the browser.
import { NextResponse } from "next/server"
import { approveTool, denyTool, toolApprovals } from "@/lib/tools"

export const dynamic = "force-dynamic"

export async function GET() {
  try {
    return NextResponse.json({ approvals: await toolApprovals() })
  } catch (err: any) {
    return NextResponse.json({ approvals: [], error: String(err?.message || err) }, { status: 502 })
  }
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  const id = String(body?.id || "").slice(0, 200)
  const action = String(body?.action || "approve")
  if (!id) return NextResponse.json({ error: "an approval id is required" }, { status: 400 })
  try {
    const ok = action === "deny" ? await denyTool(id) : await approveTool(id)
    return NextResponse.json({ ok })
  } catch (err: any) {
    return NextResponse.json({ ok: false, error: String(err?.message || err) }, { status: 502 })
  }
}
