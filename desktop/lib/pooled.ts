// The gateway lanes for the desktop engine (docs/setup.md, docs/key_management.md, docs/billing.md).
//
// Both lanes send every model call to kell's gateway with the person's own kell sign-in as the bearer, and no provider key
// is ever on the machine, in the UI, in a log or in the installer:
//
//   organization  `kell join` wrote ~/.stealthlab/exec.json with one profile per model (key_from "stealthlab") and the
//                 company's gateway address; the company's stored key pays, under its policy, budget and ledger.
//   personal      a signed-in person in no team: the gateway charges their own wallet (Starter, Pro, Plus or a top-up).
//                 The model list comes from the gateway's GET /v1/models, the way `kell join` gets a team's list from
//                 /v1/join, so the app never hard-codes which models a plan can call.
//
// Each returns the inline OpenCode config the engine starts with. With neither, the engine keeps its previous behaviour
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

type Model = { id: string; input?: number; output?: number }

export type PooledEngine = { content: string; key: string }

const readJson = (file: string): any => {
  try { return JSON.parse(fs.readFileSync(file, "utf8")) } catch { return null }
}

const priceOf = (m: Model): number =>
  typeof m.input === "number" && typeof m.output === "number" ? m.input + m.output : Infinity

/** The default operator when the gateway offers it: GLM 5.3 Flash starts fast; the priciest model stays the fallback. */
export const DEFAULT_MODEL = "glm-5.3-flash"

const home = (env: NodeJS.ProcessEnv) => env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")

/** The one engine config both lanes share: kell's gateway as an OpenAI-compatible provider, the bearer in the child's
 *  environment (never in this text), GLM 5.3 Flash as the default when the gateway offers it, the priciest model as the
 *  provider-failure fallback and the cheapest as the cheap rung. */
function buildConfig(models: Model[], baseUrl: string, token: string, extraHeaders: Record<string, string>,
                     cfg: any, env: NodeJS.ProcessEnv): PooledEngine {
  const sorted = [...models].sort((a, b) => priceOf(a) - priceOf(b))
  const cheap = sorted[0]
  const priciest = [...sorted].reverse().find((m) => Number.isFinite(priceOf(m))) || sorted[sorted.length - 1]
  // GLM 5.3 Flash is the default operator when the gateway offers it; the priciest model is the provider-failure fallback,
  // so a dead flash endpoint still has a stronger rung to move to. Not plain GLM 5.3.
  const strong = models.find((m) => m.id === DEFAULT_MODEL) || priciest
  const fallback = strong.id === priciest.id ? cheap : priciest

  const table: Record<string, unknown> = {}
  for (const m of models) {
    table[m.id] = { name: m.id, ...(Number.isFinite(priceOf(m)) ? { cost: { input: m.input, output: m.output } } : {}) }
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
            ...extraHeaders,
            // The conservative class: an organization that allows private material accepts it; the public-only pilot
            // refuses it with a message instead of silently widening the policy.
            "x-stealthlab-data-class": env.KELL_OC_DATA_CLASS || "USER_PRIVATE",
            // A dead or out-of-credit provider on the chosen model does not fail the turn: the gateway tries the others.
            "x-stealthlab-fallback-models": [...new Set([strong.id, fallback.id, cheap.id])].join(","),
          },
        },
        models: table,
      },
    },
    model: `kell/${strong.id}`,
  }

  // Keep kell's plugin and MCP server when `kell setup --client opencode` installed them, so the app gets the same
  // triage/routing and room tools as the CLI. The inline config replaces the global file for this child only.
  const pluginDir = path.join(home(env), "opencode", "plugin")
  if (fs.existsSync(path.join(pluginDir, "index.mjs"))) config.plugin = [pluginDir.replace(/\\/g, "/")]
  if (cfg?.url) {
    config.mcp = { servers: { kell: { type: "remote", url: cfg.url, codemode: false, headers: { Authorization: `Bearer ${token}` } } } }
  }
  return { content: JSON.stringify(config), key: token }
}

