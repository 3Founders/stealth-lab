// The kell sign-in in ~/.stealthlab/config.json, kept fresh: the same refresh the CLI does (packaging/npm/lib/oauth.mjs freshToken).
// The app only ever read the saved access token, so once it expired every call answered 401 while the app still said "signed in"
// (2026-11-06: the tool router answered 401, and the personal lane would have refused the sign-in). Any failure leaves the saved
// token as it was: the call that follows then says what is wrong.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const REFRESH_SKEW_MS = 120_000                    // refresh when the access token has under two minutes left

const configPath = (env: NodeJS.ProcessEnv) => path.join(env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "config.json")

let inFlight: Promise<void> | null = null

/** Refreshes the saved sign-in when it is about to expire (one refresh at a time); never throws. */
export function freshSignIn(env: NodeJS.ProcessEnv = process.env, fetchImpl: typeof fetch = fetch): Promise<void> {
  if (!inFlight) inFlight = refresh(env, fetchImpl).finally(() => { inFlight = null })
  return inFlight
}

async function refresh(env: NodeJS.ProcessEnv, fetchImpl: typeof fetch): Promise<void> {
  try {
    const p = configPath(env)
    const cfg = JSON.parse(fs.readFileSync(p, "utf8")) || {}
    if (!cfg.refresh_token || !cfg.oauth?.token_endpoint || !cfg.oauth?.client_id) return
    if (cfg.expires_at && cfg.expires_at - REFRESH_SKEW_MS > Date.now()) return
    const res = await fetchImpl(cfg.oauth.token_endpoint, {
      method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
      signal: AbortSignal.timeout(15_000),
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: cfg.refresh_token, client_id: cfg.oauth.client_id }).toString(),
    })
    const body: any = await res.json().catch(() => ({}))
    if (!res.ok || !body?.access_token) return
    const next = { ...JSON.parse(fs.readFileSync(p, "utf8")), token: body.access_token,
                   refresh_token: body.refresh_token || cfg.refresh_token,
                   expires_at: body.expires_in ? Date.now() + Number(body.expires_in) * 1000 : undefined }
    if (next.expires_at === undefined) delete next.expires_at
    fs.writeFileSync(p, JSON.stringify(next, null, 2) + "\n", { mode: 0o600 })
  } catch { /* the saved token stands */ }
}
