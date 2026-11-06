// The folder's uncommitted changes, read with git itself (2026-11-06): the Changes panel's "Other changes in this folder" list, beside
// what kell changed in its own last turn. Tracked files get their diff; new files are listed by name only (a scratch folder can hold
// hundreds, and diffing each made the panel hang on a large repo). Bounded in time and size; a folder that is not a git repository
// simply has no list.
import { execFile } from "node:child_process"
import { isNoise } from "./snapshots"

export type WorkFile = { file: string; patch: string; additions: number; deletions: number; status: "added" | "deleted" | "modified" }

const MAX_FILES = 200
const MAX_PATCH = 60_000           // characters of diff shown per file

const git = (dir: string, args: string[]): Promise<string> => new Promise((ok) => {
  execFile("git", ["-c", "core.quotepath=off", ...args], { cwd: dir, timeout: 15_000, maxBuffer: 32 * 1024 * 1024, windowsHide: true },
    (err, out) => ok(err ? "" : String(out)))
})

export async function worktreeChanges(dir: string): Promise<{ files: WorkFile[]; more: number; repo: boolean }> {
  if (!(await git(dir, ["rev-parse", "--is-inside-work-tree"])).trim()) return { files: [], more: 0, repo: false }
  const [numstat, patch, untracked] = await Promise.all([
    git(dir, ["diff", "HEAD", "--numstat", "--no-renames", "-z", "--"]),
    git(dir, ["diff", "HEAD", "--no-renames", "--no-color", "--unified=3", "--"]),
    git(dir, ["ls-files", "--others", "--exclude-standard", "-z"]),
  ])
  const patches = new Map<string, string>()
  for (const chunk of patch.split(/^(?=diff --git )/m)) {
    const m = chunk.match(/^diff --git a\/(.+?) b\//)
    if (m) patches.set(m[1], chunk.length > MAX_PATCH ? chunk.slice(0, MAX_PATCH) + "\n… (cut here: the diff goes on)" : chunk)
  }
  const files: WorkFile[] = []
  for (const rec of numstat.split("\0")) {
    const m = rec.match(/^(\d+|-)\t(\d+|-)\t([\s\S]+)$/)
    if (!m || isNoise(m[3])) continue
    const p = patches.get(m[3]) || ""
    files.push({ file: m[3], patch: p, additions: m[1] === "-" ? 0 : Number(m[1]), deletions: m[2] === "-" ? 0 : Number(m[2]),
                 status: /^deleted file mode/m.test(p) ? "deleted" : /^new file mode/m.test(p) ? "added" : "modified" })
  }
  for (const f of untracked.split("\0")) {
    if (f && !isNoise(f)) files.push({ file: f, patch: "", additions: 0, deletions: 0, status: "added" })
  }
  return { files: files.slice(0, MAX_FILES), more: Math.max(0, files.length - MAX_FILES), repo: true }
}
