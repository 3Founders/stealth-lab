// Tool sign-in in the person's own browser (owner, 2026-11-09: it should open in the default browser, not a window of ours).
//
// kell's gateway answers a tool that needs a sign-in with a link (backend/app/mcp_server/server.py _gateway_sign_in):
// either the tool provider's own OAuth page, whose redirect_uri is <kell>/tools/connect/callback, or kell's consent page
// <kell>/tools/consent/<token>. Opening it in the system browser is what the providers accept (Google refuses an embedded
// window, whatever user agent it is given), and it is where the person already is signed in to their accounts.
//
// No callback comes back to this process: the browser finishes the flow with kell's server, which seals the token or records
// the consent. The page then asks kell whether it finished (lib/tools.ts toolSignInStatus, /api/tools/signin) and carries on
// by itself; a closed browser is never taken as a sign-in.
import { shell } from "electron"

const CALLBACK = "/tools/connect/callback"
const CONSENT = "/tools/consent/"

/** Is this link a tool sign-in? A consent page on any https origin, or an OAuth authorize URL that returns to kell. */
export function isSignInLink(raw: string): boolean {
  let u: URL
  try { u = new URL(raw) } catch { return false }
  if (u.protocol !== "https:") return false
  if (u.pathname.startsWith(CONSENT)) return true
  const back = u.searchParams.get("redirect_uri") || ""
  return back.startsWith("https://") && back.includes(CALLBACK)
}

/** Open a link in the person's default browser. Resolves false only when it could not be opened. */
export function openSignIn(url: string): Promise<boolean> {
  let u: URL
  try { u = new URL(url) } catch { return Promise.resolve(false) }
  if (u.protocol !== "https:") return Promise.resolve(false)
  return shell.openExternal(u.toString()).then(() => true, () => false)
}
