// The project's files, for the sidebar's tree and the file view.
//
//   GET ?directory=<abs>&dir=<rel>    one folder's entries (the tree asks per folder, so a big repository costs nothing
//                                     until a folder is opened)
//   GET ?directory=<abs>&path=<rel>   one file's text
//
// Everything is read from the open project only: a path that resolves outside it (or through a symlink) is refused.
// Generated and dependency folders are not listed, and a file is never read past MAX_BYTES or MAX_LINES so a 200 MB
// log cannot freeze the panel.
import fs from "node:fs/promises"
import path from "node:path"
import { NextResponse } from "next/server"
import { engineStatus } from "@/lib/engine"

export const dynamic = "force-dynamic"

/** Folders the tree never shows: dependencies, build output, caches, VCS metadata. */
const SKIP = new Set([
  ".git", ".hg", ".svn", "node_modules", ".next", "dist", "build", "out", ".output", "coverage",
  ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
  ".turbo", ".cache", ".parcel-cache", ".gradle", ".idea", "target", ".DS_Store",
])
const MAX_ENTRIES = 500
const MAX_BYTES = 2 * 1024 * 1024
const MAX_LINES = 5000

/** The absolute path of `rel` inside `root`, or null when it escapes the project. */
function safe(root: string, rel: string): string | null {
  const full = path.resolve(root, rel || ".")
  const back = path.relative(root, full)
  if (back.startsWith("..") || path.isAbsolute(back)) return null
  return full
}

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams
  const root = q.get("directory") || engineStatus()?.directory || ""
  if (!root) return NextResponse.json({ error: "Open a project first" }, { status: 400 })
  const file = q.get("path")
  const find = q.get("q")
  try {
    if (find !== null) return await search(root, find)
    if (file) return await readFile(root, file)
    return await listFolder(root, q.get("dir") || "")
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 500 })
  }
}

/** File names for the command palette's "go to file": a bounded walk, matched on the path. */
async function search(root: string, q: string) {
  const needle = q.trim().toLowerCase()
  if (!needle) return NextResponse.json({ files: [] })
  const started = Date.now()
  const files: { name: string; path: string; score: number }[] = []
  let visited = 0
  const queue: string[] = [""]
  while (queue.length && visited < 20000 && files.length < 200 && Date.now() - started < 3000) {
    const rel = queue.shift() as string
    let dirents: import("node:fs").Dirent[]
    try { dirents = await fs.readdir(safe(root, rel) as string, { withFileTypes: true }) } catch { continue }
    for (const d of dirents) {
      if (d.name.startsWith(".") && d.name !== ".env.example") continue
      const p = path.posix.join(rel.replace(/\\/g, "/"), d.name)
      if (d.isDirectory()) { if (!SKIP.has(d.name)) queue.push(p); continue }
      visited++
      const lower = p.toLowerCase()
      const at = lower.indexOf(needle)
      if (at < 0) continue
      const base = d.name.toLowerCase()
      files.push({ name: d.name, path: p, score: base.startsWith(needle) ? 0 : base.includes(needle) ? 1 : 2 })
    }
  }
  files.sort((a, b) => a.score - b.score || a.path.length - b.path.length || a.path.localeCompare(b.path))
  return NextResponse.json({ files: files.slice(0, 40).map(({ name, path }) => ({ name, path })), more: Math.max(0, files.length - 40) })
}

async function listFolder(root: string, rel: string) {
  const full = safe(root, rel)
  if (!full) return NextResponse.json({ error: "That folder is outside the project" }, { status: 400 })
  const dirents = await fs.readdir(full, { withFileTypes: true })
  const entries = dirents
    .filter((d) => !SKIP.has(d.name))
    .map((d) => ({
      name: d.name,
      path: path.posix.join(rel.replace(/\\/g, "/"), d.name),
      dir: d.isDirectory(),
    }))
    .sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name, undefined, { numeric: true }) : a.dir ? -1 : 1))
  return NextResponse.json({
    directory: root, dir: rel, entries: entries.slice(0, MAX_ENTRIES), more: Math.max(0, entries.length - MAX_ENTRIES),
  })
}

async function readFile(root: string, rel: string) {
  const full = safe(root, rel)
  if (!full) return NextResponse.json({ error: "That file is outside the project" }, { status: 400 })
  const st = await fs.stat(full)
  if (!st.isFile()) return NextResponse.json({ error: "That is a folder" }, { status: 400 })
  const buf = await fs.readFile(full)
  const binary = buf.subarray(0, 8000).includes(0)
  if (binary) return NextResponse.json({ path: rel, bytes: st.size, binary: true, text: "", lines: 0, truncated: false })
  const truncatedBytes = buf.length > MAX_BYTES
  let text = buf.subarray(0, MAX_BYTES).toString("utf8")
  const all = text.split("\n")
  const truncated = truncatedBytes || all.length > MAX_LINES
  if (all.length > MAX_LINES) text = all.slice(0, MAX_LINES).join("\n")
  return NextResponse.json({
    path: rel, bytes: st.size, text, lines: Math.min(all.length, MAX_LINES), truncated, binary: false,
  })
}
