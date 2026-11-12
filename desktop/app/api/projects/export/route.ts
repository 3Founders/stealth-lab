// GET /api/projects/export?path=<abs>[&format=md|summary]: a project's chats from the app's own store, for the ⋮ menu.
//   md       every chat of that folder as one Markdown file (download)
//   summary  a short text block for the clipboard ("Share")
// The files on disk are never touched; this only reads localdb.
import path from "node:path"
import { listSessions } from "@/lib/localdb"
import { localLines } from "@/lib/history"

export const dynamic = "force-dynamic"

const norm = (p: string) => path.resolve(p).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
const nameOf = (p: string) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams
  const dir = String(q.get("path") || "")
  if (!dir) return Response.json({ error: "give a path" }, { status: 400 })
  const name = nameOf(dir)
  let sessions
  try {
    sessions = listSessions(500).filter((s) => s.directory && norm(s.directory) === norm(dir))
  } catch (err: any) {
    return Response.json({ error: String(err?.message || err) }, { status: 500 })
  }
  if ((q.get("format") || "md") === "summary") {
    const latest = sessions[0]
    const lines = [
      `**${name}**`,
      dir,
      `${sessions.length} chat${sessions.length === 1 ? "" : "s"}${latest?.title ? ` · latest: ${latest.title}` : ""}`,
    ]
    return new Response(lines.join("\n"), { headers: { "content-type": "text/plain; charset=utf-8" } })
  }
  const md: string[] = [`# ${name}`, "", `Folder: \`${dir}\``, `Chats: ${sessions.length}`, ""]
  for (const s of sessions) {
    md.push(`## ${s.title || "Untitled chat"}`, "", `_${new Date(s.updated_at).toISOString()}_`, "")
    // The local copy, or the engine's once when chats.db has no messages for it yet (a chat imported before the local
    // store existed): an export must not be a list of empty headings.
    const lines = await localLines(s.id, s.directory, s.title)
    for (const m of lines) {
      md.push(`**${m.who === "user" ? "You" : "kell"}:**`, "", m.text.trim(), "")
    }
  }
  const safe = name.replace(/[^A-Za-z0-9._-]+/g, "-") || "project"
  return new Response(md.join("\n"), {
    headers: {
      "content-type": "text/markdown; charset=utf-8",
      "content-disposition": `attachment; filename="${safe}-chats.md"`,
    },
  })
}
