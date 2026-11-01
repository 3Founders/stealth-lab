// The MCP Authorization header `kell setup --client opencode` wrote into OpenCode's opencode.json holds the
// sign-in token from that day; a session can outlive it (the saved token lasts about an hour), after which the
// session's kell MCP tools answer 401 while the hooks keep working (they refresh as packaging/npm/lib/oauth.mjs
// does). Option A from docs/handoff_opencode_agent_and_app_2026-11.md: at every plugin start, ask for a fresh
// token the same way (lib/oauth.mjs freshToken) and rewrite exactly that one header, so the next MCP connect
// (usually the next session) authenticates.
//
// Honest limits: the header an explicit --token / STEALTHLAB_MCP_TOKEN setup wrote is left alone (environment
// tokens win, like the hooks' precedence); when the refresh cannot run (offline, no refresh token) the file is
// left as it is; a rewrite helps connects that happen after it, not one that is already open; no entry that kell
// does not own is touched.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const MCP_NAME = "kell"
const REFRESH_SKEW_MS = 120_000                  // refresh when the access token has under two minutes left (lib/oauth.mjs)
const TOKEN_TIMEOUT_MS = 15_000

// OpenCode's config file: the same resolution as lib/opencode_setup.mjs opencodeConfigFile.
export function opencodeConfigFilePath(env = process.env) {
  const home = env.STEALTHLAB_TEST_HOME || os.homedir()
  const dir = env.OPENCODE_CONFIG_DIR || path.join(env.XDG_CONFIG_HOME || path.join(home, ".config"), "opencode")
  return path.join(dir, "opencode.json")
}

const readJson = (file) => { try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; } }

// kell's saved sign-in state ($STEALTHLAB_HOME/config.json), read and written like lib/config.mjs (0600, nulls out).
const settingsPath = (env) => path.join(env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "config.json")

// kell's plugin settings ($STEALTHLAB_HOME/opencode/config.json by default), where setup lists what it owns. Same
// resolution as index.mjs's ocConfig (KELL_OC_CONFIG for experiments).
const pluginSettingsPath = (env) => env.KELL_OC_CONFIG || path.join(env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "opencode", "config.json")
function saveSettings(patch, env = process.env) {
  const file = settingsPath(env)
  const next = readJson(file) || {}
  Object.assign(next, patch)
  for (const k of Object.keys(next)) if (next[k] === undefined || next[k] === null) delete next[k]
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 })
  fs.writeFileSync(file, JSON.stringify(next, null, 2) + "\n", { mode: 0o600 })
  try { fs.chmodSync(file, 0o600) } catch { /* best effort on Windows */ }
}

// The refresh-token grant, lib/oauth.mjs tokenRequest. Returns null on any failure.
async function refreshGrant(cfg, { env, now, fetchImpl, log = () => {} }) {
  try {
    const res = await fetchImpl(cfg.oauth.token_endpoint, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
      signal: AbortSignal.timeout(TOKEN_TIMEOUT_MS),
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: cfg.refresh_token, client_id: cfg.oauth.client_id }).toString(),
    })
    const text = await res.text()
    let body = {}
    try { body = JSON.parse(text) } catch { /* not JSON */ }
    if (!res.ok || !body.access_token) return null
    const out = { token: body.access_token, refresh_token: body.refresh_token || cfg.refresh_token, expires_at: body.expires_in ? now() + Number(body.expires_in) * 1000 : undefined }
    saveSettings({ token: out.token, refresh_token: out.refresh_token, expires_at: out.expires_at }, env)
    return out
  } catch (e) {
    log("refresh failed:", String(e).slice(0, 100))
    return null
  }
}

/** Aligns OpenCode's opencode.json MCP header with a current token. Never throws and never rewrites more than
 *  that header. `log` collects what happened (the plugin passes its debug logger). -> { changed: boolean,
 *  reason: string, expires_at?: number } (expires_at in epoch ms when known) */
export async function refreshMcpHeader({ env = process.env, now = Date.now, fetchImpl = globalThis.fetch, log = () => {} } = {}) {
  try {
    const cfg = readJson(settingsPath(env)) || {}
    if (!cfg.token && !cfg.refresh_token) return { changed: false, reason: "no-signin" }
    if (env.STEALTHLAB_MCP_TOKEN) return { changed: false, reason: "explicit-env-token" }
    const settings = readJson(pluginSettingsPath(env)) || {}
    const owned = Array.isArray(settings.owned) ? settings.owned : []
    if (!owned.includes("mcp.kell") || !cfg.oauth?.token_endpoint || !cfg.oauth?.client_id) {
      if (!owned.includes("mcp.kell")) return { changed: false, reason: "not-owned" }
      return { changed: false, reason: "no-refresh-state" }
    }

    const ocfg = readJson(opencodeConfigFilePath(env))
    const server = ocfg?.mcp?.servers?.[MCP_NAME]
    if (!server) return { changed: false, reason: "no-kell-entry" }
    const header = server.headers?.Authorization
    if (header && !header.startsWith("Bearer ")) return { changed: false, reason: "custom-header" }

    // A token that still has time left is used as it is (realign when the header fell behind a hook's refresh);
    // an expired or unknown one is refreshed first, which also rotates the pair in kell's config.json.
    const fresh = cfg.token && cfg.expires_at && cfg.expires_at - REFRESH_SKEW_MS > now()
    let token = cfg.token
    let reason, expires_at = cfg.expires_at
    if (fresh) { reason = "realign" }
    else {
      const t = await refreshGrant(cfg, { env, now, fetchImpl, log })
      if (!t) return { changed: false, reason: "refresh-failed" }
      token = t.token
      expires_at = t.expires_at
      reason = "refreshed"
    }
    if (header === `Bearer ${token}`) return { changed: false, reason: "same" }
    const next = { ...ocfg, mcp: { ...ocfg.mcp, servers: { ...ocfg.mcp.servers, [MCP_NAME]: { ...server, headers: { ...(server.headers || {}), Authorization: `Bearer ${token}` } } } } }
    fs.mkdirSync(path.dirname(opencodeConfigFilePath(env)), { recursive: true })
    fs.writeFileSync(opencodeConfigFilePath(env), JSON.stringify(next, null, 2) + "\n")
    const out = { changed: true, reason }
    if (expires_at) out.expires_at = expires_at
    return out
  } catch (e) {
    return { changed: false, reason: `failed (${String(e).slice(0, 80)})` }
  }
}
