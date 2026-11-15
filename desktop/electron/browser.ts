// The app's browser (owner, 2026-11-15: "basic things like in built browser", as in Claude Code).
//
// A plain web view in the bottom panel, mostly for the project's own dev server (localhost) beside the chat. Its own
// partition (persist:kell-browser): its cookies and sign-ins are not the rooms' and not the app's. No preload, no node,
// sandboxed: a page shown here gets nothing of the app. Only http and https; a link that opens a new window opens here.
// Like the rooms view, it sits over a placeholder the page lays out and hides when the page says so.
import { BrowserWindow, WebContentsView } from "electron"

type Rect = { x: number; y: number; width: number; height: number }
const PARTITION = "persist:kell-browser"
let view: WebContentsView | undefined
let owner: BrowserWindow | undefined

const plainUserAgent = (ua: string) => ua.replace(/\s(Electron|kell(-desktop)?)\/\S+/gi, "")

/** What the person typed, as an address: a full URL as is; localhost or a bare host and port over http; a domain over
 *  https; anything else is a search. */
export function toAddress(raw: string): string | null {
  const s = String(raw || "").trim()
  if (!s) return null
  if (/^https?:\/\//i.test(s)) { try { return new URL(s).toString() } catch { return null } }
  if (/^[a-z][a-z0-9+.-]*:(?!\d)/i.test(s)) return null   // other schemes (file:, javascript:, …): no; "host:port" is not one
  if (/^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|0\.0\.0\.0)(:\d+)?(\/|$)/i.test(s) || /^\d+\.\d+\.\d+\.\d+(:\d+)?(\/|$)/.test(s)) return `http://${s}`
  if (/^:?\d{2,5}$/.test(s)) return `http://localhost:${s.replace(/^:/, "")}`
  if (!/\s/.test(s) && /^[^/]+\.[a-z]{2,}(:\d+)?(\/.*)?$/i.test(s)) return `https://${s}`
  return `https://duckduckgo.com/?q=${encodeURIComponent(s)}`
}

function state() {
  const wc = view && !view.webContents.isDestroyed() ? view.webContents : undefined
  return {
    url: wc?.getURL() || "",
    title: wc?.getTitle() || "",
    loading: !!wc?.isLoading(),
    back: !!wc?.navigationHistory.canGoBack(),
    forward: !!wc?.navigationHistory.canGoForward(),
  }
}
function report() { if (owner && !owner.isDestroyed()) owner.webContents.send("kell:browser-state", state()) }

function ensure(win: BrowserWindow): WebContentsView {
  if (view && !view.webContents.isDestroyed()) return view
  owner = win
  view = new WebContentsView({ webPreferences: { partition: PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true } })
  const wc = view.webContents
  wc.setUserAgent(plainUserAgent(wc.getUserAgent()))
  wc.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) void wc.loadURL(url)
    return { action: "deny" }
  })
  wc.on("will-navigate", (e, url) => { if (!/^https?:/i.test(url)) e.preventDefault() })
  for (const ev of ["did-navigate", "did-navigate-in-page", "page-title-updated", "did-start-loading", "did-stop-loading", "did-fail-load"] as const) {
    wc.on(ev as any, () => report())
  }
  view.setVisible(false)
  win.contentView.addChildView(view)
  return view
}

export function browserBounds(win: BrowserWindow, rect: Rect | null) {
  if (!rect) { view?.setVisible(false); return }
  const v = ensure(win)
  v.setBounds({ x: Math.round(rect.x), y: Math.round(rect.y), width: Math.max(0, Math.round(rect.width)), height: Math.max(0, Math.round(rect.height)) })
  v.setVisible(true)
}

export function browserGo(win: BrowserWindow, raw: string): string | null {
  const url = toAddress(raw)
  if (!url) return null
  void ensure(win).webContents.loadURL(url).catch(() => undefined)   // a failed load shows the browser's own error page
  return url
}

export function browserAct(what: string) {
  const wc = view && !view.webContents.isDestroyed() ? view.webContents : undefined
  if (!wc) return
  if (what === "back" && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack()
  else if (what === "forward" && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward()
  else if (what === "reload") wc.reload()
  else if (what === "stop") wc.stop()
}

export function browserState() { return state() }
