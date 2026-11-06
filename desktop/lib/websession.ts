// The website session for the coding lane's model calls (2026-11-06). kell's model API (/v1/models, /v1/chat/completions,
// /v1/billing/wallet) takes only a website session; the coding sign-in (kell login, ~/.stealthlab/config.json) is an MCP-app token,
// refused there on purpose (backend authn.supabase_token_allowed). The app already holds a website session: the Rooms sign-in.
// The Electron shell reads its current token from the rooms view and hands it out on a loopback address guarded by a per-run secret
// (electron/main.ts, electron/rooms.ts roomsAccessToken). Nobody copies or pastes anything, and the token is never written to disk.
//
// The engine never holds it: its model provider is this app's own /api/gateway route, which adds the current token to each call,
// so a session that refreshes every hour does not break a long coding session.

let cached: { token: string; until: number } | null = null

const expiry = (jwt: string): number => {
  try { return Number(JSON.parse(Buffer.from(jwt.split(".")[1], "base64url").toString("utf8")).exp || 0) * 1000 } catch { return 0 }
}

/** The current website-session token, or null (the rooms are not signed in, or this is not the desktop app). */
export async function webToken(): Promise<string | null> {
  if (cached && cached.until > Date.now()) return cached.token
  const url = process.env.KELL_WEB_TOKEN_URL
  const secret = process.env.KELL_WEB_TOKEN_SECRET
  if (!url || !secret) return null
  try {
    const r = await fetch(url, { headers: { "x-kell-secret": secret }, signal: AbortSignal.timeout(30_000) })
    const token = r.ok ? String((await r.json())?.token || "") : ""
    if (!token) { cached = null; return null }
    const exp = expiry(token)
    // Reused until a minute before it expires, and for at most five minutes, so a sign-out in the rooms is noticed soon.
    cached = { token, until: Math.min(Date.now() + 300_000, (exp || Date.now() + 120_000) - 60_000) }
    return token
  } catch { return null }
}

export function forgetWebToken() { cached = null }

/** The message a model call without a website session gets. The page shows the sign-in buttons for it. */
export const SIGN_IN_TO_RUN = "Sign in to run models on your kell plan."
