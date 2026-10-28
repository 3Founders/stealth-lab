/**
 * "Connect with OpenRouter": the one provider that lets a web app obtain a person's API key through a sign-in flow instead of a
 * paste (OAuth authorization-code with PKCE, https://openrouter.ai/docs/guides/overview/auth/oauth). The person approves on
 * openrouter.ai and we receive a key that they can revoke there. No other provider we support offers this.
 *
 * Handling (OAuth for browser apps): S256 only; a random verifier and a separate random `state`, kept in sessionStorage for this
 * one attempt (they are not secrets once used, and expire after 10 minutes, the life of the code); `state` is compared before
 * anything is exchanged; the code and state are removed from the address bar straight away; the callback page sends no Referer
 * (next.config sets Referrer-Policy: no-referrer for /account/*). The key that comes back goes straight into the vault.
 *
 * The code exchange is a browser request to openrouter.ai. OpenRouter's docs do not promise it allows browser calls; its
 * preflight and a foreign-origin POST were observed to (2026-10-28), so it works today. If that ever changes the failure is
 * reported plainly and pasting a key still works.
 */
export const OPENROUTER_AUTH_URL = "https://openrouter.ai/auth";
export const OPENROUTER_EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys";
export const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";

const STORE_KEY = "kel.openrouter.pkce";
const TTL_MS = 10 * 60 * 1000;

export interface PkceStore { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }

const b64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const random = (n: number) => b64url(crypto.getRandomValues(new Uint8Array(n)));

/** S256 code challenge for a verifier (RFC 7636): base64url(SHA-256(verifier)). */
export async function challengeFor(verifier: string): Promise<string> {
  return b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
}

/** Starts a connection: remembers the verifier and state for this attempt, and returns the openrouter.ai address to send the person to. */
export async function beginConnect(callbackUrl: string, store: PkceStore, now = Date.now()): Promise<string> {
  const verifier = random(48); // 64 characters, within RFC 7636's 43-128
  const state = random(24);
  store.setItem(STORE_KEY, JSON.stringify({ verifier, state, t: now }));
  const u = new URL(OPENROUTER_AUTH_URL);
  u.searchParams.set("callback_url", callbackUrl);
  u.searchParams.set("code_challenge", await challengeFor(verifier));
  u.searchParams.set("code_challenge_method", "S256");
  u.searchParams.set("state", state);
  return u.toString();
}

export type ConnectResult = { ok: true; key: string } | { ok: false; message: string };

/** Finishes a connection from the callback's code and state. The attempt is single use whatever the outcome. */
export async function finishConnect(
  code: string, state: string, store: PkceStore, fetchImpl: typeof fetch = fetch, now = Date.now(),
): Promise<ConnectResult> {
  let saved: { verifier?: string; state?: string; t?: number } = {};
  try { saved = JSON.parse(store.getItem(STORE_KEY) ?? "{}"); } catch { /* treated as missing */ }
  store.removeItem(STORE_KEY);
  if (!saved.verifier || !saved.state || typeof saved.t !== "number") return { ok: false, message: "This connection attempt was not started here, or was already used. Start it again." };
  if (now - saved.t > TTL_MS) return { ok: false, message: "That connection attempt took too long (over 10 minutes). Start it again." };
  if (!code || !state || state !== saved.state) return { ok: false, message: "The connection could not be confirmed (the check value did not match). Start it again." };
  try {
    const res = await fetchImpl(OPENROUTER_EXCHANGE_URL, {
      method: "POST", headers: { "content-type": "application/json" }, credentials: "omit", referrerPolicy: "no-referrer",
      body: JSON.stringify({ code, code_verifier: saved.verifier, code_challenge_method: "S256" }),
    });
    if (!res.ok) return { ok: false, message: `OpenRouter did not accept the connection (${res.status}). Start it again.` };
    const json = (await res.json()) as { key?: unknown };
    return typeof json.key === "string" && json.key ? { ok: true, key: json.key } : { ok: false, message: "OpenRouter did not return a key. Start it again." };
  } catch {
    return { ok: false, message: "The browser could not reach OpenRouter to finish the connection. Paste a key instead, or try again." };
  }
}
