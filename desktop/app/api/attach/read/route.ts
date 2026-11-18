// POST { paths } -> { files: [{ name, mime, size, dataUrl }] }: the bytes of the non-text files among them, as data: URLs.
// The composer already knows a dropped file's path; this is how a picture from outside the project reaches the model,
// because /api/prompt sends project text files as path references and everything else as bytes. Browser-uploaded files
// never come here: the page has their bytes and reads them itself (lib/attach.ts).
import fs from "node:fs/promises"
import path from "node:path"
import { NextResponse } from "next/server"

export const dynamic = "force-dynamic"

const MAX_FILES = 10
const MAX_BYTES = 25 * 1024 * 1024

const MIME: Record<string, string> = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
  ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon", ".pdf": "application/pdf",
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  const paths: string[] = Array.isArray(body?.paths) ? body.paths.filter((p: unknown) => typeof p === "string").slice(0, MAX_FILES) : []
  if (!paths.length) return NextResponse.json({ files: [] })
  const files: Array<{ name: string; mime: string; size: number; dataUrl: string }> = []
  for (const raw of paths) {
    try {
      const full = path.resolve(raw)
      const st = await fs.stat(full)
      if (!st.isFile()) continue
      if (st.size > MAX_BYTES) return NextResponse.json({ error: `${path.basename(full)} is larger than 25 MB` }, { status: 413 })
      const mime = MIME[path.extname(full).toLowerCase()] || "application/octet-stream"
      // Text files are not read here: the agent opens their path itself, and their bytes would only cost tokens.
      if (/^text\//.test(mime) || mime === "application/octet-stream") continue
      files.push({ name: path.basename(full), mime, size: st.size, dataUrl: `data:${mime};base64,${(await fs.readFile(full)).toString("base64")}` })
    } catch { /* a path that vanished is skipped */ }
  }
  return NextResponse.json({ files })
}
