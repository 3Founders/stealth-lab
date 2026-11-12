// Goal -> Way -> Tool for the desktop Route page. The page is local; the answer comes from the MCP server the app is
// already signed in to (app/mcp_server/room_agent_routes.py -> app/tool_routing/route_api.py), which is the same code
// the website's GET /v1/goals/route calls. Read-only: nothing is called and no plan is written.
import { NextResponse } from "next/server"
import { routeGoals } from "@/lib/tools"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const params = new URL(req.url).searchParams
  const q = (params.get("q") || "").slice(0, 4000)
  if (!q.trim()) return NextResponse.json({ query: "", goals: [], tools: [] })
  try {
    return NextResponse.json(await routeGoals(q, Number(params.get("k")) || 5))
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: err?.status || 502 })
  }
}
