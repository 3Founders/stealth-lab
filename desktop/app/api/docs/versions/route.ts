// GET  /api/docs/versions?path=<file>   -> { versions: [{ id, at, size }] }  earlier versions of a document, newest first
// POST /api/docs/versions { path, id }  -> { ok }                            put one back (the current file is kept as a version)
import path from "node:path"
import { restoreVersion, versionsOf } from "@/lib/docs"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const p = new URL(req.url).searchParams.get("path") || ""
  return Response.json({ versions: p ? versionsOf(path.resolve(p)) : [] })
}

export async function POST(req: Request) {
  let b: any = {}
  try { b = await req.json() } catch { /* checked below */ }
  if (!b?.path || !b?.id) return Response.json({ error: "send path and id" }, { status: 400 })
  const ok = restoreVersion(path.resolve(String(b.path)), String(b.id))
  return ok ? Response.json({ ok: true }) : Response.json({ error: "that version is not there" }, { status: 404 })
}
