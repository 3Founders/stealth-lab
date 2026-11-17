// Connectors (lib/connectors.ts).
//   GET                                                   -> { catalog, connectors: [{ id, name, url, status, error }] }
//   POST { action: "add", id } | { action: "add", name, url }  -> { connector }
//   POST { action: "remove", id }
//   POST { action: "signin", id }                         -> { url, attemptID, integrationID }   (the page opens url in the browser)
//   POST { action: "status", integrationID, attemptID }   -> { status }
import { addConnector, CATALOG, removeConnector, signIn, signInStatus, statuses } from "@/lib/connectors"

export const dynamic = "force-dynamic"

export async function GET() {
  try { return Response.json({ catalog: CATALOG, connectors: await statuses() }) }
  catch (err: any) { return Response.json({ catalog: CATALOG, connectors: [], error: String(err?.message || err) }) }
}

export async function POST(req: Request) {
  const b = await req.json().catch(() => ({} as any))
  try {
    switch (b.action) {
      case "add": return Response.json({ connector: await addConnector(String(b.id || ""), b.url ? { name: String(b.name || ""), url: String(b.url) } : undefined) })
      case "remove": await removeConnector(String(b.id || "")); return Response.json({ ok: true })
      case "signin": return Response.json(await signIn(String(b.id || "")))
      case "status": return Response.json(await signInStatus(String(b.integrationID || ""), String(b.attemptID || "")))
      default: return Response.json({ error: "action: add, remove, signin or status" }, { status: 400 })
    }
  } catch (err: any) { return Response.json({ error: String(err?.message || err).slice(0, 300) }, { status: 400 }) }
}
