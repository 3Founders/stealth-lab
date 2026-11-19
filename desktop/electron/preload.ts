// The one bridge between the page and the Electron main: the account sign-in and sign-out; the team rooms view
// (electron/rooms.ts); the bottom panel's terminal and browser. Nothing else is exposed
// (contextIsolation stays on; the page never sees ipcRenderer itself).
import { contextBridge, ipcRenderer, webUtils } from "electron"

type Rect = { x: number; y: number; width: number; height: number }
type BrowserState = { url: string; title: string; loading: boolean; back: boolean; forward: boolean }

contextBridge.exposeInMainWorld("kellDesktop", {
  openAccount: (url: string): Promise<boolean> => ipcRenderer.invoke("kell:account-open", String(url)),
  signOutRooms: (): Promise<void> => ipcRenderer.invoke("kell:rooms-signout"),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("kell:pick-folder"),
  pickFiles: (): Promise<string[]> => ipcRenderer.invoke("kell:pick-files"),
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  openPath: (path: string): Promise<string> => ipcRenderer.invoke("kell:open-path", String(path)),
  showInFolder: (path: string) => ipcRenderer.send("kell:show-in-folder", String(path)),
  saveCopy: (path: string): Promise<string | null> => ipcRenderer.invoke("kell:save-copy", String(path)),
  // The typeface (View > Font): the page applies it and tells the menu; the menu tells the page when a choice is clicked.
  setFont: (font: string) => ipcRenderer.send("kell:font-choice", String(font)),
  onFont: (cb: (font: string) => void) => {
    const h = (_e: unknown, font: string) => cb(String(font))
    ipcRenderer.on("kell:font", h)
    return () => { ipcRenderer.removeListener("kell:font", h) }
  },
  // The app draws its own window (no Windows frame), so it also draws minimize / maximize / close (the page's top bar).
  window: { minimize: () => ipcRenderer.send("kell:win", "minimize"), toggleMaximize: () => ipcRenderer.send("kell:win", "maximize"),
            close: () => ipcRenderer.send("kell:win", "close"),
            // Windows' maximize button becomes "restore" while the window is maximized: ask once, then follow the events.
            isMaximized: (): Promise<boolean> => ipcRenderer.invoke("kell:win-state"),
            onState: (cb: (max: boolean) => void) => {
              const h = (_e: unknown, max: boolean) => cb(Boolean(max))
              ipcRenderer.on("kell:win-state", h)
              return () => { ipcRenderer.removeListener("kell:win-state", h) }
            } },
  onMenu: (cb: (what: string) => void) => {
    const h = (_e: unknown, what: string) => cb(String(what))
    ipcRenderer.on("kell:menu", h)
    return () => { ipcRenderer.removeListener("kell:menu", h) }
  },
  showMenu: () => ipcRenderer.send("kell:menu-popup"),
  // The quick-ask box (electron/presence.ts): it sends a question; the main window receives it and starts a new chat.
  quick: {
    ask: (text: string) => ipcRenderer.send("kell:quick-ask", String(text)),
    hide: () => ipcRenderer.send("kell:quick-hide"),
    openMain: () => ipcRenderer.send("kell:quick-open-main"),
    onShown: (cb: () => void) => { const h = () => cb(); ipcRenderer.on("kell:quick-shown", h); return () => { ipcRenderer.removeListener("kell:quick-shown", h) } },
  },
  onQuick: (cb: (text: string) => void) => {
    const h = (_e: unknown, text: string) => cb(String(text))
    ipcRenderer.on("kell:quick", h)
    return () => { ipcRenderer.removeListener("kell:quick", h) }
  },
  // The bottom panel's terminal: a shell in the project folder (electron/terminal.ts).
  term: {
    create: (cwd: string, cols: number, rows: number): Promise<{ id: string; shell: string; cwd: string } | { error: string }> => ipcRenderer.invoke("kell:term-create", cwd, cols, rows),
    write: (id: string, data: string) => ipcRenderer.send("kell:term-write", id, data),
    resize: (id: string, cols: number, rows: number) => ipcRenderer.send("kell:term-resize", id, cols, rows),
    kill: (id: string) => ipcRenderer.send("kell:term-kill", id),
    pasteText: (): Promise<string> => ipcRenderer.invoke("kell:term-paste"),
    onData: (cb: (id: string, data: string) => void) => {
      const h = (_e: unknown, id: string, data: string) => cb(String(id), String(data))
      ipcRenderer.on("kell:term-data", h)
      return () => { ipcRenderer.removeListener("kell:term-data", h) }
    },
    onExit: (cb: (id: string, code: number) => void) => {
      const h = (_e: unknown, id: string, code: number) => cb(String(id), Number(code))
      ipcRenderer.on("kell:term-exit", h)
      return () => { ipcRenderer.removeListener("kell:term-exit", h) }
    },
  },
  // The bottom panel's browser view (electron/browser.ts).
  browser: {
    show: (rect: Rect | null) => ipcRenderer.send("kell:browser-bounds", rect),
    go: (url: string): Promise<string | null> => ipcRenderer.invoke("kell:browser-go", url),
    act: (what: "back" | "forward" | "reload" | "stop") => ipcRenderer.send("kell:browser-act", what),
    state: (): Promise<BrowserState> => ipcRenderer.invoke("kell:browser-state"),
    external: (url: string) => ipcRenderer.invoke("kell:browser-external", url),
    onState: (cb: (s: BrowserState) => void) => {
      const h = (_e: unknown, s: BrowserState) => cb(s)
      ipcRenderer.on("kell:browser-state", h)
      return () => { ipcRenderer.removeListener("kell:browser-state", h) }
    },
  },
  rooms: {
    show: (rect: Rect | null) => ipcRenderer.send("kell:rooms-bounds", rect),
    reload: () => ipcRenderer.send("kell:rooms-reload"),
    panel: (panel: "desktop" | "submit") => ipcRenderer.send("kell:rooms-panel", panel),
    theme: (t: "light" | "dark") => ipcRenderer.send("kell:rooms-theme", t),
    emailSignIn: () => ipcRenderer.send("kell:rooms-email"),
    signIn: (provider: "google" | "github"): Promise<boolean> => ipcRenderer.invoke("kell:rooms-signin", provider),
    account: (): Promise<{ id: string; email: string | null } | null | undefined> => ipcRenderer.invoke("kell:rooms-account"),
  },
})
