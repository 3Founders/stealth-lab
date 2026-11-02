// Sign in, from the app: runs `kell login` in the same profile the app uses, so the browser OAuth writes the token to
// ~/.stealthlab/config.json and every part of the app (engine plugin, console helper, settings) picks it up.
//
//   POST {}            start `kell login` (detached: it opens a browser and waits for the callback itself).
//                      Returns { started: true } or { already: true } when a token is already saved.
//
// The token is never read, returned or logged here: the route only checks that config.json has one.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawn } from "node:child_process"
import { NextResponse } from "next/server"

export const dynamic = "force-dynamic"

const kellHome = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
const kell = () => process.env.KELL_CLI || "kell"

function signedIn(): boolean {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(kellHome(), "config.json"), "utf8"))
    return Boolean(cfg?.token && cfg?.url)
  } catch {
    return false
  }
}

// One login at a time: a double click must not start two browser flows.
let lastStart = 0

export async function POST() {
  if (signedIn()) return NextResponse.json({ already: true })
  if (Date.now() - lastStart < 10_000) return NextResponse.json({ started: true, note: "already starting" })
  lastStart = Date.now()
  try {
    const child = spawn(kell(), ["login"], {
      shell: true, windowsHide: true, detached: true, stdio: "ignore",
      env: { ...process.env, STEALTHLAB_HOME: kellHome() },
    })
    child.unref()
    child.on("error", () => { /* the settings poll reports the outcome */ })
    return NextResponse.json({ started: true })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 500 })
  }
}
