// The app's terminal (owner, 2026-11-15: "basic things like an in built ... terminal", as in Claude Code).
//
// A real shell in the open project's folder, run with node-pty (ConPTY on Windows), drawn by xterm.js in the page. Only
// the app's own page reaches it: the preload bridge is given to the main window alone, never to the rooms or the browser
// view. A terminal lives until the page closes it or the app quits; the page keeps one per project.
import type { BrowserWindow } from "electron"
import fs from "node:fs"
import os from "node:os"

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

function shell(): { file: string; args: string[] } {
  if (process.platform === "win32") return { file: "powershell.exe", args: ["-NoLogo"] }
  return { file: process.env.SHELL || "/bin/bash", args: ["-l"] }
}

export function termCreate(win: BrowserWindow, cwd: string, cols: number, rows: number): { id: string } | { error: string } {
  const m = pty()
  if (!m) return { error: "The terminal could not start on this computer." }
  let dir = os.homedir()
  try { if (cwd && fs.statSync(cwd).isDirectory()) dir = cwd } catch { /* not a folder: the home folder */ }
  const { file, args } = shell()
  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env)) if (typeof v === "string" && k !== "ELECTRON_RUN_AS_NODE") env[k] = v
  env.TERM = "xterm-256color"
  env.COLORTERM = "truecolor"
  let p: Pty
  try {
    p = m.spawn(file, args, { name: "xterm-256color", cols: clamp(cols, 20, 500, 80), rows: clamp(rows, 4, 200, 24), cwd: dir, env, useConpty: true })
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
  return { id }
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
