// kell desktop's Electron shell (docs/desktop_app_plan.md; the shell is Electron by owner decision 2026-11-01).
//
// One window on the Next UI. Next runs as a child process (`next start` on a production build), not in-process: in the
// Electron main, next's programmatic prepare() hangs (its render-server fork resolves to electron.exe instead of node).
// The child gets ELECTRON_RUN_AS_NODE=1 so it runs as plain node, listens on an OS-picked port (port 0 probed, never a
// fixed one â€” the OpenCode desktop app owns 28894 and is never touched), and its whole tree â€” including the opencode
// engine it starts through lib/engine.ts's /api routes â€” dies with the app.
import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron"
import { markQuitting, startPresence } from "./presence"
import { isSignInLink, openSignIn } from "./signin"
import { popupAppMenu, setAppMenu, setFontChoice } from "./menu"
import { BOOT } from "./memory"
import { openRoomsEmailSignIn, openRoomsPanel, signInRooms, openAccountSignIn, reloadRooms, roomsAccessToken, roomsAccount, setRoomsBounds, setRoomsTheme, signOutRooms } from "./rooms"

// The updater loads late and defensively: a module missing from the package there must never stop the window from
// opening. It did once (2026-11-17): a build without fs-extra crashed at require time, before any try/catch could run.
type StartUpdates = (win: () => BrowserWindow | undefined) => void
let startUpdates: StartUpdates | null = null
let ulog: (message: string) => void = () => undefined
void import("./updater.js")
  .then((m) => { startUpdates = m.startUpdates; ulog = m.ulog })
  .catch((e) => console.error("kell updater unavailable", e))
import { termCreate, termKill, termKillAll, termResize, termWrite } from "./terminal"
import { browserAct, browserBounds, browserGo, browserState } from "./browser"
import { createServer } from "node:http"
import { randomBytes } from "node:crypto"
import fs from "node:fs"
import net from "node:net"
import { spawn, type ChildProcess } from "node:child_process"
import path from "node:path"

const ROOT = path.join(__dirname, "..", "..") // desktop/ (main.js compiles to electron/dist/main.js)
// Windows shows a notification under the app's own name only when it has an AppUserModelID; the installed shortcut uses
// this one, so a finished chat's notice is attributed to kell instead of a generic Electron window (2026-11-17).
if (process.platform === "win32") app.setAppUserModelId("org.kell.desktop")
// Linux AppImage: Ubuntu 24.04+ forbids the unprivileged namespaces Chromium's sandbox needs inside an AppImage, so the app would
// exit at once with a SUID-sandbox error. The .deb installs chrome-sandbox setuid and keeps the sandbox; only the AppImage turns it off.
if (process.platform === "linux" && process.env.APPIMAGE) app.commandLine.appendSwitch("no-sandbox")
let win: BrowserWindow | undefined
let next: ChildProcess | undefined

const freePort = () => new Promise<number>((ok, no) => {
  const s = net.createServer()
  s.once("error", no)
  s.listen(0, "127.0.0.1", () => { const p = (s.address() as { port: number }).port; s.close(() => ok(p)) })
})

