// POST { directory }: runs `kell undo` in the project (roll back the last change a cheap model applied).
import { spawnSync } from "node:child_process"
import { NextResponse } from "next/server"

const kell = () => process.env.KELL_CLI || "kell"

export async function POST(req: Request) {
  try {
    const { directory } = await req.json()
    if (!directory) return NextResponse.json({ error: "feed me a directory" }, { status: 400 })
    const r = spawnSync(kell(), ["undo"], { cwd: directory, encoding: "utf8", timeout: 60_000, shell: true })
    return NextResponse.json({ output: (r.stdout || "") + (r.stderr || ""), code: r.status })
  } catch (err: any) {
    return NextResponse.json({ error: String(err?.message || err) }, { status: 500 })
  }
}
