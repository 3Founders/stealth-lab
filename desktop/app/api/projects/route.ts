// The recent-project list, kept in the app's own SQLite store (docs/localdb.md), not in localStorage. The app's origin
// changes on every start and electron/memory.ts used to clear the page's storage, so the list vanished and the prefs
// mirror wrote the empty list back. This route is the source of truth.
//
//   GET    /api/projects                 recent folders, newest first (seeds from the engine once)
//   POST   /api/projects { path }        remember a folder (or { paths: [...] } to merge the engine's list)
//   DELETE /api/projects?path=<abs>      hide a folder (the × in the sidebar)
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { DatabaseSync } from "node:sqlite"
import { anyProjects, forgetProject, importProjects, listProjects, mergeProjects, rememberProject } from "@/lib/localdb"

export const dynamic = "force-dynamic"

const list = () => listProjects(20).map((p) => p.path)

/** A folder worth listing: not the home folder itself, not a drive root, not a scratch folder under the system temp. */
function keepPath(raw: string): string | null {
  const p = path.normalize(raw)
  if (!p) return null
  const low = p.toLowerCase()
  if (low === os.homedir().toLowerCase() || low === path.parse(p).root.toLowerCase() || low.startsWith(os.tmpdir().toLowerCase())) return null
  return p
}

/** First run after the old localStorage list was lost: take the engine's own recent projects, without temp folders. */
function seedFromEngine(): void {
  const file = path.join(os.homedir(), ".local", "share", "opencode", "opencode.db")
  if (!fs.existsSync(file)) return
  let db: DatabaseSync | undefined
  try {
    db = new DatabaseSync(file, { readOnly: true })
    const rows = db.prepare("select worktree, time_active from project order by time_active desc limit 30").all() as any[]
    const out: { path: string; last: number }[] = []
    for (const r of rows) {
      const p = keepPath(String(r.worktree || ""))
      if (!p) continue
      out.push({ path: p, last: Number(r.time_active) || 0 })
      if (out.length >= 12) break
    }
    importProjects(out)
  } catch { /* no engine database yet: the list starts empty */ }
  finally { try { db?.close() } catch { /* already closed */ } }
}

export async function GET() {
  try {
    if (!anyProjects()) seedFromEngine()
    return Response.json({ projects: list() })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err), projects: [] }, { status: 500 })
  }
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "JSON body" }, { status: 400 }) }
  try {
    if (Array.isArray(body?.paths)) mergeProjects(body.paths.map((p: unknown) => keepPath(String(p))).filter(Boolean) as string[])
    else if (typeof body?.path === "string" && body.path.trim()) rememberProject(body.path.trim(), typeof body.name === "string" ? body.name : null)
    else return Response.json({ error: "give a path" }, { status: 400 })
    return Response.json({ projects: list() })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 500 })
  }
}

export async function DELETE(req: Request) {
  const p = new URL(req.url).searchParams.get("path")
  if (!p) return Response.json({ error: "give a path" }, { status: 400 })
  try {
    forgetProject(p)
    return Response.json({ projects: list() })
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 500 })
  }
}
