// The app's menu bar (owner, 2026-11-07: the File / Edit / View / Window bar felt clumsy). Electron's default menu is generic: its Help
// opens Electron's own pages and View carries reload and developer tools. This one is kell's: File opens a folder or starts a new
// session (the page does the work, told through "kell:menu"), Edit and Window are the standard ones, View only zooms and goes full
// screen (reload and the inspector only in a development build), Help opens kell's pages in the person's browser.
import { app, Menu, shell, type BrowserWindow, type MenuItemConstructorOptions } from "electron"

const SITE = "https://www.kell.org.in"

export function setAppMenu(win: () => BrowserWindow | undefined) {
  const tell = (what: "open-folder" | "new-session") => () => win()?.webContents.send("kell:menu", what)
  const dev: MenuItemConstructorOptions[] = app.isPackaged ? [] : [{ type: "separator" }, { role: "reload" }, { role: "toggleDevTools" }]
  const template: MenuItemConstructorOptions[] = [
    { label: "&File", submenu: [
      { label: "Open folder…", accelerator: "CmdOrCtrl+O", click: tell("open-folder") },
      { label: "New session", accelerator: "CmdOrCtrl+N", click: tell("new-session") },
      { type: "separator" },
      { role: "quit", label: "Quit kell" },
    ] },
    { label: "&Edit", submenu: [
      { role: "undo" }, { role: "redo" }, { type: "separator" },
      { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" },
    ] },
    { label: "&View", submenu: [
      { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }, ...dev,
    ] },
    { label: "&Window", submenu: [{ role: "minimize" }, { role: "close" }] },
    { label: "&Help", submenu: [
      { label: "How kell works", click: () => void shell.openExternal(`${SITE}/docs`) },
      { label: "Plans and credits", click: () => void shell.openExternal(`${SITE}/pricing`) },
      { label: "Account and billing", click: () => void shell.openExternal(`${SITE}/account/billing`) },
      { type: "separator" },
      { label: "Contact us", click: () => void shell.openExternal(`${SITE}/contact`) },
      { type: "separator" },
      { label: `kell ${app.getVersion()}`, enabled: false },
    ] },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

/** Show the same File / Edit / View / Window / Help menu as a popup, for the frameless window's top bar. */
export function popupAppMenu(win: BrowserWindow | undefined) {
  const menu = Menu.getApplicationMenu()
  if (menu && win) menu.popup({ window: win })
}
