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
  rooms: {
    show: (rect: Rect | null) => ipcRenderer.send("kell:rooms-bounds", rect),
    reload: () => ipcRenderer.send("kell:rooms-reload"),
    theme: (t: "light" | "dark") => ipcRenderer.send("kell:rooms-theme", t),
    account: (): Promise<{ id: string; email: string | null } | null | undefined> => ipcRenderer.invoke("kell:rooms-account"),
  },
})
