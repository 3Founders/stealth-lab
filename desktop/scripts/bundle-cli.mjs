// Copy the kell CLI (packaging/npm) into the desktop app's resources at build time.
//
// Why: the installer must work on a machine that has never installed kell. Sign-in and setup run the CLI, and until
// now the app called `kell` from PATH, which a fresh machine does not have. The CLI has no runtime dependencies of its
// own (kell-mcp keeps none), so a copy of bin/, lib/ and its package.json is the whole program.
//
// The copy is generated (desktop/vendor/ is git-ignored) and unpacked from the asar so a child process can execute it.
import { spawnSync } from "node:child_process"
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

// kell's OpenCode plugin (packaging/npm/lib/opencode), ready to load: the engine runs kell's hooks through it on every prompt -- the
// find_ways lookup with .kell/ (claims, library), the survey that builds .kell/, the capture hooks that record outcomes and grow the
// library -- the same flow as Claude Code's hooks, and its native layer (the model per prompt, escalation, provider fallback). Built
// here so a new install needs nothing run after it (owner, 2026-11-08); `kell setup --client opencode` used to be the only way.
// One esbuild bundle with @opencode/plugin inside: the package installs 156 MB of dependencies (provider SDKs, npm's own tooling),
// the plugin uses a few of them. The package is installed once into a build folder outside vendor/ and kept between builds.
const PLUGIN_VERSION = "2.2.5"            // the engine's version (package.json @opencode/cli); lib/opencode_setup.mjs OPENCODE_VERSION
const build = path.resolve(here, "..", ".plugin-build")
const plug = path.resolve(here, "..", "vendor", "kell-opencode")
const have = (() => {
  try { return JSON.parse(fs.readFileSync(path.join(build, "node_modules", "@opencode", "plugin", "package.json"), "utf8")).version } catch { return null }
})()
if (have !== PLUGIN_VERSION) {
  fs.rmSync(build, { recursive: true, force: true })
  fs.mkdirSync(build, { recursive: true })
  fs.writeFileSync(path.join(build, "package.json"), JSON.stringify({ name: "kell-plugin-build", private: true, type: "module",
    dependencies: { "@opencode/plugin": PLUGIN_VERSION } }, null, 2) + "\n")
  // --legacy-peer-deps: the package lists UI hosts (vite, electron, solid) as peers, which a plugin never loads
  const r = spawnSync("npm install --no-audit --no-fund --omit=dev --legacy-peer-deps --no-package-lock",
    { cwd: build, encoding: "utf8", shell: true, timeout: 600_000 })
  if (r.status !== 0) {
    console.error(`bundle-cli: npm install in ${build} failed: ${String(r.stderr || r.stdout).slice(-400)}`)
    process.exit(1)
  }
}
// the plugin's sources sit in the build folder so the bundler finds @opencode/plugin from them; lib/exec comes along (the hook
// runner imports it by a relative path)
fs.rmSync(path.join(build, "src"), { recursive: true, force: true })
copyDir(path.join(src, "lib", "opencode"), path.join(build, "src", "opencode"))
copyDir(path.join(src, "lib", "exec"), path.join(build, "src", "exec"))
fs.rmSync(plug, { recursive: true, force: true })
fs.mkdirSync(plug, { recursive: true })
const { build: esbuild } = await import("esbuild")
await esbuild({
  entryPoints: [path.join(build, "src", "opencode", "index.mjs")], outfile: path.join(plug, "index.mjs"),
  bundle: true, platform: "node", format: "esm", target: "node20", logLevel: "warning",
  // a bundle with CommonJS inside needs require() in ESM
  banner: { js: 'import { createRequire as __kellRequire } from "node:module"; const require = __kellRequire(import.meta.url);' },
})
fs.writeFileSync(path.join(plug, "package.json"), JSON.stringify({ name: "kell-opencode-plugin", private: true, type: "module",
  main: "index.mjs" }, null, 2) + "\n")
console.log(`bundle-cli: ${path.relative(path.resolve(here, ".."), plug)} (@opencode/plugin ${PLUGIN_VERSION}, ${Math.round(fs.statSync(path.join(plug, "index.mjs")).size / 1024)} KB)`)
