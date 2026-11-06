// Team rooms inside the app (owner, 2026-11-04: "do the rooms wiring in; handle the accounts properly").
//
// The rooms are the website's own chat (<site>/chat), shown in a view inside the app window, so people, agents, the
// canvas and the encrypted sync are exactly the website's, with nothing re-implemented here. The page lays out a
// placeholder and reports its rectangle; the view sits on top of it and hides when the page shows the coding lane.
//
// One account for the whole app. The coding lane signs in with `kell login` (OAuth with PKCE, the token saved in
// ~/.stealthlab/config.json) in the person's default browser (Google refuses embedded windows); the rooms sign in with the
// website's own session (Supabase, in the site's localStorage, partition persist:kell-web). Both are the same Supabase
// account. Signing out clears both. If the two ever name different accounts (someone signed in as another person inside
// the rooms), the page says so and offers to sign out of both.
//
// Links: the site and the sign-in providers it uses stay in the view; a tool's sign-in link opens the app's tool sign-in
// window (electron/signin.ts); anything else goes to the system browser.
import { BrowserWindow, WebContentsView, session, shell } from "electron"
import { createServer, type Server, type ServerResponse } from "node:http"
import { createHash, randomBytes } from "node:crypto"
import { isSignInLink, openSignIn } from "./signin"

export const SITE = (process.env.KELL_SITE || "https://www.kell.org.in").replace(/\/$/, "")
const PARTITION = "persist:kell-web"
const plainUserAgent = (ua: string) => ua.replace(/\s(Electron|kell(-desktop)?)\/\S+/gi, "")

// Where the rooms view may go by itself: on kell's site only the chat and what signing in needs (owner, 2026-11-06: the rooms show
// the chat, never the rest of the website; anything else opens in the person's browser), plus kell's auth server and the sign-in
// providers the site offers.
const SITE_PATHS = /^\/(chat|sign-in|auth|oauth|onboarding|api)(\/|$)/
export function staysInView(raw: string): boolean {
  let u: URL
  try { u = new URL(raw) } catch { return false }
  if (u.protocol !== "https:") return false
  const h = u.hostname
  const site = new URL(SITE).hostname
  if (h === site || h === "kell.org.in" || h === "www.kell.org.in") return SITE_PATHS.test(u.pathname)
  return h.endsWith(".supabase.co") || h === "accounts.google.com" || h === "github.com"
}
// The site's own links back to the website (the logo, "Back to home"): hidden in the app. Navigation is blocked too (below), so this is
// only so nothing points somewhere it cannot go.
// With the logo row and the "Back to home" row gone, the sidebar's collapse button (absolutely placed at its top) sat on the Search
// box; the sidebar keeps that top row empty for it instead.
const HIDE_SITE_LINKS = `.back-btn, a[aria-label^="keळ, back to the site"], a[href="/"], .dg-side > .dg-brand, .dg-side > .dg-backrow { display: none !important; }
.dg-side { padding-top: 48px !important; }`

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
  // devTools off: nobody opens the inspector here to reach the rest of the site.
  view = new WebContentsView({ webPreferences: { partition: PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true, devTools: false } })
  const wc = view.webContents
  wc.setUserAgent(plainUserAgent(wc.getUserAgent()))   // Google refuses sign-in in a user agent that names Electron
  // A tool's sign-in started in the rooms opens the app's sign-in window; when it finishes, the rooms page hears it
  // (RoomView listens for kell:tool-signin-done) and runs the request again. Only the rooms: the coding page would otherwise
  // resume its own session for a sign-in it never asked for.
  const toolDone = (r: { url: string; ok: boolean }) => {
    if (r.ok && !wc.isDestroyed()) void wc.executeJavaScript(`window.dispatchEvent(new CustomEvent("kell:tool-signin-done"))`).catch(() => undefined)
  }
  // Tells the site it runs inside the app, so a sign-in window it could not open itself is not reported as a blocked pop-up.
  wc.on("dom-ready", () => {
    void wc.executeJavaScript("window.__kellDesktop = true").catch(() => undefined)
    void wc.insertCSS(HIDE_SITE_LINKS).catch(() => undefined)
  })
  // The site is a single-page app: its own links change the address without a navigation event, so will-navigate never sees them.
  // Any address outside the chat goes to the person's browser and the view goes back to the chat.
  wc.on("did-navigate-in-page", (_e, url) => {
    if (staysInView(url)) return
    if (/^https?:/.test(url)) void shell.openExternal(url)
    void wc.loadURL(`${SITE}/chat`)
  })
  wc.on("did-navigate", (_e, url) => { if (url.startsWith("http") && !staysInView(url)) void wc.loadURL(`${SITE}/chat`) })
  wc.on("devtools-opened", () => wc.closeDevTools())
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

