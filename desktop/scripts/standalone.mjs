// After `next build` with output "standalone": put the static files where .next/standalone/server.js serves them from (Next leaves
// that copy to the deployer), and report the size so a regression shows in the build log.
import fs from "node:fs"
import path from "node:path"

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "..")
const out = path.join(root, ".next", "standalone")
if (!fs.existsSync(path.join(out, "server.js"))) {
  console.error("standalone: .next/standalone/server.js is missing; is output: \"standalone\" set in next.config.mjs?")
  process.exit(1)
}
fs.cpSync(path.join(root, ".next", "static"), path.join(out, ".next", "static"), { recursive: true })
if (fs.existsSync(path.join(root, "public"))) fs.cpSync(path.join(root, "public"), path.join(out, "public"), { recursive: true })
// The server tree too. Next's standalone trace can leave out the chunks its own pages/_document.js requires
// ("./vendor-chunks/next.js", "./611.js"), and the packaged app then never boots -- no window, "the Next server never
// answered" (found 2026-11-08 on a cold .next). Copying .next/server over the traced copy is small next to the app and
// guarantees every file the server requires is there.
fs.cpSync(path.join(root, ".next", "server"), path.join(out, ".next", "server"), { recursive: true })

let files = 0, bytes = 0
const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else { files++; bytes += fs.statSync(p).size } } }
walk(out)
console.log(`standalone: ${files} files, ${(bytes / 1048576).toFixed(0)} MB`)