const killTree = () => {
  if (!next?.pid) return
  if (process.platform === "win32") spawn("taskkill", ["/pid", String(next.pid), "/T", "/F"], { windowsHide: true })
  else next.kill()
  next = undefined
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus() } })

  // Startup check (docs/desktop_requirements_2026-11.md, P0 #11): a start that cannot finish says why and what to do, instead of
  // leaving no window at all (Cowork hung silently on Windows Home). The Next server's output goes to next.log in the logs
  // folder, so the reason is on disk too.
  const logsDir = () => { try { return app.getPath("logs") } catch { return app.getPath("userData") } }
  const nextLog = () => path.join(logsDir(), "next.log")
  const tail = (f: string, n = 12) => { try { return fs.readFileSync(f, "utf8").trim().split(/\r?\n/).slice(-n).join("\n") } catch { return "" } }
  const failStart = async (why: string, fix: string) => {
    ulog(`start failed: ${why}`)
    const last = tail(nextLog())
    const detail = last ? `${fix}\n\nLast lines of ${nextLog()}:\n${last}` : `${fix}\n\nLogs: ${logsDir()}`
    const r = await dialog.showMessageBox({ type: "error", title: "kell could not start", message: why, detail, buttons: ["Try again", "Open logs folder", "Quit"], defaultId: 0, cancelId: 2 })
    if (r.response === 1) await shell.openPath(logsDir())
    markQuitting(); killTree()
    if (r.response === 0) app.relaunch()
    app.exit(1)
  }
  const preflight = (): [string, string] | null => {
    try { fs.mkdirSync(app.getPath("userData"), { recursive: true }); const t = path.join(app.getPath("userData"), ".write-test"); fs.writeFileSync(t, "ok"); fs.rmSync(t) }
    catch (e: any) { return ["kell can't write to its data folder", `${app.getPath("userData")}: ${String(e?.message || e)}. Check the folder's permissions or free some disk space.`] }
    try {
      const st = (fs as any).statfsSync?.(app.getPath("userData"))
      if (st && st.bavail * st.bsize < 300 * 1024 * 1024) return ["Your disk is almost full", "kell needs at least 300 MB free to start its engine and keep your chats. Free some space and try again."]
    } catch { /* not supported here */ }
    const standalone = path.join(ROOT, ".next", "standalone", "server.js")
    if (!fs.existsSync(standalone) && !fs.existsSync(path.join(ROOT, "node_modules", "next", "dist", "bin", "next")))
      return ["kell's files are incomplete", "Part of the app is missing (an antivirus may have quarantined it). Reinstall kell from kell's download page."]
    return null
  }

  app.whenReady().then(async () => {
    const say = (...a: unknown[]) => console.log("KELL_SMOKE", ...a)
    const pre = preflight()
    if (pre) { await failStart(pre[0], pre[1]); return }
    say("electron ready; starting next")
    setAppMenu(() => win)
    const port = await freePort()
    if (port === 28894) throw new Error("28894 belongs to the OpenCode desktop app") // unreachable; the rule's guard
    // The installed app ships Next's standalone server (only the files it uses); a dev tree without that build runs `next start`.
    // KELL_ROOT tells the routes where the app is: the standalone server changes its working directory to its own folder.
    const standalone = path.join(ROOT, ".next", "standalone", "server.js")
    // The website session for the coding lane's model calls (lib/websession.ts asks here; electron/rooms.ts roomsAccessToken). Loopback
    // only, and only for a caller holding this run's secret, which only the Next child gets: the token never touches the disk.
    const secret = randomBytes(24).toString("base64url")
    const tokens = createServer(async (req, res) => {
      if (req.method !== "GET" || req.url !== "/web-token" || req.headers["x-kell-secret"] !== secret) { res.writeHead(404); res.end(); return }
      const token = await roomsAccessToken(win).catch(() => null)
      res.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" })
      res.end(JSON.stringify({ token }))
    })
    const tokenPort = await new Promise<number>((ok) => tokens.listen(0, "127.0.0.1", () => ok((tokens.address() as { port: number }).port)))
    app.on("will-quit", () => tokens.close())
    const env = { ...process.env, ELECTRON_RUN_AS_NODE: "1", KELL_PARENT_PID: String(process.pid), KELL_ROOT: ROOT, PORT: String(port), HOSTNAME: "127.0.0.1",
                  KELL_WEB_TOKEN_URL: `http://127.0.0.1:${tokenPort}/web-token`, KELL_WEB_TOKEN_SECRET: secret }
    // RAM: in the installed app the Next child's stdout/stderr are only diagnostics. Piping them makes Electron keep a
    // growing read buffer; KELL_DEBUG=1 restores the pipes for a support session.
    const quiet = app.isPackaged && process.env.KELL_DEBUG !== "1"
    let fd: number | "ignore" | undefined
    const logFd = (): number | "ignore" => {
      if (fd !== undefined) return fd
      try { fs.mkdirSync(logsDir(), { recursive: true }); fd = fs.openSync(nextLog(), "a") } catch { fd = "ignore" }
      return fd
    }
    next = fs.existsSync(standalone)
      ? spawn(process.execPath, [standalone], { cwd: ROOT, windowsHide: true, env, stdio: quiet ? ["ignore", logFd(), logFd()] : ["ignore", "pipe", "pipe"] })
      : spawn(process.execPath, [path.join(ROOT, "node_modules", "next", "dist", "bin", "next"), "start", "-p", String(port)],
              { cwd: ROOT, windowsHide: true, env, stdio: quiet ? ["ignore", logFd(), logFd()] : ["ignore", "pipe", "pipe"] })
    let started = false
    next.on("exit", (code) => {
      say(`next exited (${code})`); next = undefined
      if (!started) return               // the wait below reports it
      markQuitting(); win?.close()
    })
    next.stdout?.on("data", (c) => process.stdout.write(c))
    next.stderr?.on("data", (c) => process.stderr.write(c))
    // In the installed app the output is not piped to this process (RAM, above): it goes to next.log instead, kept short.
    if (quiet) {
      try {
        fs.mkdirSync(logsDir(), { recursive: true })
        if (fs.existsSync(nextLog()) && fs.statSync(nextLog()).size > 1024 * 1024) fs.renameSync(nextLog(), nextLog() + ".old")
      } catch { /* logging is optional */ }
    }

    const base = `http://127.0.0.1:${port}`
    let up = false
    for (let i = 0; i < 300 && !up && next; i++) {
      try { up = (await fetch(base)).ok } catch { await new Promise((r) => setTimeout(r, 300)) }
    }
    if (!up) {
      await failStart(next ? "kell's local server did not answer in 90 seconds" : "kell's local server stopped while starting",
        "This is usually an antivirus or a security policy blocking a program inside kell, or a broken install. Try again; if it keeps happening, reinstall kell, or allow kell in your antivirus.")
      return
    }
    started = true
    say(`next up on ${base}`)

    // A run starts clean: the page's storage of the last run (the folder open, its sessions, the column widths, the last tab) goes
    // before the window exists (electron/memory.ts). Only localStorage and index DB: the rooms, sign-in and cookie jars are other
    // partitions and keep their sign-ins.
    await session.defaultSession.clearStorageData({ storages: ["localstorage", "indexdb"] }).catch(() => undefined)
    win = new BrowserWindow({ width: 1240, height: 880, minWidth: 360, minHeight: 360, title: "kell", backgroundColor: "#F8F6EF", show: false,
                              // The app draws its own window: no Windows title bar or frame, no File / Edit / View strip
                              // (the page's top bar carries the tabs, the account and Help; drag it to move the window).
                              // Electron's frame on Windows cannot take the app's rounded corners and shadow.
                              frame: false,
                              webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false,
                                                               devTools: !app.isPackaged,
                                                               plugins: true } })   // Chromium's PDF viewer, for the document preview   // no inspector in the installed app
    // The page's storage emptied before it reads anything, before it can restore anything: a fresh project and no sessions.
    win.webContents.on("dom-ready", () => { void win?.webContents.executeJavaScript(BOOT).catch(() => undefined) })
    // Links: a tool's sign-in opens in the person's default browser (electron/signin.ts); any other link goes there too.
    // The app window itself never navigates away from the UI. The page asks kell whether a sign-in finished (no callback
    // comes back to this process).
    win.webContents.setWindowOpenHandler(({ url }) => {
      if (isSignInLink(url)) void openSignIn(url)
      else if (/^https?:/.test(url)) void shell.openExternal(url)
      return { action: "deny" }
    })
    win.webContents.on("will-navigate", (e, url) => {
      if (url.startsWith(base)) return
      e.preventDefault()
      if (isSignInLink(url)) void openSignIn(url)
      else if (/^https?:/.test(url)) void shell.openExternal(url)
    })
    ipcMain.on("kell:menu-popup", () => popupAppMenu(win))    // The team rooms (electron/rooms.ts) and the one account sign-in shared by the coding lane and the rooms.
    // The page owns the saved typeface; the menu only shows which one is checked and sends a click back to the page.
    ipcMain.on("kell:font-choice", (_e, font: string) => setFontChoice(font === "inter" || font === "poppins" ? font : "default"))
    ipcMain.on("kell:rooms-bounds", (_e, rect) => { if (win) setRoomsBounds(win, rect && typeof rect === "object" ? rect : null) })
    ipcMain.on("kell:rooms-reload", () => reloadRooms())
    ipcMain.on("kell:rooms-panel", (_e, panel: unknown) => openRoomsPanel(String(panel)))
    ipcMain.on("kell:rooms-email", () => openRoomsEmailSignIn())
    ipcMain.on("kell:rooms-theme", (_e, t) => setRoomsTheme(t === "dark" ? "dark" : "light"))
    ipcMain.handle("kell:rooms-account", () => roomsAccount(win))
    ipcMain.handle("kell:rooms-signout", () => signOutRooms())
    ipcMain.handle("kell:rooms-signin", (_e, provider) => (win ? signInRooms(win, provider === "github" ? "github" : "google") : false))
    ipcMain.handle("kell:account-open", (_e, url: string) => openAccountSignIn(win, String(url)))
    // The bottom panel: a terminal in the project folder (electron/terminal.ts) and a browser view (electron/browser.ts).
    ipcMain.handle("kell:term-create", (_e, cwd: unknown, cols: unknown, rows: unknown) =>
      (win ? termCreate(win, String(cwd || ""), Number(cols), Number(rows)) : { error: "no window" }))
    ipcMain.on("kell:term-write", (_e, id: unknown, data: unknown) => termWrite(String(id), String(data)))
    ipcMain.on("kell:term-resize", (_e, id: unknown, cols: unknown, rows: unknown) => termResize(String(id), Number(cols), Number(rows)))
    ipcMain.on("kell:term-kill", (_e, id: unknown) => termKill(String(id)))
    ipcMain.on("kell:browser-bounds", (_e, rect) => { if (win) browserBounds(win, rect && typeof rect === "object" ? rect : null) })
    ipcMain.handle("kell:browser-go", (_e, url: unknown) => (win ? browserGo(win, String(url || "")) : null))
    ipcMain.on("kell:browser-act", (_e, what: unknown) => browserAct(String(what)))
    ipcMain.handle("kell:browser-state", () => browserState())
    ipcMain.handle("kell:browser-external", (_e, url: unknown) => { const u = String(url || ""); if (/^https?:\/\//i.test(u)) void shell.openExternal(u) })
    // The window's own buttons, drawn by the page: the frame is off (above), so Electron's are not there to click.
    ipcMain.on("kell:win", (_e, what: string) => {
      if (!win) return
      if (what === "minimize") win.minimize()
      else if (what === "maximize") { if (win.isMaximized()) win.unmaximize(); else win.maximize() }
      else if (what === "close") win.close()
    })
    // The page's maximize button shows Windows' restore glyph while the window is maximized, so it asks once and then
    // follows the window's own maximize/unmaximize events (below).
    ipcMain.handle("kell:win-state", () => (win ? win.isMaximized() : false))
    // The project folder: the system's own folder picker (typing a path was the only way, and the button looked dead, 2026-11-06).
    ipcMain.handle("kell:pick-folder", async () => {
      const r = win ? await dialog.showOpenDialog(win, { title: "Choose a project folder", properties: ["openDirectory", "createDirectory"] })
                    : await dialog.showOpenDialog({ title: "Choose a project folder", properties: ["openDirectory", "createDirectory"] })
      return r.canceled ? null : r.filePaths[0] ?? null
    })
    // Files picked in the composer. The renderer gets absolute paths and sends only paths to the local API: the bytes
    // are read by the engine (or by the project's own tools), never copied through the page.
    ipcMain.handle("kell:pick-files", async () => {
      const r = win ? await dialog.showOpenDialog(win, { title: "Attach files", properties: ["openFile", "multiSelections"] })
                    : await dialog.showOpenDialog({ title: "Attach files", properties: ["openFile", "multiSelections"] })
      return r.canceled ? [] : r.filePaths
    })
    // Open a file outside the project in the person's default app. Project files are opened in the app's own viewer.
    ipcMain.handle("kell:open-path", async (_e, p: string) => (p ? shell.openPath(String(p)) : "no path"))
    // Documents kell made (components/DocPanel.tsx): show one in Explorer, or save a copy where the person chooses.
    ipcMain.on("kell:show-in-folder", (_e, p: string) => { if (p) shell.showItemInFolder(String(p)) })
    ipcMain.handle("kell:save-copy", async (_e, p: string) => {
      const src = String(p || "")
      if (!src || !fs.existsSync(src)) return null
      const opts = { title: "Save a copy", defaultPath: path.join(app.getPath("documents"), path.basename(src)) }
      const r = win ? await dialog.showSaveDialog(win, opts) : await dialog.showSaveDialog(opts)
      if (r.canceled || !r.filePath) return null
      fs.copyFileSync(src, r.filePath)
      return r.filePath
    })
    // Register the show handler BEFORE the load: "ready-to-show" can fire while loadURL is still resolving, and a handler
    // added after the await then never runs, leaving the window hidden forever (the installed app looked like it did
    // nothing, 2026-11-03). show() is idempotent, so it is also called once the load has finished.
    win.once("ready-to-show", () => win?.show())
    win.on("closed", () => { win = undefined })
    // Keep the page's maximize/restore glyph in step with the window, whichever way it was maximized (our button, a
    // double-click on the title bar, or Windows itself).
    win.on("maximize", () => win?.webContents.send("kell:win-state", true))
    win.on("unmaximize", () => win?.webContents.send("kell:win-state", false))
    // The frame is off, so no Windows shadow; the page adds a hairline so the window's edge reads against a light desktop.
    const edge = () => void win?.webContents.insertCSS(
      ".dk-switch { border-top: 1px solid var(--rule-strong, rgba(0,0,0,.18)); }").catch(() => undefined)
    win.webContents.on("did-finish-load", edge)
    await win.loadURL(base)
    say("window loaded")
    win.show()

    // Updates (electron/updater.ts): check at start and every few hours, offer a restart when one is downloaded, log to
    // updater.log. Not in dev or the smoke run; a failure never stops the app.
    if (app.isPackaged && process.env.KELL_SMOKE !== "1" && process.env.KELL_NO_UPDATE !== "1") startUpdates?.(() => win)
    // The tray, the global quick-ask hotkey and its box (electron/presence.ts).
    if (process.env.KELL_SMOKE !== "1") {
      try { const p = startPresence(() => win, base, ROOT, path.join(__dirname, "preload.js")); say(`presence ${JSON.stringify(p)}`) }
      catch (e: any) { say(`presence failed: ${String(e?.message || e)}`) }
    }

    // Smoke (KELL_SMOKE=1): with the window up, start the engine through the UI's own route, report, quit.
    // KELL_SMOKE_DIR is the project directory to boot the engine on.
    if (process.env.KELL_SMOKE === "1") {
      const dir = process.env.KELL_SMOKE_DIR || ""
      try {
        say(`posting /api/engine for ${dir}`)
        const r = await fetch(`${base}/api/engine`, { signal: AbortSignal.timeout(60_000), method: "POST",
                                                      headers: { "content-type": "application/json" }, body: JSON.stringify({ directory: dir }) })
        const j = await r.json()
        say(`engine ${r.status} ${JSON.stringify(j).slice(0, 200)}`)
        killTree()
        app.exit(r.ok && j.up ? 0 : 1)
      } catch (e: any) {
        console.error("KELL_SMOKE failed", String(e?.message || e))
        killTree()
        app.exit(1)
      }
    }
  })

  // Clean shutdown: the next child's whole tree (the opencode engine included) dies with the app.
  app.on("before-quit", () => { termKillAll(); killTree() })
  app.on("will-quit", killTree)
  app.on("window-all-closed", () => app.quit())
  // A crash of this process must not leave the Next server and the engine running (each is hundreds of MB and they pile up
  // across crashes). The Next child also watches this pid (instrumentation.ts) for the case where we are killed outright.
  process.on("exit", killTree)
  process.on("uncaughtException", (e) => { console.error("KELL uncaught", e); killTree(); app.exit(1) })
}
