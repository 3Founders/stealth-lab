// The one bridge between the page and the Electron main: open a tool's sign-in in the app's own sign-in window, and hear
// when it finished. Nothing else is exposed (contextIsolation stays on; the page never sees ipcRenderer itself).
import { contextBridge, ipcRenderer } from "electron"

contextBridge.exposeInMainWorld("kellDesktop", {
  openSignIn: (url: string) => ipcRenderer.send("kell:open-signin", String(url)),
  onSignInDone: (cb: (r: { url: string; ok: boolean }) => void) => {
    const h = (_e: unknown, r: { url: string; ok: boolean }) => cb(r)
    ipcRenderer.on("kell:signin-done", h)
    return () => { ipcRenderer.removeListener("kell:signin-done", h) }
  },
})
