// GET  /api/last   -> { last: { directory, sessionID } | null }
// POST /api/last { directory?, sessionID? }  remembers what to reopen
//
// The page's localStorage is wiped on every start (electron/memory.ts: the origin changes each run, and the app used to
// come back into old chats). That made the app open on nothing, which read as "my chats are gone" even though they were
// in chats.db all along. The folder and chat to reopen are not transient: keep them in the app's own store.
import { getMeta, setMeta } from "@/lib/localdb"

export const dynamic = "force-dynamic"

type Last = { directory: string | null; sessionID: string | null }

const read = (): Last | null => {
  try {
    const v = JSON.parse(getMeta("last") || "null")
    if (!v || typeof v !== "object") return null
    return {
      directory: typeof v.directory === "string" && v.directory ? v.directory : null,
      sessionID: typeof v.sessionID === "string" && v.sessionID ? v.sessionID : null,
    }
  } catch { return null }
}

export async function GET() {
  try { return Response.json({ last: read() }) }
  catch (err: any) { return Response.json({ last: null, error: String(err?.message || err) }, { status: 500 }) }
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "JSON body" }, { status: 400 }) }
  const prev = read() || { directory: null, sessionID: null }
  const next: Last = {
    directory: typeof body?.directory === "string" && body.directory ? body.directory : prev.directory,
    // `null` is meaningful here: a new chat clears the remembered session but keeps the folder.
    sessionID: body?.sessionID === null ? null
      : typeof body?.sessionID === "string" && body.sessionID ? body.sessionID : prev.sessionID,
  }
  try { setMeta("last", JSON.stringify(next)); return Response.json({ last: next }) }
  catch (err: any) { return Response.json({ error: String(err?.message || err) }, { status: 500 }) }
}
