// kell desktop's Electron shell (docs/desktop_app_plan.md; the shell is Electron by owner decision 2026-11-01).
//
// One window on the Next UI. Next runs as a child process (`next start` on a production build), not in-process: in the
// Electron main, next's programmatic prepare() hangs (its render-server fork resolves to electron.exe instead of node).
// The child gets ELECTRON_RUN_AS_NODE=1 so it runs as plain node, listens on an OS-picked port (port 0 probed, never a
// fixed one — the OpenCode desktop app owns 28894 and is never touched), and its whole tree — including the opencode
// engine it starts through lib/engine.ts's /api routes — dies with the app.
import { app, BrowserWindow } from "electron"
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
    const port = await freePort()
    if (port === 28894) throw new Error("28894 belongs to the OpenCode desktop app") // unreachable; the rule's guard
    next = spawn(process.execPath, [path.join(ROOT, "node_modules", "next", "dist", "bin", "next"), "start", "-p", String(port)],
                 { cwd: ROOT, windowsHide: true, env: { ...process.env, ELECTRON_RUN_AS_NODE: "1", KELL_PARENT_PID: String(process.pid) }, stdio: ["ignore", "pipe", "pipe"] })
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

    win = new BrowserWindow({ width: 1240, height: 880, title: "kell", backgroundColor: "#F8F6EF", show: false })
    await win.loadURL(base)
    say("window loaded")
    win.once("ready-to-show", () => win?.show())
    win.on("closed", () => { win = undefined })

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
