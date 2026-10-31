// Writes opencode.json into a fixture directory: the General Compute provider (the key comes from the environment, never
// from this file) and this plugin by absolute path.
// usage: node with_opencode.mjs <dir> [model]
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const [dir, model = "gc/gemma-4-31B-it"] = process.argv.slice(2)
if (!dir) { console.error("usage: node with_opencode.mjs <dir> [model]"); process.exit(1) }
const base = JSON.parse(fs.readFileSync(path.join(here, "sandbox", "opencode.json"), "utf8"))
base.model = model
base.plugin = [path.join(here, "plugin").replace(/\\/g, "/")]
fs.writeFileSync(path.join(dir, "opencode.json"), JSON.stringify(base, null, 2))
