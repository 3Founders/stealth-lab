// The app's terminal (owner, 2026-11-15: "basic things like an in built ... terminal", as in Claude Code).
//
// A real shell in the open project's folder, run with node-pty (ConPTY on Windows), drawn by xterm.js in the page. Only
// the app's own page reaches it: the preload bridge is given to the main window alone, never to the rooms or the browser
// view. A terminal lives until the page closes it or the app quits; the page keeps one per project.
//
// It is the person's own shell, spawned directly: PowerShell 7 (`pwsh`) when it is installed, else the Windows PowerShell
// that ships with Windows (or $SHELL on Linux). Nothing is wrapped in cmd.exe, a login script or an emulator. Two details
// on Windows are load-bearing and easy to lose:
//
//   * The shell writes its first prompt the instant ConPTY starts it, which is before `onData` below can exist; those bytes
//     are dropped. A same-size `resize` makes ConPTY repaint its whole screen buffer, which brings the prompt back without
//     the person having to press a key (a `\r` would also work, but it leaves a blank command line behind).
//   * `useConpty` is deprecated in node-pty 1.1 and ignored; ConPTY is chosen by the OS version. Passing it only suggests
//     that this code controls something it does not.
import type { BrowserWindow } from "electron"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

type Pty = { pid: number; write(d: string): void; resize(c: number, r: number): void; kill(): void; onData(cb: (d: string) => void): unknown; onExit(cb: (e: { exitCode: number }) => void): unknown }
// node-pty loads its prebuilt binary (prebuilds/win32-x64, N-API, so no rebuild per Electron version). Loaded on first use:
// a missing or broken binary costs the terminal, never the app's start.
let ptyMod: { spawn: (file: string, args: string[], o: Record<string, unknown>) => Pty } | null | undefined
function pty() {
  if (ptyMod === undefined) {
    try { ptyMod = require("node-pty") } catch (e) { console.error("KELL terminal unavailable", e); ptyMod = null }
  }
  return ptyMod
}

const terms = new Map<string, Pty>()
let seq = 0

/** An executable on PATH, or null. Windows PATH entries are searched with PATHEXT, the way a shell would. */
function onPath(exe: string): string | null {
  const dirs = (process.env.PATH || process.env.Path || "").split(path.delimiter)
  const exts = process.platform === "win32" ? (process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";") : [""]
  for (const dir of dirs) {
    if (!dir) continue
    for (const ext of exts) {
      const file = path.join(dir, exe + ext)
      try { if (fs.statSync(file).isFile()) return file } catch { /* not in this folder */ }
    }
  }
  return null
}

/** The shell the person actually uses, and the name the panel shows: PowerShell 7 when installed, else Windows
 *  PowerShell (5.1), else the console's own command processor. Linux gets $SHELL, or bash. */
function shell(): { file: string; args: string[]; label: string } {
  if (process.platform === "win32") {
    const pwsh = onPath("pwsh")
    if (pwsh) return { file: pwsh, args: ["-NoLogo"], label: "PowerShell 7" }
    const system = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if (fs.existsSync(system)) return { file: system, args: ["-NoLogo"], label: "Windows PowerShell" }
    return { file: process.env.ComSpec || "cmd.exe", args: [], label: "Command Prompt" }
  }
  const file = process.env.SHELL || "/bin/bash"
  return { file, args: ["-l"], label: path.basename(file) }
}

export type TermInfo = { id: string; shell: string; cwd: string }

export function termCreate(win: BrowserWindow, cwd: string, cols: number, rows: number): TermInfo | { error: string } {
  const m = pty()
  if (!m) return { error: "The terminal could not start on this computer." }
  let dir = os.homedir()
  try { if (cwd && fs.statSync(cwd).isDirectory()) dir = cwd } catch { /* not a folder: the home folder */ }
  const { file, args, label } = shell()
  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env)) if (typeof v === "string" && k !== "ELECTRON_RUN_AS_NODE") env[k] = v
  env.TERM = "xterm-256color"
  env.COLORTERM = "truecolor"
  const size = { cols: clamp(cols, 20, 500, 80), rows: clamp(rows, 4, 200, 24) }
  let p: Pty
  try {
    p = m.spawn(file, args, { name: "xterm-256color", cols: size.cols, rows: size.rows, cwd: dir, env })
  } catch (e: any) {
    return { error: `The terminal could not start: ${String(e?.message || e).slice(0, 160)}` }
  }
  const id = `t${++seq}`
  terms.set(id, p)
  p.onData((d) => { if (!win.isDestroyed()) win.webContents.send("kell:term-data", id, d) })
  p.onExit(({ exitCode }) => {
    terms.delete(id)
    if (!win.isDestroyed()) win.webContents.send("kell:term-exit", id, exitCode)
  })
  // Repaint the screen the shell drew before the listener above existed (see the note at the top).
  try { p.resize(size.cols, size.rows) } catch { /* it exited in its first millisecond */ }
  return { id, shell: label, cwd: dir }
}

export function termWrite(id: string, data: string) { terms.get(id)?.write(String(data)) }
export function termResize(id: string, cols: number, rows: number) {
  try { terms.get(id)?.resize(clamp(cols, 20, 500, 80), clamp(rows, 4, 200, 24)) } catch { /* exited meanwhile */ }
}
export function termKill(id: string) {
  const p = terms.get(id)
  terms.delete(id)
  try { p?.kill() } catch { /* already gone */ }
}
/** Every terminal ends with the app (its shells and what they started). */
export function termKillAll() { for (const id of [...terms.keys()]) termKill(id) }

function clamp(n: unknown, lo: number, hi: number, dflt: number) {
  const v = Math.floor(Number(n))
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : dflt
}
