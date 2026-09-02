/**
 * Frontend identity. The backend validates RS256 OIDC bearer tokens against
 * its configured issuer's JWKS (app/services/authn.py) and, in the public
 * posture only, accepts an X-Viewer-Id header. There is no backend login
 * endpoint by design, so the browser performs Authorization Code + PKCE
 * directly against the OIDC provider; the dev fallback sends X-Viewer-Id.
 *
 * Tokens live in sessionStorage: short-lived, per-tab, nothing sensitive
 * persisted long-term. The backend is the only token validator.
 */

const AUTH_KEY = "stealth.auth";

export interface AuthState {
  mode: "oidc" | "viewer";
  /** OIDC access token (mode=oidc) or viewer id (mode=viewer). */
  token: string;
  /** Unix ms expiry for OIDC tokens; undefined for viewer mode. */
  expiresAt?: number;
  email?: string;
  name?: string;
}

export function getAuth(): AuthState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const auth = JSON.parse(raw) as AuthState;
    if (auth.mode === "oidc" && auth.expiresAt && auth.expiresAt < Date.now()) {
      signOut();
      return null;
    }
    return auth;
  } catch {
    return null;
  }
}

export function setAuth(auth: AuthState): void {
  sessionStorage.setItem(AUTH_KEY, JSON.stringify(auth));
}

export function signOut(): void {
  sessionStorage.removeItem(AUTH_KEY);
}

/** Headers the API client should attach for identity. */
export function authHeaders(): Record<string, string> {
  const auth = getAuth();
  if (!auth) return {};
  return auth.mode === "oidc"
    ? { Authorization: `Bearer ${auth.token}` }
    : { "X-Viewer-Id": auth.token };
}

// ---------------------------------------------------------------------------
// OIDC config (build-time via NEXT_PUBLIC_* env).
// ---------------------------------------------------------------------------

export const OIDC_AUTHORITY = process.env.NEXT_PUBLIC_OIDC_AUTHORITY ?? "";
export const OIDC_CLIENT_ID = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? "";

export function isOidcConfigured(): boolean {
  return Boolean(OIDC_AUTHORITY && OIDC_CLIENT_ID);
}

// ---------------------------------------------------------------------------
// PKCE (RFC 7636) — Authorization Code flow with S256, all client-side.
// Provider-agnostic via /.well-known/openid-configuration discovery
// (Google, Auth0, Keycloak, WorkOS, ...).
// ---------------------------------------------------------------------------

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function sha256(input: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input)
  );
  return new Uint8Array(digest);
}

interface PendingFlow {
  verifier: string;
  state: string;
  createdAt: number;
}

const PENDING_KEY = "stealth.oidc.pending";

export async function beginOidcSignIn(): Promise<void> {
  if (!isOidcConfigured()) throw new Error("OIDC is not configured");
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(await sha256(verifier));
  const state = base64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem(
    PENDING_KEY,
    JSON.stringify({ verifier, state, createdAt: Date.now() } satisfies PendingFlow)
  );

  const discovery = await fetch(
    `${OIDC_AUTHORITY}/.well-known/openid-configuration`
  );
  if (!discovery.ok) throw new Error("OIDC discovery failed");
  const doc = (await discovery.json()) as { authorization_endpoint: string };

  const url = new URL(doc.authorization_endpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", OIDC_CLIENT_ID);
  url.searchParams.set("redirect_uri", `${window.location.origin}/auth`);
  url.searchParams.set("scope", "openid profile email");
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  window.location.assign(url.toString());
}

/**
 * Handle the redirect back to /auth?code=...&state=...
 * Exchanges the code at the provider's token endpoint (public client, PKCE,
 * no client secret — the backend never sees the provider, only the token).
 */
export async function completeOidcSignInIfPending(): Promise<AuthState | null> {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  if (!code || !state) return null;

  const raw = sessionStorage.getItem(PENDING_KEY);
  window.history.replaceState(null, "", "/auth");
  if (!raw) throw new Error("No sign-in flow in progress");
  sessionStorage.removeItem(PENDING_KEY);
  const pending = JSON.parse(raw) as PendingFlow;
  if (pending.state !== state) throw new Error("State mismatch");
  if (Date.now() - pending.createdAt > 10 * 60_000)
    throw new Error("Sign-in flow expired; try again");

  const discovery = await fetch(
    `${OIDC_AUTHORITY}/.well-known/openid-configuration`
  );
  const doc = (await discovery.json()) as { token_endpoint: string };

  const res = await fetch(doc.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: `${window.location.origin}/auth`,
      client_id: OIDC_CLIENT_ID,
      code_verifier: pending.verifier,
    }),
  });
  if (!res.ok) throw new Error("Token exchange failed");
  const tokens = (await res.json()) as {
    access_token: string;
    expires_in: number;
    id_token?: string;
  };

  const auth: AuthState = {
    mode: "oidc",
    token: tokens.access_token,
    expiresAt: Date.now() + tokens.expires_in * 1000,
  };
  // Best-effort display profile from the id_token payload.
  if (tokens.id_token) {
    try {
      const payload = JSON.parse(
        atob(tokens.id_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))
      ) as { email?: string; name?: string };
      auth.email = payload.email;
      auth.name = payload.name;
    } catch {
      // display-only; ignore malformed id_token
    }
  }
  setAuth(auth);
  return auth;
}

/** Dev fallback: unverified viewer identity, valid only in the public posture. */
export function signInAsViewer(id: string): AuthState {
  const trimmed = id.trim().slice(0, 100);
  if (!trimmed) throw new Error("Viewer id required");
  const auth: AuthState = { mode: "viewer", token: trimmed };
  setAuth(auth);
  return auth;
}
