/**
 * Session handling for the real, authenticated contribution flows
 * (submissions, usage events, Credits, Standing, profile/avatar).
 *
 * V1 identity: Supabase Auth (Google, GitHub, email+password) is the
 * browser-side session mechanism (see lib/supabase.ts) — this module
 * wraps its supported session APIs rather than reimplementing token
 * storage. The backend independently verifies every access token itself
 * (app/services/authn.py's Supabase Auth preset); nothing decoded here is
 * ever treated as authorization, only as a DISPLAY/URL-building
 * convenience (e.g. knowing whether to show "Sign in" or the account
 * menu before the profile API has answered).
 */
"use client";

import { getSupabase, supabaseConfigured } from "@/lib/supabase";

export interface Session {
  token: string;
  subject: string;
}

/** The current access token, or null when signed out / Supabase isn't
 * configured for this deployment. Async because Supabase's own session
 * read is async (it may need to refresh a near-expired token). */
export async function getAccessToken(): Promise<string | null> {
  const client = getSupabase();
  if (!client) return null;
  try {
    const { data } = await client.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

/** Best-effort current session. null when signed out, misconfigured, or
 * the client has no session yet — never fabricated. */
export async function getSession(): Promise<Session | null> {
  const client = getSupabase();
  if (!client) return null;
  try {
    const { data } = await client.auth.getSession();
    const session = data.session;
    if (!session?.access_token || !session.user?.id) return null;
    return { token: session.access_token, subject: session.user.id };
  } catch {
    return null;
  }
}

export async function signOut(): Promise<void> {
  const client = getSupabase();
  if (!client) return;
  await client.auth.signOut();
}

export { supabaseConfigured };

/** Subscribe to sign-in/sign-out. Returns an unsubscribe function (or a
 * no-op when Supabase isn't configured). */
export function onSessionChange(cb: (session: Session | null) => void): () => void {
  const client = getSupabase();
  if (!client) return () => {};
  const { data } = client.auth.onAuthStateChange((_event, session) => {
    if (session?.access_token && session.user?.id) {
      cb({ token: session.access_token, subject: session.user.id });
    } else {
      cb(null);
    }
  });
  return () => data.subscription.unsubscribe();
}

// --- safe post-auth redirect target ---------------------------------------

const REDIRECT_KEY = "kel_post_auth_redirect";

/** Only same-origin, path-shaped destinations are ever honoured — never an
 * absolute URL or protocol-relative one (open-redirect guard). */
export function isSafeRedirectPath(path: string | null | undefined): path is string {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//") && !path.includes("://");
}

export function setPostAuthRedirect(path: string): void {
  if (!isSafeRedirectPath(path)) return;
  try {
    window.sessionStorage.setItem(REDIRECT_KEY, path);
  } catch {
    /* private mode / blocked storage — falls back to the default destination */
  }
}

export function consumePostAuthRedirect(): string {
  try {
    const path = window.sessionStorage.getItem(REDIRECT_KEY);
    window.sessionStorage.removeItem(REDIRECT_KEY);
    return isSafeRedirectPath(path) ? path : "/";
  } catch {
    return "/";
  }
}
