// Copy the kell CLI (packaging/npm) into the desktop app's resources at build time.
//
// Why: the installer must work on a machine that has never installed kell. Sign-in and setup run the CLI, and until
// now the app called `kell` from PATH, which a fresh machine does not have. The CLI has no runtime dependencies of its
// own (kell-mcp keeps none), so a copy of bin/, lib/ and its package.json is the whole program.
//
// The copy is generated (desktop/vendor/ is git-ignored) and unpacked from the asar so a child process can execute it.
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const src = path.resolve(here, "..", "..", "packaging", "npm")
const dst = path.resolve(here, "..", "vendor", "kell-mcp")

const copyDir = (from, to) => {
  fs.mkdirSync(to, { recursive: true })
  for (const e of fs.readdirSync(from, { withFileTypes: true })) {
    const a = path.join(from, e.name)
    const b = path.join(to, e.name)
    if (e.isDirectory()) copyDir(a, b)
    else fs.copyFileSync(a, b)
  }
}

fs.rmSync(dst, { recursive: true, force: true })
for (const name of ["bin", "lib", "install"]) {
  const from = path.join(src, name)
  if (fs.existsSync(from)) copyDir(from, path.join(dst, name))
}
for (const name of ["package.json", "README.md"]) {
  const from = path.join(src, name)
  if (fs.existsSync(from)) fs.copyFileSync(from, path.join(dst, name))
}

const entry = path.join(dst, "bin", "kell.mjs")
if (!fs.existsSync(entry)) {
  console.error(`bundle-cli: ${entry} is missing; packaging/npm changed shape`)
  process.exit(1)
}
console.log(`bundle-cli: ${path.relative(path.resolve(here, ".."), dst)} (${fs.readdirSync(path.join(dst, "lib")).length} lib entries)`)
