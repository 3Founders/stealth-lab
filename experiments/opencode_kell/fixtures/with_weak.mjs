// Adds the weak provider to a fixture's opencode.json (after with_opencode.mjs). usage: node with_weak.mjs <dir> <port>
import fs from "node:fs"
import path from "node:path"
const [dir, port = "8799"] = process.argv.slice(2)
const f = path.join(dir, "opencode.json")
const o = JSON.parse(fs.readFileSync(f, "utf8"))
o.provider.weak = { npm: "@ai-sdk/openai-compatible", name: "Weak (test)", options: { baseURL: `http://127.0.0.1:${port}/v1`, apiKey: "none" }, models: { "weak-1": { name: "weak-1" } } }
fs.writeFileSync(f, JSON.stringify(o, null, 2))
