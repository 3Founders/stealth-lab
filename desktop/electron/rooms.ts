// Team rooms inside the app (owner, 2026-11-04: "do the rooms wiring in; handle the accounts properly").
//
// The rooms are the website's own chat (<site>/chat), shown in a view inside the app window, so people, agents, the
// canvas and the encrypted sync are exactly the website's, with nothing re-implemented here. The page lays out a
// placeholder and reports its rectangle; the view sits on top of it and hides when the page shows the coding lane.
//
// One account for the whole app. The coding lane signs in with `kell login` (OAuth with PKCE, the token saved in
// ~/.stealthlab/config.json); the rooms sign in with the website's own session (Supabase, in the site's localStorage).
// Both are the same Supabase account, so the app runs `kell login --no-browser` and opens its authorize link here, in the
// rooms' own cookie store (partition persist:kell-web): the person signs in on kell's consent page once, which leaves the
// website session for the rooms AND hands the CLI its code. Signing out clears both. If the two ever name different
// accounts (someone signed in as another person inside the rooms), the page says so and offers to sign out of both.
//
// Links: the site and the sign-in providers it uses stay in the view; a tool's sign-in link opens the app's tool sign-in
// window (electron/signin.ts); anything else goes to the system browser.
import { BrowserWindow, WebContentsView, session, shell } from "electron"
import { isSignInLink, openSignIn } from "./signin"

export const SITE = (process.env.KELL_SITE || "https://www.kell.org.in").replace(/\/$/, "")
const PARTITION = "persist:kell-web"
const plainUserAgent = (ua: string) => ua.replace(/\s(Electron|kell(-desktop)?)\/\S+/gi, "")

// Where the rooms view may go by itself: kell's site, its auth server and the sign-in providers the site offers.
function staysInView(raw: string): boolean {
  let u: URL
  try { u = new URL(raw) } catch { return false }
  if (u.protocol !== "https:") return false
  const h = u.hostname
  const site = new URL(SITE).hostname
  return h === site || h === "kell.org.in" || h.endsWith(".kell.org.in") || h.endsWith(".supabase.co")
    || h === "accounts.google.com" || h === "github.com"
}

type Rect = { x: number; y: number; width: number; height: number }
let view: WebContentsView | undefined
let owner: BrowserWindow | undefined

function route(url: string, done: (r: { url: string; ok: boolean }) => void): "stay" | "away" {
  if (isSignInLink(url)) { openSignIn(owner, url, done); return "away" }
  if (staysInView(url)) return "stay"
  if (/^https?:/.test(url)) void shell.openExternal(url)
  return "away"
}

function ensureView(win: BrowserWindow): WebContentsView {
  if (view && !view.webContents.isDestroyed()) return view
  owner = win
  view = new WebContentsView({ webPreferences: { partition: PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true } })
  const wc = view.webContents
  wc.setUserAgent(plainUserAgent(wc.getUserAgent()))   // Google refuses sign-in in a user agent that names Electron
  // A tool's sign-in started in the rooms opens the app's sign-in window; when it finishes, the rooms page hears it
  // (RoomView listens for kell:tool-signin-done) and runs the request again. Only the rooms: the coding page would otherwise
  // resume its own session for a sign-in it never asked for.
  const toolDone = (r: { url: string; ok: boolean }) => {
    if (r.ok && !wc.isDestroyed()) void wc.executeJavaScript(`window.dispatchEvent(new CustomEvent("kell:tool-signin-done"))`).catch(() => undefined)
  }
  // Tells the site it runs inside the app, so a sign-in window it could not open itself is not reported as a blocked pop-up.
  wc.on("dom-ready", () => { void wc.executeJavaScript("window.__kellDesktop = true").catch(() => undefined) })
  wc.setWindowOpenHandler(({ url }) => {
    if (route(url, toolDone) === "stay") void wc.loadURL(url)
    return { action: "deny" }
  })
  wc.on("will-navigate", (e, url) => { if (route(url, toolDone) === "away") e.preventDefault() })
  view.setVisible(false)
  win.contentView.addChildView(view)
  void wc.loadURL(`${SITE}/chat`)
  return view
}

