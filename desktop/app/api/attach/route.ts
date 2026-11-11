// Metadata for files picked or dropped into the composer. This route never reads the bytes: it stats the path and
// returns the name, size, type and (when the file is inside the open project) its relative path. That lets the
// composer show a chip and lets /api/prompt decide whether the cheapest thing is a path reference or an attachment.
import fs from "node:fs/promises"
import path from "node:path"
import { NextResponse } from "next/server"

export const dynamic = "force-dynamic"

const MAX_FILES = 10
const MAX_BYTES = 25 * 1024 * 1024

const MIME: Record<string, string> = {
  ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".json": "application/json",
  ".jsonl": "application/x-ndjson", ".yaml": "application/yaml", ".yml": "application/yaml", ".toml": "application/toml",
  ".ini": "text/plain", ".cfg": "text/plain", ".conf": "text/plain", ".env": "text/plain", ".csv": "text/csv",
  ".tsv": "text/tab-separated-values", ".xml": "application/xml", ".html": "text/html", ".htm": "text/html",
  ".css": "text/css", ".scss": "text/x-scss", ".less": "text/x-less", ".js": "text/javascript", ".mjs": "text/javascript",
  ".cjs": "text/javascript", ".jsx": "text/javascript", ".ts": "text/typescript", ".tsx": "text/typescript",
  ".py": "text/x-python", ".rb": "text/x-ruby", ".go": "text/x-go", ".rs": "text/x-rust", ".java": "text/x-java",
  ".kt": "text/x-kotlin", ".swift": "text/x-swift", ".c": "text/x-c", ".h": "text/x-c", ".cpp": "text/x-c++",
  ".hpp": "text/x-c++", ".cs": "text/x-csharp", ".php": "text/x-php", ".sh": "text/x-sh", ".bash": "text/x-sh",
  ".zsh": "text/x-sh", ".ps1": "text/x-powershell", ".sql": "text/x-sql", ".graphql": "application/graphql",
  ".vue": "text/x-vue", ".svelte": "text/x-svelte", ".astro": "text/x-astro", ".lua": "text/x-lua", ".r": "text/x-r",
  ".scala": "text/x-scala", ".clj": "text/x-clojure", ".ex": "text/x-elixir", ".exs": "text/x-elixir",
  ".dart": "text/x-dart", ".tf": "text/x-hcl", ".hcl": "text/x-hcl", ".dockerfile": "text/plain",
  ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
  ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon",
}

const mimeOf = (name: string) => MIME[path.extname(name).toLowerCase()] || "application/octet-stream"

/** The file's path relative to `root` (forward slashes), or undefined when it is outside. */
function relTo(root: string, full: string): string | undefined {
  const back = path.relative(root, full)
  if (!back || back.startsWith("..") || path.isAbsolute(back)) return undefined
  return back.split(path.sep).join("/")
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  const root = typeof body?.directory === "string" ? path.resolve(body.directory) : ""
  const paths: string[] = Array.isArray(body?.paths) ? body.paths.filter((p: unknown) => typeof p === "string").slice(0, MAX_FILES) : []
  if (!root) return NextResponse.json({ error: "Open a project first" }, { status: 400 })
  if (!paths.length) return NextResponse.json({ files: [] })
  const files = []
  for (const raw of paths) {
    try {
      const full = path.resolve(raw)
      const st = await fs.stat(full)
      if (!st.isFile()) continue
      if (st.size > MAX_BYTES) {
        return NextResponse.json({ error: `${path.basename(full)} is larger than 25 MB` }, { status: 413 })
      }
      files.push({ path: full, name: path.basename(full), size: st.size, mime: mimeOf(full), ...(relTo(root, full) ? { rel: relTo(root, full) } : {}) })
    } catch { /* a path that vanished is skipped */ }
  }
  return NextResponse.json({ files })
}
