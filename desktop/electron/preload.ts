// The one bridge between the page and the Electron main: open a tool's sign-in in the app's own sign-in window and hear
// when it finished; the account sign-in and sign-out; the team rooms view (electron/rooms.ts). Nothing else is exposed
// (contextIsolation stays on; the page never sees ipcRenderer itself).
import { contextBridge, ipcRenderer } from "electron"

type Rect = { x: number; y: number; width: number; height: number }

contextBridge.exposeInMainWorld("kellDesktop", {
  openSignIn: (url: string) => ipcRenderer.send("kell:open-signin", String(url)),
  onSignInDone: (cb: (r: { url: string; ok: boolean }) => void) => {
    const h = (_e: unknown, r: { url: string; ok: boolean }) => cb(r)
    ipcRenderer.on("kell:signin-done", h)
    return () => { ipcRenderer.removeListener("kell:signin-done", h) }
  },
  openAccount: (url: string): Promise<boolean> => ipcRenderer.invoke("kell:account-open", String(url)),
  signOutRooms: (): Promise<void> => ipcRenderer.invoke("kell:rooms-signout"),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("kell:pick-folder"),
  // The app draws its own window (no Windows frame), so it also draws minimize / maximize / close (the page's top bar).
  window: { minimize: () => ipcRenderer.send("kell:win", "minimize"), toggleMaximize: () => ipcRenderer.send("kell:win", "maximize"),
            close: () => ipcRenderer.send("kell:win", "close") },
  onMenu: (cb: (what: string) => void) => {
    const h = (_e: unknown, what: string) => cb(String(what))
    ipcRenderer.on("kell:menu", h)
    return () => { ipcRenderer.removeListener("kell:menu", h) }
  },
  rooms: {
    show: (rect: Rect | null) => ipcRenderer.send("kell:rooms-bounds", rect),
    reload: () => ipcRenderer.send("kell:rooms-reload"),
    theme: (t: "light" | "dark") => ipcRenderer.send("kell:rooms-theme", t),
    emailSignIn: () => ipcRenderer.send("kell:rooms-email"),
    signIn: (provider: "google" | "github"): Promise<boolean> => ipcRenderer.invoke("kell:rooms-signin", provider),
    account: (): Promise<{ id: string; email: string | null } | null | undefined> => ipcRenderer.invoke("kell:rooms-account"),
  },
})
