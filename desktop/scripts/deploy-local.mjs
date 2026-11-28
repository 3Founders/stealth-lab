// Update only this machine's installed kell from the local build.
//
// No installer, no GitHub release, no setup path: stop kell, copy release/win-unpacked over the
// installed app, start it again. User data (%APPDATA%\kell-desktop and ~/.stealthlab) is untouched.
//
//   npm run deploy:local
//
// The first install still needs the installer; after that this updates it in place. Set
// KELL_INSTALL_DIR to override the default %LOCALAPPDATA%\Programs\kell-desktop.
import { spawn, spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, "..")
const src = path.join(root, "release", "win-unpacked")
const target = process.env.KELL_INSTALL_DIR || path.join(process.env.LOCALAPPDATA || "", "Programs", "kell-desktop")
const exe = path.join(target, "kell.exe")
const appPkg = path.join(target, "resources", "app", "package.json")
const mainJs = path.join(target, "resources", "app", "electron", "dist", "main.js")

const run = (cmd, args) => spawnSync(cmd, args, { stdio: "inherit" })

if (process.platform !== "win32") {
  console.error("deploy:local is Windows-only for now.")
  process.exit(1)
}
if (!fs.existsSync(src)) {
  console.error(`No build at ${src}. Run "npm run build" and "npm run electron:build" first.`)
  process.exit(1)
}
if (!fs.existsSync(exe)) {
  console.error(`kell is not installed at ${target}. Install it once with the installer; after that this updates it in place.`)
  process.exit(1)
}

console.log(`stopping kell (${exe})`)
run("taskkill", ["/IM", "kell.exe", "/T", "/F"])
await new Promise((r) => setTimeout(r, 1500))     // let the file handles go

// The app payload is replaced whole. robocopy alone leaves files the new build no longer has (an old page chunk
// stayed behind and the server kept serving it from its route cache), so remove resources/app first. User data
// lives in %APPDATA%, never here.
const payload = path.join(target, "resources", "app")
if (fs.existsSync(payload)) {
  console.log(`removing the old app payload (${payload})`)
  fs.rmSync(payload, { recursive: true, force: true })
}

console.log(`copying ${src} -> ${target}`)
const copy = run("robocopy", [src, target, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:2", "/W:2"])
// robocopy: 0-7 are success (0 no change, 1 copied, 2 extra, 3 copied+extra, ...); 8+ is an error.
if (copy.status >= 8) {
  console.error(`robocopy failed with ${copy.status}. Close kell and try again.`)
  process.exit(copy.status || 1)
}
if (!fs.existsSync(mainJs)) {
  console.error(`The copy is incomplete: ${mainJs} is missing. Run "npm run build" and try again.`)
  process.exit(1)
}

let version = "unknown"
try { version = JSON.parse(fs.readFileSync(appPkg, "utf8")).version || version } catch { /* keep unknown */ }
console.log(`installed kell ${version}`)

console.log(`starting ${exe}`)
const child = spawn(exe, [], { detached: true, stdio: "ignore", windowsHide: false })
child.unref()
await new Promise((r) => setTimeout(r, 2500))

const list = spawnSync("tasklist", ["/FI", "IMAGENAME eq kell.exe", "/NH"], { encoding: "utf8" })
if (!/kell\.exe/i.test(list.stdout || "")) {
  console.error("kell did not stay running. Start it from the Start menu and check %APPDATA%\\kell-desktop\\logs.")
  process.exit(1)
}
console.log("kell is running the local build.")
