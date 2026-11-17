// POST { id, pinned?, archived?, folder? } -> { ok, filing }: pin a chat, archive it, or put it in a folder (lib/localdb.ts fileSession).
import { fileSession } from "@/lib/localdb"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const b = await req.json().catch(() => ({} as Record<string, unknown>))
  const id = String(b.id || "")
  if (!id) return Response.json({ error: "send the chat's id" }, { status: 400 })
  const patch: { pinned?: boolean; archived?: boolean; folder?: string | null } = {}
  if (typeof b.pinned === "boolean") patch.pinned = b.pinned
  if (typeof b.archived === "boolean") patch.archived = b.archived
  if (b.folder === null || typeof b.folder === "string") patch.folder = (b.folder as string | null) || null
  const filing = fileSession(id, patch)
  return filing ? Response.json({ ok: true, filing }) : Response.json({ error: "that chat is not in the app's store" }, { status: 404 })
}
