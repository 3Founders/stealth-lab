// Whether one tool's sign-in has actually finished: POST { server, link? } -> { signedIn }. The sign-in opens in the
// person's default browser (electron/signin.ts), so no callback comes back to the app; the page asks this while the card
// says "open" and whenever the app regains focus (lib/tools.ts toolSignInStatus). A closed browser is never a sign-in.
import { NextResponse } from "next/server"
import { toolSignInStatus } from "@/lib/tools"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  let server = ""
  let link = ""
  try {
    const body = await req.json()
    server = String(body.server || "").slice(0, 200)
    link = String(body.link || "").slice(0, 2000)
  } catch { /* empty */ }
  if (!server.trim()) return NextResponse.json({ signedIn: false }, { status: 400 })
  try {
    return NextResponse.json({ signedIn: await toolSignInStatus(server, link) })
  } catch (err: any) {
    return NextResponse.json({ signedIn: false, error: String(err?.message || err) }, { status: 502 })
  }
}
