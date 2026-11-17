// kell as a desktop app rather than a window (docs/desktop_requirements_2026-11.md, P0 #3 and #4, 2026-11-17):
//
//   - a tray icon: kell keeps running when its window is closed (a long task, a schedule), with Open, Quick ask, Check for updates
//     and Quit; closing the window hides it, Quit (tray, menu or Ctrl+Q) ends it;
//   - a global hotkey (Ctrl+Shift+Space; Option+Shift+Space on a Mac) that opens a small quick-ask box over any app. Enter sends the
//     question to a new chat in the main window and shows it; Escape or clicking away hides the box.
import { app, BrowserWindow, globalShortcut, ipcMain, Menu, nativeImage, Notification, Tray } from "electron"
import path from "node:path"
import fs from "node:fs"
import { checkForUpdates } from "./updater"

export const QUICK_KEY = process.platform === "darwin" ? "Alt+Shift+Space" : "Control+Shift+Space"

let tray: Tray | null = null
let quick: BrowserWindow | null = null
let quitting = false
let toldTray = false

export const isQuitting = () => quitting
export const markQuitting = () => { quitting = true }

function iconPath(root: string) {
  const c = [path.join(root, "build", "icon.png"), path.join(process.resourcesPath || "", "icon.png")]
  return c.find((p) => { try { return fs.statSync(p).isFile() } catch { return false } }) || ""
}

function showMain(win: () => BrowserWindow | undefined) {
  const w = win()
  if (!w) return
  if (w.isMinimized()) w.restore()
  w.show()
  w.focus()
}

function openQuick(base: string, preload: string) {
  if (quick && !quick.isDestroyed()) {
    if (quick.isVisible()) { quick.hide(); return }
    quick.center(); quick.show(); quick.focus()
    quick.webContents.send("kell:quick-shown")
    return
  }
  quick = new BrowserWindow({
    width: 640, height: 132, frame: false, resizable: false, alwaysOnTop: true, skipTaskbar: true, show: false,
    transparent: true, backgroundColor: "#00000000", title: "Ask kell", fullscreenable: false, minimizable: false, maximizable: false,
    webPreferences: { preload, contextIsolation: true, nodeIntegration: false, devTools: !app.isPackaged },
  })
  quick.setAlwaysOnTop(true, "floating")
  quick.on("blur", () => { if (quick && !quick.webContents.isDevToolsOpened()) quick.hide() })
  quick.on("closed", () => { quick = null })
  quick.once("ready-to-show", () => { quick?.center(); quick?.show(); quick?.focus() })
  void quick.loadURL(`${base}/quick`)
}

/** The tray, the hotkey and the quick box. Call once the main window exists. */
export function startPresence(win: () => BrowserWindow | undefined, base: string, root: string, preload: string) {
  ipcMain.on("kell:quick-ask", (_e, text: unknown) => {
    const t = String(text || "").trim()
    quick?.hide()
    if (!t) return
    showMain(win)
    win()?.webContents.send("kell:quick", t.slice(0, 20_000))
  })
  ipcMain.on("kell:quick-hide", () => quick?.hide())
  ipcMain.on("kell:quick-open-main", () => { quick?.hide(); showMain(win) })

  const okKey = globalShortcut.register(QUICK_KEY, () => { console.log("KELL_SMOKE", "quick-ask hotkey"); openQuick(base, preload) })
  if (process.env.KELL_TEST_QUICK === "1") setTimeout(() => openQuick(base, preload), 3000)
  if (!okKey) console.log("KELL_SMOKE", `the quick-ask hotkey ${QUICK_KEY} is taken by another app`)
  app.on("will-quit", () => globalShortcut.unregisterAll())

  const icon = iconPath(root)
  if (icon) {
    const img = nativeImage.createFromPath(icon).resize({ width: 16, height: 16 })
    tray = new Tray(img)
    tray.setToolTip("kell")
    const label = QUICK_KEY.replace("Control", "Ctrl").replace("Alt", process.platform === "darwin" ? "Option" : "Alt")
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "Open kell", click: () => showMain(win) },
      { label: `Quick ask   ${label}`, click: () => openQuick(base, preload) },
      { type: "separator" },
      { label: "Check for updates…", click: () => void checkForUpdates(win, true) },
      { type: "separator" },
      { label: "Quit kell", click: () => { quitting = true; app.quit() } },
    ]))
    tray.on("click", () => showMain(win))
    tray.on("double-click", () => showMain(win))
  }

  // Closing the window hides it while the tray is there; the first time, say where kell went.
  const w = win()
  w?.on("close", (e) => {
    if (quitting || !tray) return
    e.preventDefault()
    w.hide()
    if (!toldTray && Notification.isSupported()) {
      toldTray = true
      new Notification({ title: "kell is still running", body: `It's in the tray. Press ${QUICK_KEY.replace("Control", "Ctrl")} to ask from anywhere; right-click the tray icon to quit.` }).show()
    }
  })
  app.on("before-quit", () => { quitting = true })
  return { hotkey: okKey ? QUICK_KEY : null, tray: !!tray }
}