/** Email and password: the site's own sign-in page (with its CAPTCHA) inside the rooms view. Only Google refuses an embedded
 *  window; email works there, and it lands back on the rooms. No token is copied and no Supabase setting is needed. */
export function openRoomsEmailSignIn() {
  if (view && !view.webContents.isDestroyed()) void view.webContents.loadURL(`${SITE}/sign-in?redirect=${encodeURIComponent("/chat")}`)
}

/** Keep the rooms in the app's light/dark choice (the site reads `kell:theme`, like the app). */
export function setRoomsTheme(theme: "light" | "dark") {
  if (!view || view.webContents.isDestroyed()) return
  void view.webContents.executeJavaScript(
    `try { localStorage.setItem("kell:theme", ${JSON.stringify(theme)}); document.documentElement.dataset.theme = ${JSON.stringify(theme)} } catch {}`,
  ).catch(() => undefined)
}

/** The website session's current access token, for the coding lane's model calls (2026-11-06): the model gateway takes only website
 *  sessions, and the coding sign-in is an MCP-app token it refuses on purpose. The rooms view owns this session and its Supabase
 *  client refreshes it, so the token is only ever read here, never refreshed here (two refreshers of one refresh token trip
 *  Supabase's reuse detection and revoke the session). An expired one: the view is reloaded, which makes the client refresh it on
 *  start. Null when the rooms are not signed in. The view is created (hidden) when needed, so the coding lane works before the
 *  Rooms tab was ever opened. */
