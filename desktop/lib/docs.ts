// Documents (docs/desktop_requirements_2026-11.md, P0 #2 and P1 #4/#5, 2026-11-17): the engine makes real .docx/.xlsx/.pptx/.pdf files
// with the bundled kell-docs (scripts/kell-docs), the app shows a card for each beside the chat, previews it, keeps its versions and
// can save a copy. This file is the server side: the `kell-docs` command on the engine's PATH, the rule that tells the model about
// it, the log of files made, and the version folders.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const home = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
export const DOC_EXT = /\.(docx|xlsx|pptx|pdf|csv|md|html?)$/i

/** The bundled kell-docs script ("" when this build has none). */
export function docsScript(): string {
  const cwd = process.env.KELL_ROOT || process.cwd()
  const c = [
    process.env.KELL_DOCS || "",
    path.join(cwd.replace(/app\.asar$/, "app.asar.unpacked"), "vendor", "kell-docs", "kell-docs.mjs"),
    path.join(cwd, "vendor", "kell-docs", "kell-docs.mjs"),
  ].filter(Boolean)
  return c.find((p) => { try { return fs.statSync(p).isFile() } catch { return false } }) || ""
}

/** Writes `kell-docs` (cmd and sh) into ~/.stealthlab/desktop/bin and returns that folder ("" without the script). The command runs
 *  the script with this app's own runtime (Electron in node mode), so nothing has to be installed. */
export function prepareDocsBin(): string {
  const script = docsScript()
  if (!script) return ""
  const bin = path.join(home(), "desktop", "bin")
  fs.mkdirSync(bin, { recursive: true })
  const exe = process.execPath
  const write = (f: string, text: string) => { try { if (fs.readFileSync(f, "utf8") === text) return } catch { /* new */ } fs.writeFileSync(f, text, { mode: 0o755 }) }
  write(path.join(bin, "kell-docs.cmd"), `@echo off\r\nset ELECTRON_RUN_AS_NODE=1\r\n"${exe}" "${script}" %*\r\n`)
  const sh = (p: string) => p.replace(/\\/g, "/").replace(/^([A-Za-z]):/, (_m, d) => `/${d.toLowerCase()}`)
  write(path.join(bin, "kell-docs"), `#!/bin/sh\nELECTRON_RUN_AS_NODE=1 exec "${sh(exe)}" "${sh(script)}" "$@"\n`)
  return bin
}

/** The engine's environment with the docs folder first on PATH (Windows spells it Path). */
export function withDocsPath(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  let bin = ""
  try { bin = prepareDocsBin() } catch { return env }
  if (!bin) return env
  const key = Object.keys(env).find((k) => k.toLowerCase() === "path") || "PATH"
  return { ...env, [key]: `${bin}${path.delimiter}${env[key] || ""}`, KELL_DOCS_BIN: bin }
}

export const DOC_RULES = [
  "Documents: when the person asks for a Word document, spreadsheet, slide deck or PDF (a report, letter, CV, budget, invoice, "
  + "deck, table), make a real file with the `kell-docs` command, not a text file and not a script of your own. Write a JSON spec "
  + "to a temporary file and run `kell-docs make spec.json`, then delete the spec. Run `kell-docs spec` once to see the format: "
  + "\"markdown\" for .docx/.pdf/.html, \"sheets\" (columns, rows, cells starting with = are formulas, \"totals\": true) for "
  + ".xlsx/.csv, \"slides\" (title, bullets, notes, table, chart) for .pptx. Charts: a slide's \"chart\" is a native, editable "
  + "chart; in markdown a ```chart block with the same JSON. In a reply, a ```chart block {\"type\":\"bar|line|pie\",\"title\","
  + "\"labels\":[...],\"series\":[{\"name\",\"values\":[...]}]} is drawn as a chart in the chat. Name files for what they are. "
  + "To change a document, make it again at the same path (the old one is kept as a version). To read an attached .docx, .xlsx "
  + "or .pptx, run `kell-docs read <file>`. The app shows each file you make beside the chat; just say what you made.",
  "Maths in replies: $...$ inline and $$...$$ on its own lines are typeset. Diagrams: a ```mermaid block is drawn.",
].join("\n\n")

type Logged = { path: string; kind: string; at: number; cwd?: string }

/** Files kell-docs made since `since` (ms), still on disk; inside `dir` when given. Newest last, one row per path. */
export function recentDocs(since: number, dir?: string): { path: string; name: string; kind: string; at: number; size: number }[] {
  let lines: string[] = []
  try { lines = fs.readFileSync(path.join(home(), "desktop", "docs.jsonl"), "utf8").split("\n").slice(-2000) } catch { return [] }
  const norm = (p: string) => path.resolve(p).toLowerCase()
  const inDir = dir ? norm(dir) : ""
  const seen = new Map<string, Logged>()
  for (const l of lines) {
    try {
      const r = JSON.parse(l) as Logged
      if (!r?.path || r.at < since) continue
      if (inDir && !norm(r.path).startsWith(inDir) && !(r.cwd && norm(r.cwd).startsWith(inDir))) continue
      seen.set(norm(r.path), r)
    } catch { /* a torn line */ }
  }
  const out = []
  for (const r of seen.values()) {
    try { const st = fs.statSync(r.path); out.push({ path: r.path, name: path.basename(r.path), kind: r.kind, at: r.at, size: st.size }) } catch { /* deleted since */ }
  }
  return out.sort((a, b) => a.at - b.at)
}

const versionsDir = (file: string) => path.join(path.dirname(file), ".kell-versions", path.basename(file))

/** Earlier versions of a document kell-docs replaced, newest first. */
export function versionsOf(file: string): { id: string; at: number; size: number }[] {
  try {
    return fs.readdirSync(versionsDir(file)).map((id) => {
      const st = fs.statSync(path.join(versionsDir(file), id))
      return { id, at: st.mtimeMs, size: st.size }
    }).sort((a, b) => b.at - a.at)
  } catch { return [] }
}

/** The path of one kept version, or "" when the id is not one of them (no path tricks). */
export function versionPath(file: string, id: string): string {
  if (!/^[\w.-]+$/.test(id)) return ""
  const p = path.join(versionsDir(file), id)
  return fs.existsSync(p) ? p : ""
}

/** Puts a kept version back; the current file is kept as a version first, so a restore can be undone too. */
export function restoreVersion(file: string, id: string): boolean {
  const v = versionPath(file, id)
  if (!v) return false
  if (fs.existsSync(file)) {
    const stamp = new Date(fs.statSync(file).mtimeMs).toISOString().replace(/[:.]/g, "-")
    fs.mkdirSync(versionsDir(file), { recursive: true })
    fs.copyFileSync(file, path.join(versionsDir(file), stamp + path.extname(file)))
  }
  fs.copyFileSync(v, file)
  try { fs.appendFileSync(path.join(home(), "desktop", "docs.jsonl"), JSON.stringify({ path: file, kind: path.extname(file).slice(1), at: Date.now(), restored: id }) + "\n") } catch { /* fine */ }
  return true
}
