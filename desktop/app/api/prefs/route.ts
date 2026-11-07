// The app's own settings (recent projects, the open project, column widths, theme), kept in a file so they outlive a restart
// (owner, 2026-11-07: the projects were gone after the app reopened). The window's localStorage cannot hold them: the app's server
// takes a new port on every start, so every start is a new origin with empty storage. public/prefs-init.js loads this before the page
// reads anything and sends each change back here. Only kell's own keys, only short strings.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export const dynamic = "force-dynamic"

const KEY = /^kell[.:][\w.:-]{1,60}$/
const MAX = 20_000
const file = () => path.join(process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "desktop-prefs.json")

function read(): Record<string, string> {
  try {
    const j = JSON.parse(fs.readFileSync(file(), "utf8"))
    return j && typeof j === "object" ? Object.fromEntries(Object.entries(j).filter(([k, v]) => KEY.test(k) && typeof v === "string")) as Record<string, string> : {}
  } catch { return {} }
}

export async function GET() {
  return Response.json(read(), { headers: { "cache-control": "no-store" } })
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "JSON body" }, { status: 400 }) }
  const key = String(body?.key || "")
  if (!KEY.test(key)) return Response.json({ error: "not a kell setting" }, { status: 400 })
  const prefs = read()
  if (body.value === null || body.value === undefined) delete prefs[key]
  else if (typeof body.value === "string" && body.value.length <= MAX) prefs[key] = body.value
  else return Response.json({ error: "a short string, or null" }, { status: 400 })
  fs.mkdirSync(path.dirname(file()), { recursive: true })
  const tmp = `${file()}.tmp`
  fs.writeFileSync(tmp, JSON.stringify(prefs, null, 1))
  fs.renameSync(tmp, file())                                   // whole file or nothing: a crash mid-write keeps the old one
  return Response.json({ ok: true })
}
