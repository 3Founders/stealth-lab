// Chat without a folder (docs/desktop_requirements_2026-11.md, P0 #1). The engine serves one folder at a time, so "no folder" is
// a folder of its own: Documents\kell (or ~/kell), made on first use. Plain questions, advice, drafts and the documents kell makes
// for them land there, so a person can ask anything without choosing a project first. Its AGENTS.md tells the engine it is a
// general assistant here, not a coding agent in someone's repository.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export const HOME_NAME = "Chats"

export function homeDir(): string {
  if (process.env.KELL_HOME_DIR) return process.env.KELL_HOME_DIR
  const docs = path.join(os.homedir(), "Documents")
  return path.join(fs.existsSync(docs) ? docs : os.homedir(), "kell")
}

const AGENTS = `# kell chats

This folder is the person's general chat space in kell, not a code project. Most requests here are questions, advice,
explanations, writing, planning and analysis: answer them directly in the chat. Do not explore or list this folder
unless the person asks about a file in it.

When the person asks for a document, spreadsheet, slide deck or PDF, make a real file with \`kell-docs\` (see the
instructions you were given) and save it in this folder, named for what it is. Files the person attaches may live
anywhere on the computer; read them where they are.
`

/** The chats folder, created with its AGENTS.md when missing. */
export function ensureHome(): string {
  const dir = homeDir()
  fs.mkdirSync(dir, { recursive: true })
  const agents = path.join(dir, "AGENTS.md")
  if (!fs.existsSync(agents)) fs.writeFileSync(agents, AGENTS)
  return dir
}

const norm = (p: string) => path.resolve(p).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
export const isHome = (dir: string | null | undefined) => !!dir && norm(dir) === norm(homeDir())
