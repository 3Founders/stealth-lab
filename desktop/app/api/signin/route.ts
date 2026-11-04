// Sign in and out, from the app: one account for the coding lane and the team rooms (electron/rooms.ts).
//
//   POST {}     start `kell login --no-browser` in the same profile the app uses and return its authorize link
//               { url }; the page opens it in the rooms' cookie store, so the one sign-in on kell's consent page also
//               signs the rooms in. The CLI waits for the loopback callback itself and writes the token to
//               ~/.stealthlab/config.json, which every part of the app (engine plugin, console helper, settings) reads.
//               { already: true } when a token is already saved, unless the body says { again: true }: the rooms are not
//               signed in yet (the coding lane signed in earlier through a browser), so the same sign-in runs once more
//               and the CLI's token is replaced with one for the same account.
//   DELETE      `kell logout`: forget the saved token (the page signs the rooms out too).
//
// The token is never read, returned or logged here: the route only checks that config.json has one.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawn, type ChildProcess } from "node:child_process"
import { NextResponse } from "next/server"
import { kellCommand } from "@/lib/cli"

export const dynamic = "force-dynamic"

const kellHome = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")

function signedIn(): boolean {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(kellHome(), "config.json"), "utf8"))
    return Boolean(cfg?.token)   // no saved url is fine: the CLI falls back to its default server
  } catch {
    return false
  }
}

const run = (args: string[], detached: boolean) => {
  const c = kellCommand(args)
  return spawn(c.cmd, c.args, {
    shell: c.shell, windowsHide: true, detached, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "1", STEALTHLAB_HOME: kellHome() },
  })
}

// One login at a time: a second click returns the link the running one printed.
let pending: { child: ChildProcess; url: Promise<string> } | null = null

export async function POST(req: Request) {
  const again = await req.json().then((b) => !!b?.again).catch(() => false)
  if (signedIn() && !again) return NextResponse.json({ already: true })
  if (!pending || pending.child.exitCode !== null) {
    const child = run(["login", "--no-browser"], true)
    const url = new Promise<string>((ok, no) => {
      let out = ""
      const look = (c: Buffer) => {
        out += c.toString()
        const m = out.match(/https:\/\/\S+/)
        if (m) ok(m[0])
      }
      child.stdout?.on("data", look)
      child.stderr?.on("data", look)
      child.on("error", (e) => no(e))
      child.on("exit", (code) => no(new Error(out.trim().split("\n").pop() || `kell login exited (${code})`)))
      setTimeout(() => no(new Error("kell login printed no sign-in link")), 30_000)
    })
    child.unref()
    pending = { child, url }
  }
  try {
    return NextResponse.json({ url: await pending.url })
  } catch (err: any) {
    pending = null
    return NextResponse.json({ error: String(err?.message || err) }, { status: 500 })
  }
}

export async function DELETE() {
  if (pending && pending.child.exitCode === null) pending.child.kill()
  pending = null
  const code = await new Promise<number | null>((ok) => {
    const child = run(["logout"], false)
    child.on("error", () => ok(-1))
    child.on("exit", (c) => ok(c))
  })
  return code === 0 && !signedIn() ? NextResponse.json({ ok: true }) : NextResponse.json({ error: "kell logout failed" }, { status: 500 })
}
