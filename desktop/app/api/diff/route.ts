// GET ?sessionID=... : the changes panel's data.
//
// With a sessionID: the diff of its last turn, read from the SAME snapshot pair POST /api/undo restores (OpenCode's shadow
// git, before- and after-trees), so what the panel shows is exactly what Undo puts back. Without one: the working tree's
// diff, through the engine (client.vcs.diff), for files changed outside a session or after a reset.
import fs from "node:fs"
import path from "node:path"
import { NextResponse } from "next/server"
import { clientFor, engineStatus } from "@/lib/engine"
import { blobOf, isNoise, lastPair, pairFiles } from "@/lib/snapshots"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const e = engineStatus()
  if (!e) return NextResponse.json({ error: "engine is down; open a project first" }, { status: 502 })
  const sessionID = new URL(req.url).searchParams.get("sessionID") || ""
  try {
    if (sessionID) {
      const p = lastPair(e.directory, sessionID)
      if (!p) return NextResponse.json({ directory: e.directory, sessionID, snapshot: null, files: [] })
      const files = pairFiles(p)
      // The turn's diff stays as history; `pending` says whether there is still anything to undo: a file counts only while
      // it differs from the BEFORE tree (done once a file matches before again; a file edited since still counts, so Undo
      // offers and the confirm step guards it).
      const pending = files.filter((f) => {
        const target = path.join(e.directory, f.file)
        const now = fs.existsSync(target) ? fs.readFileSync(target) : null
        const was = blobOf(p, p.before, f.file)
        if (!now || !was) return f.status !== "added" ? !now : true
        return !now.equals(was)
      }).length
      return NextResponse.json({ directory: e.directory, sessionID, snapshot: { before: p.before, after: p.after }, files, pending })
    }
    const client = await clientFor(e)
    const r = await client.vcs.diff({ location: { directory: e.directory }, mode: "working", untracked: "files", context: 3 })
    return NextResponse.json({ directory: e.directory, sessionID: null, files: r.data.filter((f) => !isNoise(f.file)) })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 502 })
  }
}


