// kell desktop's Electron shell (docs/desktop_app_plan.md; the shell is Electron by owner decision 2026-11-01).
//
// One window on the Next UI. Next runs as a child process (`next start` on a production build), not in-process: in the
// Electron main, next's programmatic prepare() hangs (its render-server fork resolves to electron.exe instead of node).
// The child gets ELECTRON_RUN_AS_NODE=1 so it runs as plain node, listens on an OS-picked port (port 0 probed, never a
// fixed one — the OpenCode desktop app owns 28894 and is never touched), and its whole tree — including the opencode
// engine it starts through lib/engine.ts's /api routes — dies with the app.
import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron"
import { autoUpdater } from "electron-updater"
import { isSignInLink, openSignIn } from "./signin"
import { popupAppMenu, setAppMenu } from "./menu"
import { BOOT } from "./memory"
import { openRoomsEmailSignIn, signInRooms, openAccountSignIn, reloadRooms, roomsAccessToken, roomsAccount, setRoomsBounds, setRoomsTheme, signOutRooms } from "./rooms"
import { createServer } from "node:http"
import { randomBytes } from "node:crypto"
import fs from "node:fs"
import net from "node:net"
import { spawn, type ChildProcess } from "node:child_process"
import path from "node:path"

const ROOT = path.join(__dirname, "..", "..") // desktop/ (main.js compiles to electron/dist/main.js)
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
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus() } })

  app.whenReady().then(async () => {
    const say = (...a: unknown[]) => console.log("KELL_SMOKE", ...a)
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
    next = fs.existsSync(standalone)
      ? spawn(process.execPath, [standalone], { cwd: ROOT, windowsHide: true, env, stdio: ["ignore", "pipe", "pipe"] })
      : spawn(process.execPath, [path.join(ROOT, "node_modules", "next", "dist", "bin", "next"), "start", "-p", String(port)],
              { cwd: ROOT, windowsHide: true, env, stdio: ["ignore", "pipe", "pipe"] })
    next.on("exit", (code) => { say(`next exited (${code})`); next = undefined; win?.close() })
    next.stdout?.on("data", (c) => process.stdout.write(c))
    next.stderr?.on("data", (c) => process.stderr.write(c))

    const base = `http://127.0.0.1:${port}`
    let up = false
    for (let i = 0; i < 200 && !up; i++) {
      try { up = (await fetch(base)).ok } catch { await new Promise((r) => setTimeout(r, 300)) }
    }
    if (!up) throw new Error("the Next server never answered")
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
                                                               devTools: !app.isPackaged } })   // no inspector in the installed app
    // The page's storage emptied before it reads anything, before it can restore anything: a fresh project and no sessions.
    win.webContents.on("dom-ready", () => { void win?.webContents.executeJavaScript(BOOT).catch(() => undefined) })
    // Links: a tool's sign-in opens in the app's own sign-in window (electron/signin.ts); any other link goes to the
    // system browser. The app window itself never navigates away from the UI.
    const signInDone = (r: { url: string; ok: boolean }) => win?.webContents.send("kell:signin-done", r)
    win.webContents.setWindowOpenHandler(({ url }) => {
      if (isSignInLink(url)) openSignIn(win, url, signInDone)
      else if (/^https?:/.test(url)) void shell.openExternal(url)
      return { action: "deny" }
    })
    win.webContents.on("will-navigate", (e, url) => {
      if (url.startsWith(base)) return
      e.preventDefault()
      if (isSignInLink(url)) openSignIn(win, url, signInDone)
      else if (/^https?:/.test(url)) void shell.openExternal(url)
    })
    ipcMain.on("kell:open-signin", (_e, url: string) => openSignIn(win, String(url), signInDone))
    ipcMain.on("kell:menu-popup", () => popupAppMenu(win))    // The team rooms (electron/rooms.ts) and the one account sign-in shared by the coding lane and the rooms.
    ipcMain.on("kell:rooms-bounds", (_e, rect) => { if (win) setRoomsBounds(win, rect && typeof rect === "object" ? rect : null) })
    ipcMain.on("kell:rooms-reload", () => reloadRooms())
    ipcMain.on("kell:rooms-email", () => openRoomsEmailSignIn())
    ipcMain.on("kell:rooms-theme", (_e, t) => setRoomsTheme(t === "dark" ? "dark" : "light"))
    ipcMain.handle("kell:rooms-account", () => roomsAccount())
    ipcMain.handle("kell:rooms-signout", () => signOutRooms())
    ipcMain.handle("kell:rooms-signin", (_e, provider) => (win ? signInRooms(win, provider === "github" ? "github" : "google") : false))
    ipcMain.handle("kell:account-open", (_e, url: string) => openAccountSignIn(win, String(url)))
    // The window's own buttons, drawn by the page: the frame is off (above), so Electron's are not there to click.
    ipcMain.on("kell:win", (_e, what: string) => {
      if (!win) return
      if (what === "minimize") win.minimize()
      else if (what === "maximize") { if (win.isMaximized()) win.unmaximize(); else win.maximize() }
      else if (what === "close") win.close()
    })
    // The project folder: the system's own folder picker (typing a path was the only way, and the button looked dead, 2026-11-06).
    ipcMain.handle("kell:pick-folder", async () => {
      const r = win ? await dialog.showOpenDialog(win, { title: "Choose a project folder", properties: ["openDirectory", "createDirectory"] })
                    : await dialog.showOpenDialog({ title: "Choose a project folder", properties: ["openDirectory", "createDirectory"] })
      return r.canceled ? null : r.filePaths[0] ?? null
    })
    // Register the show handler BEFORE the load: "ready-to-show" can fire while loadURL is still resolving, and a handler
    // added after the await then never runs, leaving the window hidden forever (the installed app looked like it did
    // nothing, 2026-11-03). show() is idempotent, so it is also called once the load has finished.
    win.once("ready-to-show", () => win?.show())
    win.on("closed", () => { win = undefined })
    // The frame is off, so no Windows shadow; the page adds a hairline so the window's edge reads against a light desktop.
    const edge = () => void win?.webContents.insertCSS(
      ".dk-switch { border-top: 1px solid var(--rule-strong, rgba(0,0,0,.18)); }").catch(() => undefined)
    win.webContents.on("did-finish-load", edge)
    await win.loadURL(base)
    say("window loaded")
    win.show()

    // Updates: the installed app asks the public releases-only repo (build.publish in package.json) for a newer installer
    // once at start, downloads it quietly and installs it when the app closes. Not in dev or the smoke run; a failure (no
    // network, no release yet) is logged and never stops the app. No token is involved: the releases are public.
    if (app.isPackaged && process.env.KELL_SMOKE !== "1" && process.env.KELL_NO_UPDATE !== "1") {
      autoUpdater.autoDownload = true
      autoUpdater.autoInstallOnAppQuit = true
      autoUpdater.on("update-downloaded", (i) => say(`update ${i.version} downloaded; installs when the app closes`))
      autoUpdater.checkForUpdates().catch((e) => say(`update check failed: ${String(e?.message || e).slice(0, 160)}`))
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
  app.on("before-quit", killTree)
  app.on("will-quit", killTree)
  app.on("window-all-closed", () => app.quit())
  // A crash of this process must not leave the Next server and the engine running (each is hundreds of MB and they pile up
  // across crashes). The Next child also watches this pid (instrumentation.ts) for the case where we are killed outright.
  process.on("exit", killTree)
  process.on("uncaughtException", (e) => { console.error("KELL uncaught", e); killTree(); app.exit(1) })
}
