// GET /api/docs/preview?path=<file>[&v=<version id>][&raw=1] -> the document for the preview pane: HTML made by `kell-docs html` for
// .docx/.xlsx/.pptx/.csv/.md, or the file's own bytes for .pdf, .html and images (raw=1 always sends the bytes, for "Open"). Only
// document and image types are served, from a path the page already shows; the server listens on loopback only.
import { spawn } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { docsScript, versionPath } from "@/lib/docs"

export const dynamic = "force-dynamic"

const RAW: Record<string, string> = {
  ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
  ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".csv": "text/csv; charset=utf-8", ".md": "text/markdown; charset=utf-8",
}
const HTML_VIA_DOCS = /\.(docx|xlsx|pptx|csv|md)$/i

const run = (script: string, file: string) => new Promise<string>((ok, no) => {
  const child = spawn(process.execPath, [script, "html", file], { env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" }, windowsHide: true })
  let out = "", err = ""
  child.stdout.on("data", (c) => { out += c })
  child.stderr.on("data", (c) => { err += c })
  const t = setTimeout(() => { child.kill(); no(new Error("the preview took too long")) }, 30_000)
  child.on("close", (code) => { clearTimeout(t); code === 0 ? ok(out) : no(new Error(err.trim() || `kell-docs exited ${code}`)) })
})

const page = (msg: string) => new Response(`<!doctype html><meta charset="utf-8"><body style="font:14px system-ui;padding:24px;color:#555">${msg.replace(/</g, "&lt;")}</body>`,
  { headers: { "content-type": "text/html; charset=utf-8" } })

export async function GET(req: Request) {
  const u = new URL(req.url)
  const asked = u.searchParams.get("path") || ""
  const v = u.searchParams.get("v") || ""
  if (!asked) return page("No file.")
  const file = path.resolve(asked)
  const ext = path.extname(file).toLowerCase()
  if (!RAW[ext]) return page("kell can't preview this kind of file. Open it in its app instead.")
  const src = v ? versionPath(file, v) : file
  if (!src || !fs.existsSync(src)) return page("This file is no longer there.")
  const raw = u.searchParams.get("raw") === "1"
  if (raw || !HTML_VIA_DOCS.test(ext)) {
    const headers: Record<string, string> = { "content-type": RAW[ext], "cache-control": "no-store" }
    if (raw) headers["content-disposition"] = `attachment; filename="${encodeURIComponent(path.basename(file))}"`
    // HTML from a document is shown in a sandboxed frame by the page; it is never run in the app's own origin.
    if (ext === ".html" || ext === ".htm") headers["content-security-policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    return new Response(fs.readFileSync(src), { headers })
  }
  const script = docsScript()
  if (!script) return page("This build of kell has no document previewer.")
  try {
    const html = await run(script, src)
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'" } })
  } catch (e: any) { return page(`Couldn't preview ${path.basename(file)}: ${String(e?.message || e).slice(0, 300)}`) }
}