export async function roomsAccessToken(win: BrowserWindow | undefined): Promise<string | null> {
  if (!win || win.isDestroyed()) return null
  const wc = ensureView(win).webContents
  const loaded = () => new Promise<void>((ok) => {
    if (!wc.isLoading() && wc.getURL().startsWith(SITE)) return ok()
    wc.once("did-finish-load", () => ok()); setTimeout(ok, 15_000)
  })
  const read = async (): Promise<{ token: string; exp: number } | null> => {
    await loaded()
    try {
      return await wc.executeJavaScript(`(() => {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i) || ""
          if (!/^sb-.*-auth-token$/.test(k)) continue
          try { const s = JSON.parse(localStorage.getItem(k) || "null"); const c = s && (s.access_token ? s : s.currentSession)
                if (c && typeof c.access_token === "string") return { token: c.access_token, exp: Number(c.expires_at || 0) } } catch {}
        }
        return null
      })()`)
    } catch { return null }
  }
  let s = await read()
  if (s && s.exp * 1000 - Date.now() < 60_000) {
    wc.reload()
    await new Promise((r) => setTimeout(r, 500))
    for (let i = 0; i < 10; i++) {                       // the client refreshes on start; give it a few seconds
      s = await read()
      if (!s || s.exp * 1000 - Date.now() >= 60_000) break
      await new Promise((r) => setTimeout(r, 700))
    }
  }
  return s && s.exp * 1000 - Date.now() > 0 ? s.token : null
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

/** The one sign-in: open `kell login`'s authorize link in the person's DEFAULT BROWSER (2026-11-06). Google refuses to sign in
 *  inside an embedded window, whatever user agent it sends ("This browser or app may not be secure"), so the link goes where
 *  Google accepts it. The CLI waits on a loopback address (http://127.0.0.1:<port>/callback), which any local browser can
 *  reach, and saves its token itself; the page polls /api/settings until that token is there. Resolves true once the browser
 *  was opened. The rooms view keeps its own website session (see the header): it is signed in inside the rooms, where
 *  GitHub works, or the rooms are opened in the browser. */
export function openAccountSignIn(_parent: BrowserWindow | undefined, url: string): Promise<boolean> {
  let u: URL
  try { u = new URL(url) } catch { return Promise.resolve(false) }
  if (u.protocol !== "https:") return Promise.resolve(false)
  return shell.openExternal(u.toString()).then(() => true, () => false)
}

/** Sign the rooms in with the app's OWN session, no token copied (2026-11-06; RFC 8252 + Supabase PKCE). The two tokens stay
 *  two (the coding lane's MCP token and the website session are rejected on each other's surface on purpose,
 *  authn.py::supabase_token_allowed), and neither is ever moved between programs. Here the app is its own OAuth client: it makes
 *  a PKCE verifier that never leaves this process, listens once on 127.0.0.1, sends the person's default browser to
 *  Supabase's authorize endpoint with the challenge, and the browser comes back to the loopback address carrying only a
 *  one-time code (valid 5 minutes, single use, useless without the verifier). The app swaps the code for a session of its own
 *  (its own refresh-token family, so the browser's session is untouched and nothing gets revoked) and puts it into the rooms
 *  view's storage. Needs the loopback address (http://127.0.0.1, any port, any path) in Supabase redirect allow list. The project URL and anon key are public
 *  by design and come from the site (/api/desktop-config). Resolves true once the rooms hold the session. */
export type RoomsProvider = "google" | "github"

export function signInRooms(win: BrowserWindow, provider: RoomsProvider): Promise<boolean> {
  if (provider !== "google" && provider !== "github") return Promise.resolve(false)
  const verifier = randomBytes(48).toString("base64url")
  const challenge = createHash("sha256").update(verifier).digest("base64url")
  const nonce = randomBytes(18).toString("base64url")
  return new Promise<boolean>(async (resolve) => {
    let settled = false
    let server: Server | undefined
    const finish = (ok: boolean) => { if (settled) return; settled = true; clearTimeout(timer); server?.close(); resolve(ok) }
    const timer = setTimeout(() => finish(false), 300_000)
    const page = (res: ServerResponse, status: number, text: string) => {
      res.writeHead(status, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "referrer-policy": "no-referrer" })
      res.end(`<!doctype html><meta charset="utf-8"><title>kell</title><body style="font-family:system-ui;max-width:32rem;margin:15vh auto;padding:0 1rem"><p>${text}</p>`)
    }
    let cfg: { url: string; anonKey: string }
    try {
      const r = await fetch(`${SITE}/api/desktop-config`, { signal: AbortSignal.timeout(10_000) })
      cfg = await r.json()
      const host = new URL(cfg.url).hostname
      if (!r.ok || new URL(cfg.url).protocol !== "https:" || !host.endsWith(".supabase.co") || !cfg.anonKey) throw new Error("bad config")
    } catch { finish(false); return }
    const ref = new URL(cfg.url).hostname.split(".")[0]
    server = createServer(async (req, res) => {
      const u = new URL(req.url || "/", "http://127.0.0.1")
      if (req.method !== "GET" || u.pathname !== `/callback/${nonce}`) { page(res, 404, "Not found."); return }
      const code = u.searchParams.get("code")
      if (!code || !/^[A-Za-z0-9._-]{8,200}$/.test(code)) { page(res, 400, "The sign-in did not finish. Go back to kell and try again."); finish(false); return }
      try {
        const r = await fetch(`${cfg.url}/auth/v1/token?grant_type=pkce`, {
          method: "POST", signal: AbortSignal.timeout(15_000),
          headers: { "content-type": "application/json", apikey: cfg.anonKey },
          body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
        })
        const s = await r.json()
        if (!r.ok || typeof s?.access_token !== "string" || typeof s?.refresh_token !== "string" || !s?.user?.id) throw new Error("exchange refused")
        if (!s.expires_at) s.expires_at = Math.floor(Date.now() / 1000) + Number(s.expires_in || 3600)
        const wc = ensureView(win).webContents
        if (wc.isLoading() || !wc.getURL().startsWith(SITE)) await new Promise<void>((ok) => { wc.once("did-finish-load", () => ok()); setTimeout(ok, 15_000) })
        await wc.executeJavaScript(`localStorage.setItem(${JSON.stringify(`sb-${ref}-auth-token`)}, ${JSON.stringify(JSON.stringify(s))})`)
        void wc.loadURL(`${SITE}/chat`)
        page(res, 200, "Signed in. You can close this tab and go back to kell.")
        finish(true)
      } catch {
        page(res, 500, "Could not finish signing in. Go back to kell and try again.")
        finish(false)
      }
    })
    server.on("error", () => finish(false))
    server.listen(0, "127.0.0.1", () => {
      const port = (server!.address() as { port: number }).port
      const authorize = new URL(`${cfg.url}/auth/v1/authorize`)
      authorize.searchParams.set("provider", provider)
      authorize.searchParams.set("redirect_to", `http://127.0.0.1:${port}/callback/${nonce}`)
      authorize.searchParams.set("code_challenge", challenge)
      authorize.searchParams.set("code_challenge_method", "s256")
      shell.openExternal(authorize.toString()).catch(() => finish(false))
    })
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
