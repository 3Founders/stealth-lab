// Where the kell CLI is for this app: the copy the installer ships (vendor/kell-mcp, unpacked beside app.asar), an
// explicit KELL_CLI, or `kell` from PATH for a developer's own install. A bundled .mjs runs through Electron in node
// mode (the Next child already runs that way); in `npm run dev` process.execPath is plain node.
//
// Keeping this in one place means sign-in, console and any future setup action all use the bundled CLI, so a machine
// that has never installed kell still works.
import fs from "node:fs"
import path from "node:path"

export type KellCommand = { cmd: string; args: string[]; shell: boolean }

export function kellCommand(args: string[]): KellCommand {
  const resources = (process as unknown as { resourcesPath?: string }).resourcesPath || ""
  const candidates = [
    process.env.KELL_CLI || "",
    path.join(process.cwd(), "vendor", "kell-mcp", "bin", "kell.mjs"),
    path.join(process.cwd().replace(/app\.asar$/, "app.asar.unpacked"), "vendor", "kell-mcp", "bin", "kell.mjs"),
    resources ? path.join(resources, "app.asar.unpacked", "vendor", "kell-mcp", "bin", "kell.mjs") : "",
  ].filter(Boolean)
  const found = candidates.find((p) => { try { return fs.statSync(p).isFile() } catch { return false } })
  if (!found) return { cmd: "kell", args, shell: true }
  if (found.endsWith(".mjs") || found.endsWith(".js")) return { cmd: process.execPath, args: [found, ...args], shell: false }
  return { cmd: found, args, shell: false }
}
