// Tool sign-in inside the app (owner, 2026-11-04: "one click, sign in with Google or GitHub or whatever the tool uses").
//
// kell's gateway answers a tool that needs a sign-in with a link (backend/app/mcp_server/server.py _gateway_sign_in):
// either the tool provider's own OAuth page, whose redirect_uri is <kell>/tools/connect/callback, or kell's consent page
// <kell>/tools/consent/<token>. This opens that link in a small window of the app's own, waits for the flow to land back
// on kell, and closes the window by itself, so the person only signs in. Everything else (an ordinary link in a message)
// goes to the system browser.
//
// The window keeps its own cookie store (partition persist:kell-signin), so a Google or GitHub sign-in done once is
// remembered for the next tool. Google refuses sign-in inside an app whose user agent names Electron, so the window uses
// the plain Chrome user agent Electron is built on.
import { BrowserWindow, shell } from "electron"

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

const plainUserAgent = (ua: string) => ua.replace(/\s(Electron|kell(-desktop)?)\/\S+/gi, "")

export function openSignIn(parent: BrowserWindow | undefined, url: string, done: (r: { url: string; ok: boolean }) => void) {
  if (!isSignInLink(url)) { void shell.openExternal(url); return }
  const w = new BrowserWindow({
    width: 520, height: 720, parent, title: "Sign in", backgroundColor: "#F8F6EF", autoHideMenuBar: true,
    webPreferences: { partition: "persist:kell-signin", contextIsolation: true, nodeIntegration: false, sandbox: true },
  })
  w.webContents.setUserAgent(plainUserAgent(w.webContents.getUserAgent()))
  let finished = false
  let consentLoads = 0
  const finish = (ok: boolean, at: string) => {
    if (finished) return
    finished = true
    done({ url: at, ok })
    setTimeout(() => { if (!w.isDestroyed()) w.close() }, 1200)   // long enough to read kell's "Signed in" page
  }
  w.webContents.on("did-finish-load", () => {
    const at = w.webContents.getURL()
    let path = ""
    try { path = new URL(at).pathname } catch { return }
    if (path.startsWith(CALLBACK)) finish(!/error|failed|refused/i.test(w.webContents.getTitle()), at)
    else if (path.startsWith(CONSENT) && ++consentLoads >= 2) finish(true, at)   // first load: the question; second: the answer
  })
  // A popup from the provider (some use one for account choice) stays in this window's session.
  w.webContents.setWindowOpenHandler(({ url: next }) => { void w.loadURL(next); return { action: "deny" } })
  w.on("closed", () => { if (!finished) { finished = true; done({ url, ok: false }) } })
  void w.loadURL(url)
}
