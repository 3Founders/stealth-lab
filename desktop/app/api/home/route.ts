// GET /api/home -> { path, name }: the chats folder (lib/home.ts), made on first use, so kell can be asked anything without a project.
import { ensureHome, HOME_NAME } from "@/lib/home"

export const dynamic = "force-dynamic"

export async function GET() {
  try { return Response.json({ path: ensureHome(), name: HOME_NAME }) }
  catch (err: any) { return Response.json({ error: `could not make the chats folder: ${String(err?.message || err)}` }, { status: 500 }) }
}
