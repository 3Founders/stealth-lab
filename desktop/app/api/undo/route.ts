// Undo for the changes panel: POST { directory, sessionID?, force? }.
//
// Two kinds of change reach a checkout, and each has exactly one way back:
//
//  1. A session's own edits (an agent edited files directly). Undo restores the turn's before-tree from OpenCode's shadow
//     git (lib/snapshots.ts) -- Claude Code's checkpoint mechanism. The panel's diff (GET /api/diff) reads the same pair,
//     so what it shows is exactly what Undo puts back.
//  2. An answer a dispatched cheap model applied. `kell undo` keeps refs/stealthlab/undo/<id> and refuses without --force
//     when a touched file was edited again since (packaging/npm/lib/undo.mjs).
//
// Neither silently throws away a later edit: the session path compares each changed file with what the turn wrote first.
import fs from "node:fs"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { NextResponse } from "next/server"
import { blobOf, lastPair, pairFiles } from "@/lib/snapshots"

export const dynamic = "force-dynamic"

const kell = () => process.env.KELL_CLI || "kell"

export async function POST(req: Request) {
  try {
    const { directory, sessionID, force } = await req.json()
    if (!directory) return NextResponse.json({ error: "feed me a directory" }, { status: 400 })

    if (sessionID) {
      const p = lastPair(directory, sessionID)
      const changed = p ? pairFiles(p) : []
      if (p && changed.length) {
        // A file that no longer matches what the turn wrote was edited again.
        const edited = force ? [] : changed.filter((f) => {
          const target = path.join(directory, f.file)
          const now = fs.existsSync(target) ? fs.readFileSync(target) : null
          const then = blobOf(p, p.after, f.file)
          return f.status !== "added" && (!now || !then || !now.equals(then))
        }).map((f) => f.file)
        if (edited.length) {
          return NextResponse.json({ needsConfirm: true, warn: `edited since kell's change: ${edited.join(", ")}` })
        }
        const restored: string[] = []
        for (const f of changed) {
          const target = path.join(directory, f.file)
          if (f.status === "added") { fs.rmSync(target, { force: true }); restored.push(f.file); continue }
          const bytes = blobOf(p, p.before, f.file)
          if (!bytes) continue
          fs.mkdirSync(path.dirname(target), { recursive: true })
          fs.writeFileSync(target, bytes)               // bytes, not text: no encoding or newline conversion
          restored.push(f.file)
        }
        return NextResponse.json({ restored, via: "session snapshot" })
      }
      if (p) return NextResponse.json({ error: "this session's last turn changed nothing" }, { status: 400 })
    }

    const r = spawnSync(kell(), ["undo", ...(force ? ["--force"] : [])], { cwd: directory, encoding: "utf8", timeout: 60_000, shell: true })
    return NextResponse.json({ output: (r.stdout || "") + (r.stderr || ""), code: r.status, via: "kell undo" })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 500 })
  }
}


