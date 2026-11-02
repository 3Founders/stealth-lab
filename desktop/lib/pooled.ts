// The organization lane for the desktop engine (docs/setup.md, docs/key_management.md).
//
// When this machine has run `kell join`, ~/.stealthlab/exec.json holds one profile per model with key_from
// "stealthlab" and the company's gateway address. Those profiles carry no provider key: the request goes to the
// gateway with the person's own kell sign-in as the bearer, and the company's stored key pays, under its policy,
// budget and ledger. This module turns that into the inline OpenCode config the engine starts with, so coding uses
// the same pooled keys as the website's /chat lane and nothing provider-shaped is on the machine.
//
// It returns null when there is no joined profile (or no sign-in), and the engine then keeps its previous behaviour
// (OpenCode's own global config, e.g. a developer-held key lane).
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

type Profile = {
  model_id?: string
  key_from?: string
  openai_base_url?: string
  input_per_mtok?: number
  output_per_mtok?: number
}

export type PooledEngine = { content: string; key: string }

const readJson = (file: string): any => {
  try { return JSON.parse(fs.readFileSync(file, "utf8")) } catch { return null }
}

const price = (p: Profile): number =>
  typeof p.input_per_mtok === "number" && typeof p.output_per_mtok === "number" ? p.input_per_mtok + p.output_per_mtok : Infinity

/** The inline config and the bearer, or null when this machine is not on an organization gateway. */
export function pooledEngineConfig(env: NodeJS.ProcessEnv = process.env): PooledEngine | null {
  const home = env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
  const exec = readJson(path.join(home, "exec.json"))
  const cfg = readJson(path.join(home, "config.json"))
  const token: string = cfg?.token || ""
  const all = Object.values(exec?.profiles || {}) as Profile[]
  const profiles = all.filter((p) => p?.key_from === "stealthlab" && p?.openai_base_url && p?.model_id)
  if (!token || !profiles.length) return null

  // One provider per gateway address; `kell join` writes one address for all of them.
  const baseUrl = profiles[0].openai_base_url!
  const same = profiles.filter((p) => p.openai_base_url === baseUrl)
  const sorted = [...same].sort((a, b) => price(a) - price(b))
  const cheap = sorted[0]
  const strong = [...sorted].reverse().find((p) => Number.isFinite(price(p))) || sorted[sorted.length - 1]

  const models: Record<string, unknown> = {}
  for (const p of same) {
    models[p.model_id!] = {
      name: p.model_id,
      ...(Number.isFinite(price(p)) ? { cost: { input: p.input_per_mtok, output: p.output_per_mtok } } : {}),
    }
  }

  const config: any = {
    $schema: "https://opencode.ai/v2/config.json",
    provider: {
      kell: {
        npm: "@ai-sdk/openai-compatible",
        name: "kell",
        options: {
          baseURL: baseUrl,
          apiKey: "{env:OPEN_MODEL_API_KEY}",       // the bearer is in the child's environment, not in this text
          headers: {
            ...(exec?.organization_id ? { "x-stealthlab-org": String(exec.organization_id) } : {}),
            // The conservative class: an organization that allows private material accepts it; the public-only pilot
            // refuses it with a message instead of silently widening the policy.
            "x-stealthlab-data-class": env.KELL_OC_DATA_CLASS || "USER_PRIVATE",
          },
        },
        models,
      },
    },
    model: `kell/${strong.model_id}`,
  }

  // Keep kell's plugin and MCP server when `kell setup --client opencode` installed them, so the app gets the same
  // triage/routing and room tools as the CLI. The inline config replaces the global file for this child only.
  const pluginDir = path.join(home, "opencode", "plugin")
  if (fs.existsSync(path.join(pluginDir, "index.mjs"))) config.plugin = [pluginDir.replace(/\\/g, "/")]
  if (cfg?.url) {
    config.mcp = { servers: { kell: { type: "remote", url: cfg.url, codemode: false, headers: { Authorization: `Bearer ${token}` } } } }
  }

  return { content: JSON.stringify(config), key: token }
}
