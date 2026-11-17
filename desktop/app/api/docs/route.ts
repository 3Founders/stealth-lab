// GET /api/docs?since=<ms>&dir=<folder> -> { docs: [{ path, name, kind, at, size }] }: the documents kell-docs made (lib/docs.ts), for the
// cards beside the chat.
import { recentDocs } from "@/lib/docs"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const u = new URL(req.url)
  const since = Number(u.searchParams.get("since")) || 0
  const dir = u.searchParams.get("dir") || undefined
  return Response.json({ docs: recentDocs(since, dir) })
}
