// GET /api/way-artifact?run=<run id>&name=<file>  -> the file, streamed to the browser.
//
// A Way's output is licence-gated (docs/procedure_outputs.md), and the backend reads the session from
// `Authorization` only. A browser download carries no header, and the token lives on the server side of this app
// (lib/mcp.ts holds the Rooms sign-in), so the fetch happens here and the bytes are streamed back with the filename
// attached. Same seam as app/api/submissions: the browser never touches the token.
import { NextResponse } from "next/server"

import { callWebRouteBinary } from "@/lib/mcp"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const url = new URL(req.url)
  const run = String(url.searchParams.get("run") || "")
  const name = String(url.searchParams.get("name") || "")
  if (!run) return NextResponse.json({ error: "give a run id" }, { status: 400 })

  // `name` absent means the whole deliverable as a zip.
  const path = name
    ? `/v1/goals/runs/${encodeURIComponent(run)}/artifacts/${encodeURIComponent(name)}`
    : `/v1/goals/runs/${encodeURIComponent(run)}/artifacts.zip`

  try {
    const res = await callWebRouteBinary(path)
    if (res.status === 402) {
      return NextResponse.json({ error: "buy this Way before downloading its output" }, { status: 402 })
    }
    if (!res.ok) {
      return NextResponse.json({ error: `the backend answered ${res.status}` }, { status: res.status })
    }
    const body = await res.arrayBuffer()
    const fallback = name || "way-output.zip"
    // Keep the backend's filename when it sent one; otherwise name the file after the requested artifact.
    const disposition = res.headers.get("content-disposition") || `attachment; filename="${fallback}"`
    return new NextResponse(body, {
      status: 200,
      headers: {
        "content-type": res.headers.get("content-type") || "application/octet-stream",
        "content-disposition": disposition,
        "cache-control": "private, no-store",
      },
    })
  } catch (e) {
    return NextResponse.json({ error: String((e as Error)?.message || e) }, { status: 502 })
  }
}
