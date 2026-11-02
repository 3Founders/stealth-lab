// OpenCode's turn snapshots, read directly: the mechanism behind the changes panel and Undo.
//
// OpenCode saves the working tree into a shadow git (data/opencode/snapshot/<project>/<repo>) around each turn as TREE objects
// -- the turn's before- and after-trees are both kept, with no commits or refs. This is Claude Code's checkpoint mechanism.
// The engine's own session.revert.stage does not apply them here (it answers files: [] and changes nothing), so kell's app
// reads the pair itself and writes the before-blobs back.
//
// Which pair belongs to a turn is recorded, not guessed: every assistant message in OpenCode's database (opencode.db,
// session_message.data) carries `snapshot: { start, end, files }` -- the before-tree, the after-tree and the files between
// them. The app opens that database READ-ONLY and takes the session's newest assistant message that has a snapshot.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { DatabaseSync } from "node:sqlite"

export type SnapshotPair = { before: string; after: string; gitdir: string; files: string[] }
export type SnapshotFile = { file: string; patch: string; additions: number; deletions: number; status: "added" | "deleted" | "modified" }

const dataDir = () => path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "opencode")

const gib = (gitdir: string, args: string[], max = 64 * 1024 * 1024) => {
  const r = spawnSync("git", [`--git-dir=${gitdir}`, ...args], { encoding: "utf8", timeout: 60_000, windowsHide: true, maxBuffer: max })
  return r.status === 0 ? r.stdout : ""
}

/** The shadow git for this project, or null when OpenCode has never snapshotted it. It names its work tree in `config`. */
export function shadowGit(directory: string): string | null {
  const root = path.join(dataDir(), "snapshot")
  let repos: string[] = []
  try { repos = fs.readdirSync(root) } catch { return null }
  const want = path.resolve(directory).toLowerCase()
  for (const r of repos) {
    let kids: string[] = []
    try { kids = fs.readdirSync(path.join(root, r)) } catch { continue }
    for (const g of kids) {
      const gitdir = path.join(root, r, g)
      const cfg = (() => { try { return fs.readFileSync(path.join(gitdir, "config"), "utf8") } catch { return "" } })()
      const m = cfg.match(/worktree\s*=\s*(.+)/)
      if (m && path.resolve(m[1].trim()).toLowerCase() === want) return gitdir
    }
  }
  return null
}

/** The session's newest turn snapshot, from OpenCode's own record of it. */
export function lastPair(directory: string, sessionID: string): SnapshotPair | null {
  const gitdir = shadowGit(directory)
  if (!gitdir) return null
  const dbFile = path.join(dataDir(), "opencode.db")
  if (!fs.existsSync(dbFile)) return null
  let db: DatabaseSync | undefined
  try {
    // Read-only, but on the LIVE database: the turn being shown is usually the newest one and may still sit in the WAL
    // file, so a copied snapshot of opencode.db would miss it. busy_timeout avoids failing while the engine is writing.
    db = new DatabaseSync(dbFile, { readOnly: true, timeout: 5000 })
    db.exec("pragma busy_timeout = 5000")
    // Steps are recorded newest first. A turn is everything after the session's last user message; its range is the first
    // step's start tree to the last step's end tree. Taking the whole turn (not just the steps that touched source) means
    // Undo puts back one coherent earlier state, including the noise a test run made inside the turn.
    const all = db.prepare(
      "select seq, type, data from session_message where session_id = ? order by seq desc limit 200"
    ).all(sessionID) as { seq: number; type: string; data: string }[]
    const steps: { start: string; end: string }[] = []
    for (const row of all) {
      if (row.type === "user") break                      // the turn's start
      if (row.type !== "assistant") continue
      let d: any
      try { d = JSON.parse(row.data) } catch { continue }
      const s = d?.snapshot
      if (s?.start && s?.end && s.start !== s.end) steps.push({ start: s.start, end: s.end })
    }
    if (!steps.length) return null
    const after = steps[0].end                              // newest step's end
    const before = steps[steps.length - 1].start            // oldest step of this turn
    return { before, after, gitdir, files: [] }
  } catch { return null } finally { try { db?.close() } catch { /* already closed */ } }
}

/** The files a pair differs in, with their patch -- what the panel lists and Undo restores.
 *  Build and tool noise (byte-compiled caches, editor temp files) is left out: an agent's turn that ran tests would
 *  otherwise list __pycache__/*.pyc, and Undo would "restore" them, which is never what the person asked for. */
const NOISE = /(^|\/)(__pycache__|node_modules|\.git|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.tox)(\/|$)|\.(pyc|pyo|class|o|obj|swp|tmp)$/i
export const isNoise = (file: string) => NOISE.test(file)

export function pairFiles(p: SnapshotPair): SnapshotFile[] {
  const numstat = gib(p.gitdir, ["diff", "--numstat", "-z", "--no-renames", p.before, p.after, "--"])
  const patch = gib(p.gitdir, ["diff", "--no-renames", "--no-color", "--unified=3", p.before, p.after, "--"])
  const counts = new Map<string, [number, number]>()
  for (const rec of numstat.split("\0")) {
    const m = rec.match(/^(\d+|-)\t(\d+|-)\t([\s\S]+)$/)
    if (m) counts.set(m[3], [m[1] === "-" ? 0 : Number(m[1]), m[2] === "-" ? 0 : Number(m[2])])
  }
  return patch.split(/^diff --git /m).slice(1).map((chunk) => {
    const body = "diff --git " + chunk
    const file = body.match(/^\+\+\+ b\/(.+)$/m)?.[1] || body.match(/^--- a\/(.+)$/m)?.[1] || ""
    const [additions, deletions] = counts.get(file) || [0, 0]
    const status: SnapshotFile["status"] = /^new file mode/m.test(body) ? "added" : /^deleted file mode/m.test(body) ? "deleted" : "modified"
    return { file, patch: body, additions, deletions, status }
  }).filter((f) => f.file && !NOISE.test(f.file))
}

/** A file's bytes as a tree holds them, or null when the tree has no such file. */
export function blobOf(p: SnapshotPair, tree: string, file: string): Buffer | null {
  const hash = gib(p.gitdir, ["rev-parse", `${tree}:${file}`]).trim()
  if (!hash) return null
  const r = spawnSync("git", [`--git-dir=${p.gitdir}`, "cat-file", "blob", hash], { maxBuffer: 64 * 1024 * 1024, windowsHide: true })
  return r.status === 0 ? r.stdout : null
}