/** Show the rooms over the page's placeholder (rect in CSS pixels of the window), or hide them (null). */
export function setRoomsBounds(win: BrowserWindow, rect: Rect | null) {
  if (!rect) { view?.setVisible(false); return }
  const v = ensureView(win)
  v.setBounds({ x: Math.round(rect.x), y: Math.round(rect.y), width: Math.max(0, Math.round(rect.width)), height: Math.max(0, Math.round(rect.height)) })
  v.setVisible(true)
}

export function reloadRooms() {
  if (view && !view.webContents.isDestroyed()) void view.webContents.loadURL(`${SITE}/chat`)
}

/** Keep the rooms in the app's light/dark choice (the site reads `kell:theme`, like the app). */
export function setRoomsTheme(theme: "light" | "dark") {
  if (!view || view.webContents.isDestroyed()) return
  void view.webContents.executeJavaScript(
    `try { localStorage.setItem("kell:theme", ${JSON.stringify(theme)}); document.documentElement.dataset.theme = ${JSON.stringify(theme)} } catch {}`,
  ).catch(() => undefined)
}

/** Who the rooms are signed in as: the website session's user id and email, or null. Read for display and for the
 *  same-account check only; the rooms API verifies the session itself. */
export async function roomsAccount(): Promise<{ id: string; email: string | null } | null | undefined> {
  if (!view || view.webContents.isDestroyed()) return undefined
  // Not known yet while the site is still loading (a blank page has no session to read): undefined, not "signed out".
  if (view.webContents.isLoading() || !view.webContents.getURL().startsWith(SITE)) return undefined
  try {
    return await view.webContents.executeJavaScript(`(() => {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i) || ""
        if (!/^sb-.*-auth-token$/.test(k)) continue
        try { const s = JSON.parse(localStorage.getItem(k) || "null"); const u = s && (s.user || (s.currentSession && s.currentSession.user))
              if (u && u.id) return { id: String(u.id), email: u.email ? String(u.email) : null } } catch {}
      }
      return null
    })()`)
  } catch {
    return undefined
  }
}

/** The one sign-in: open `kell login`'s authorize link in the rooms' cookie store. Resolves true once the flow reaches the
 *  CLI's loopback callback (the CLI then saves its token), false if the window is closed first. */
export function openAccountSignIn(parent: BrowserWindow | undefined, url: string): Promise<boolean> {
  return new Promise((resolve) => {
    let u: URL
    try { u = new URL(url) } catch { resolve(false); return }
    if (u.protocol !== "https:") { resolve(false); return }
    const w = new BrowserWindow({
      width: 520, height: 760, parent, title: "Sign in to kell", backgroundColor: "#F8F6EF", autoHideMenuBar: true,
      webPreferences: { partition: PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true },
    })
    w.webContents.setUserAgent(plainUserAgent(w.webContents.getUserAgent()))
    let finished = false
    const atCallback = (to: string) => /^http:\/\/127\.0\.0\.1:\d+\/callback/.test(to)
    const finish = (ok: boolean) => {
      if (finished) return
      finished = true
      resolve(ok)
      setTimeout(() => { if (!w.isDestroyed()) w.close() }, 1200)
    }
    w.webContents.on("did-navigate", (_e, to) => { if (atCallback(to)) finish(true) })
    w.webContents.on("did-redirect-navigation", (_e, to) => { if (atCallback(to)) setTimeout(() => finish(true), 1500) })
    w.webContents.setWindowOpenHandler(({ url: next }) => { void w.loadURL(next); return { action: "deny" } })
    w.on("closed", () => { if (!finished) { finished = true; resolve(false) } })
    void w.loadURL(url)
  })
}

/** Sign the rooms out: the site's session and its local copies (cached chat keys included) go, so a next account never
 *  sees the last one's data. The provider's own cookies (Google, GitHub) stay, like in a browser. */
export async function signOutRooms() {
  const s = session.fromPartition(PARTITION)
  for (const origin of new Set([SITE, "https://kell.org.in", "https://www.kell.org.in"])) {
    await s.clearStorageData({ origin, storages: ["localstorage", "indexdb", "cookies", "serviceworkers", "cachestorage"] }).catch(() => undefined)
  }
  reloadRooms()
}
