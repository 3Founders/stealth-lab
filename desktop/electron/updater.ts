// Updates (owner, 2026-11-17: an installed app sat on 0.1.22 while 0.1.25 was out, with nothing to say why). The installed app asks
// the public releases-only repo (build.publish in package.json) for a newer installer at start and every few hours, downloads it
// quietly, and says so: a dialog offers "Restart now", and it also installs when the app closes. Every step and error goes to
// updater.log in the app's logs folder (%APPDATA%\kell\logs on Windows), so a stuck update can be read later. Before the first check,
// installers left in the updater's cache from versions this app already is (or is past), and half-downloaded temp files, are removed:
// an old leftover there was the likeliest thing holding updates back. No token is involved: the releases are public.
import { app, dialog, type BrowserWindow } from "electron"
import { autoUpdater } from "electron-updater"
import fs from "node:fs"
import path from "node:path"

const EVERY_MS = 4 * 60 * 60 * 1000
let started = false
let ready: string | null = null
let asked = false

function logFile() {
  try { return path.join(app.getPath("logs"), "updater.log") } catch { return path.join(app.getPath("userData"), "updater.log") }
}

export function ulog(...a: unknown[]) {
  const line = `${new Date().toISOString()} ${a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ")}\n`
  console.log("KELL_UPDATE", line.trim())
  try {
    const f = logFile()
    fs.mkdirSync(path.dirname(f), { recursive: true })
    if (fs.existsSync(f) && fs.statSync(f).size > 512 * 1024) fs.renameSync(f, f + ".old")
    fs.appendFileSync(f, line)
  } catch { /* logging never stops the app */ }
}

// "1.2.3" > "1.2.2"; anything unparseable counts as old.
function newer(a: string, b: string) {
  const p = (v: string) => v.split(/[.-]/).slice(0, 3).map((n) => parseInt(n, 10) || 0)
  const [x, y] = [p(a), p(b)]
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i]
  return false
}

/** Remove installers in the updater cache that are not newer than this app, and partial downloads. */
function clearStale() {
  const base = process.platform === "win32" ? process.env.LOCALAPPDATA : process.platform === "darwin" ? path.join(app.getPath("home"), "Library", "Caches") : (process.env.XDG_CACHE_HOME || path.join(app.getPath("home"), ".cache"))
  if (!base) return
  const pending = path.join(base, "kell-desktop-updater", "pending")
  let names: string[] = []
  try { names = fs.readdirSync(pending) } catch { return }
  const mine = app.getVersion()
  for (const n of names) {
    const v = n.match(/(\d+\.\d+\.\d+)/)?.[1]
    const stale = n.startsWith("temp-") || (v != null && !newer(v, mine))
    if (!stale) continue
    try { fs.rmSync(path.join(pending, n), { recursive: true, force: true }); ulog(`removed stale ${n}`) } catch (e) { ulog(`could not remove ${n}: ${String((e as Error)?.message || e)}`) }
  }
  // update-info.json describes the cached installer; without one it is meaningless and the updater rewrites it.
  try { if (!fs.readdirSync(pending).some((n) => /\.(exe|dmg|zip|AppImage)$/i.test(n))) fs.rmSync(path.join(pending, "update-info.json"), { force: true }) } catch { /* fine */ }
}

function offerRestart(win: () => BrowserWindow | undefined, version: string) {
  if (asked) return
  asked = true
  const w = win()
  const opts = { type: "info" as const, buttons: ["Restart now", "Later"], defaultId: 0, cancelId: 1, title: "Update ready",
    message: `kell ${version} is ready`, detail: "Restart to install it. If you choose Later, it installs the next time kell closes." }
  const p = w && !w.isDestroyed() ? dialog.showMessageBox(w, opts) : dialog.showMessageBox(opts)
  p.then((r) => { if (r.response === 0) { ulog(`restarting into ${version}`); setImmediate(() => autoUpdater.quitAndInstall(false, true)) } })
    .catch((e) => ulog(`restart prompt failed: ${String(e?.message || e)}`))
}

/** Check now (also from Help > Check for updates). `manual` says the outcome even when there is nothing new. */
export async function checkForUpdates(win: () => BrowserWindow | undefined, manual = false) {
  if (!started) {
    if (manual) void dialog.showMessageBox({ type: "info", message: "Updates are off in this build", detail: `kell ${app.getVersion()}` })
    return
  }
  if (ready) { asked = false; offerRestart(win, ready); return }
  try {
    const r = await autoUpdater.checkForUpdates()
    const v = r?.updateInfo?.version
    if (manual && (!v || !newer(v, app.getVersion()))) void dialog.showMessageBox({ type: "info", message: "kell is up to date", detail: `You have kell ${app.getVersion()}.` })
    else if (manual && v) void dialog.showMessageBox({ type: "info", message: `Downloading kell ${v}`, detail: "You'll be asked to restart when it's ready." })
  } catch (e) {
    const msg = String((e as Error)?.message || e).slice(0, 400)
    ulog(`check failed: ${msg}`)
    if (manual) void dialog.showMessageBox({ type: "warning", message: "Couldn't check for updates", detail: `${msg}\n\nThe log is at ${logFile()}` })
  }
}

export function startUpdates(win: () => BrowserWindow | undefined) {
  if (started) return
  started = true
  ulog(`kell ${app.getVersion()} starting update checks`)
  clearStale()
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.logger = { info: (m: unknown) => ulog("info", m), warn: (m: unknown) => ulog("warn", m), error: (m: unknown) => ulog("error", m), debug: () => {} }
  autoUpdater.on("checking-for-update", () => ulog("checking"))
  autoUpdater.on("update-available", (i) => ulog(`available ${i.version}`))
  autoUpdater.on("update-not-available", (i) => ulog(`up to date (latest ${i.version})`))
  let lastPct = -1
  autoUpdater.on("download-progress", (p) => { const pct = Math.floor(p.percent / 25) * 25; if (pct !== lastPct) { lastPct = pct; ulog(`downloading ${Math.round(p.percent)}%`) } })
  autoUpdater.on("error", (e) => ulog(`error: ${String(e?.stack || e?.message || e).slice(0, 800)}`))
  autoUpdater.on("update-downloaded", (i) => { ready = i.version; ulog(`downloaded ${i.version}; asking to restart`); offerRestart(win, i.version) })
  void checkForUpdates(win)
  setInterval(() => { if (!ready) void checkForUpdates(win) }, EVERY_MS).unref?.()
}
