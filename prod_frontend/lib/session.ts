/**
 * Minimal session/token handling for the real, authenticated contribution
 * flows (submissions, usage events, Credits, Standing). keळ deployments
 * identify people through an external OIDC provider (see /sign-in) — this
 * app never collects a password. Once that provider redirects back with a
 * token, it is expected to land here under ACCESS_TOKEN_KEY; nothing in
 * this codebase runs that redirect yet (NEXT_PUBLIC_KEL_SIGNIN_URL is
 * unset in this build), so today `getAccessToken()` honestly returns null
 * and every authenticated call degrades to the same "sign in first" state
 * every other gated action in this app already shows.
 *
 * The token itself is never sent anywhere but this deployment's own
 * backend (see lib/kel-api.ts's authedFetch). Decoding it client-side
 * (below) is for DISPLAY/URL-building only — e.g. knowing your own
 * contributor_id to link to your own Credits page — never for
 * authorization: the server independently verifies the token on every
 * request (app/api/deps.py::require_authenticated_user) and never trusts
 * anything the client derived from it.
 */
"use client";

const ACCESS_TOKEN_KEY = "kel_access_token";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAccessToken(token: string): void {
  try {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, token);
  } catch {
    /* private mode / blocked storage — session simply won't persist */
  }
}

export function clearAccessToken(): void {
  try {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Non-authoritative: display/URL-building only. See module docstring. */
export function decodeJwtSubject(token: string): string | null {
  try {
    const [, payload] = token.split(".");
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as Record<string, unknown>;
    const sub = claims.sub;
    return typeof sub === "string" && sub ? sub : null;
  } catch {
    return null;
  }
}

export interface Session {
  token: string;
  subject: string;
}

/** Best-effort current session from the stored token. null when signed out
 * or the stored token doesn't even parse — never fabricated. */
export function getSession(): Session | null {
  const token = getAccessToken();
  if (!token) return null;
  const subject = decodeJwtSubject(token);
  if (!subject) return null;
  return { token, subject };
}
