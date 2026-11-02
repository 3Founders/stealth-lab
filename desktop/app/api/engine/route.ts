// POST { directory } boots (or reuses) the engine for that project; GET returns its status.
import { NextResponse } from "next/server"
import { engineStart, engineStatus } from "@/lib/engine"

export async function GET() {
  const e = engineStatus()
  return e ? NextResponse.json({ up: true, baseUrl: e.baseUrl, directory: e.directory }) : NextResponse.json({ up: false })
}

export async function POST(req: Request) {
  try {
    const { directory } = await req.json()
    if (!directory) return NextResponse.json({ error: "feed me a directory" }, { status: 400 })
    const e = await engineStart(directory)
    return NextResponse.json({ up: true, baseUrl: e.baseUrl, directory: e.directory })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 502 })
  }
}