/** The organization lane: the inline config and the bearer, or null when this machine is not on an organization gateway. */
export function pooledEngineConfig(env: NodeJS.ProcessEnv = process.env): PooledEngine | null {
  const h = home(env)
  const exec = readJson(path.join(h, "exec.json"))
  const cfg = readJson(path.join(h, "config.json"))
  const token: string = cfg?.token || ""
  const all = Object.values(exec?.profiles || {}) as Profile[]
  const profiles = all.filter((p) => p?.key_from === "stealthlab" && p?.openai_base_url && p?.model_id)
  if (!token || !profiles.length) return null

  // One provider per gateway address; `kell join` writes one address for all of them.
  const baseUrl = profiles[0].openai_base_url!
  const same = profiles.filter((p) => p.openai_base_url === baseUrl)
  return buildConfig(same.map((p) => ({ id: p.model_id!, input: p.input_per_mtok, output: p.output_per_mtok })),
    baseUrl, token, exec?.organization_id ? { "x-stealthlab-org": String(exec.organization_id) } : {}, cfg, env)
}

/** True when a developer set up their own provider key for the engine (`kell setup --client opencode` wrote its models). */
export function ownKeyLane(env: NodeJS.ProcessEnv = process.env): boolean {
  const oc = readJson(path.join(home(env), "opencode", "config.json"))
  return Boolean(oc?.cheap || oc?.strong)
}

// ---- the personal lane ------------------------------------------------------------------------------------------------

const DEFAULT_HOST = "https://mcp.kell.org.in"

/** The hosted server's origin: KELL_GATEWAY for a test server, else the saved address (the MCP url minus its path), else kell's. */
export function gatewayOrigin(cfg: any, env: NodeJS.ProcessEnv = process.env): string {
  for (const raw of [env.KELL_GATEWAY, cfg?.url]) {
    try { if (raw) return new URL(String(raw)).origin } catch { /* try the next */ }
  }
  return DEFAULT_HOST
}

const shared = globalThis as typeof globalThis & { __kellPersonalModels?: string[] }
/** Model ids the last personal-lane start found, for the Settings pane (empty before the engine first starts). */
export const personalModelIds = (): string[] => shared.__kellPersonalModels || []

export type Personal =
  | { ok: true; engine: PooledEngine }
  | { ok: false; reason: "not-signed-in" | "no-models" | "unreachable" | "rejected"; message: string }

/** The personal lane: models from the gateway for this sign-in. Never throws; says why when it cannot. */
export async function personalEngineConfig(env: NodeJS.ProcessEnv = process.env, fetchImpl: typeof fetch = fetch): Promise<Personal> {
  const cfg = readJson(path.join(home(env), "config.json"))
  const token: string = cfg?.token || ""
  if (!token) return { ok: false, reason: "not-signed-in", message: "Sign in to use your kell plan." }
  const origin = gatewayOrigin(cfg, env)
  let res: Response
  try {
    res = await fetchImpl(`${origin}/v1/models`, { headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(15_000) })
  } catch {
    return { ok: false, reason: "unreachable", message: "Cannot reach kell right now. Check your connection and try again." }
  }
  if (res.status === 401 || res.status === 403) return { ok: false, reason: "rejected", message: "kell did not accept this sign-in. Sign out and sign in again." }
  if (!res.ok) return { ok: false, reason: "unreachable", message: `kell answered ${res.status}; try again in a moment.` }
  const body: any = await res.json().catch(() => null)
  const models: Model[] = (Array.isArray(body?.data) ? body.data : [])
    .filter((m: any) => typeof m?.id === "string")
    .map((m: any) => ({ id: m.id, input: m.stealthlab?.input_per_mtok, output: m.stealthlab?.output_per_mtok }))
  if (!models.length) return { ok: false, reason: "no-models", message: "No models are available to this account yet." }
  shared.__kellPersonalModels = models.map((m) => m.id)
  return { ok: true, engine: buildConfig(models, `${origin}/v1`, token, {}, cfg, env) }
}

/** Dollars left in the person's own wallet, or null when it cannot be read (then the model call decides). */
export async function walletRemaining(env: NodeJS.ProcessEnv = process.env, fetchImpl: typeof fetch = fetch): Promise<number | null> {
  const cfg = readJson(path.join(home(env), "config.json"))
  if (!cfg?.token) return null
  try {
    const r = await fetchImpl(`${gatewayOrigin(cfg, env)}/v1/billing/wallet`,
      { headers: { authorization: `Bearer ${cfg.token}` }, signal: AbortSignal.timeout(8_000) })
    if (!r.ok) return null
    const n = Number((await r.json())?.remaining_usd)
    return Number.isFinite(n) ? n : null
  } catch { return null }
}

/** True for the gateway's out-of-credit refusal, however the engine words the error around it. */
export const isNeedPlan = (text: string): boolean => /buy credits|prepaid balance|wallet_empty|payment required/i.test(text)

export const PRICING_URL = "https://www.kell.org.in/pricing"
