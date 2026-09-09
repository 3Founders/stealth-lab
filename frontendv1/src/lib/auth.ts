/**
 * Frontend identity, backed by Supabase Auth (launch compliance Phase 1).
 *
 * Supabase issues the session; the StealthLab backend is the only token
 * validator — it verifies "Authorization: Bearer <access_token>" against the
 * project's JWKS (app/services/authn.py, ES256/RS256). The access token is
 * kept in a module variable, refreshed transparently by supabase-js and
 * mirrored here via onAuthStateChange, so `authHeaders()` stays synchronous
 * for the API client and a reload restores the signed-in state.
 *
 * Dev fallback: when Supabase is not configured (local public posture),
 * `signInAsViewer` sends an unverified X-Viewer-Id — accepted by the backend
 * only while nothing is private.
 */
import type { Session } from "@supabase/supabase-js";

import { getSupabase, isSupabaseConfigured } from "@/lib/supabase/client";

export { isSupabaseConfigured };

const VIEWER_KEY = "stealth.viewer";

export interface AuthState {
  mode: "supabase" | "viewer";
  /** OIDC access token (mode=supabase) or viewer id (mode=viewer). */
  token: string;
  expiresAt?: number;
  email?: string;
  name?: string;
}

// --- live session mirror -------------------------------------------------

let _session: Session | null = null;
let _ready = false;
const _listeners = new Set<() => void>();

function _emit() {
  for (const l of _listeners) l();
}

/** Subscribe to auth-state changes (sign in / out / token refresh). */
export function onAuthChange(cb: () => void): () => void {
  _listeners.add(cb);
  return () => _listeners.delete(cb);
}

/** Resolves once the initial session has been read from storage. */
export function authReady(): Promise<void> {
  if (_ready || typeof window === "undefined") return Promise.resolve();
  return new Promise((resolve) => {
    const off = onAuthChange(() => {
      if (_ready) {
        off();
        resolve();
      }
    });
  });
}

if (typeof window !== "undefined") {
  const supabase = getSupabase();
  if (supabase) {
    supabase.auth.getSession().then(({ data }) => {
      _session = data.session;
      _ready = true;
      _emit();
    });
    supabase.auth.onAuthStateChange((_event, session) => {
      _session = session;
      _ready = true;
      _emit();
    });
  } else {
    _ready = true;
  }
}

// --- viewer fallback ---------------------------------------------------

function _viewerId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(VIEWER_KEY) || null;
  } catch {
    return null;
  }
}

// --- public API ------------------------------------------------------

/** Synchronous snapshot for rendering. */
export function getAuth(): AuthState | null {
  if (_session?.access_token) {
    return {
      mode: "supabase",
      token: _session.access_token,
      expiresAt: _session.expires_at ? _session.expires_at * 1000 : undefined,
      email: _session.user?.email ?? undefined,
      name:
        (_session.user?.user_metadata?.full_name as string | undefined) ??
        (_session.user?.user_metadata?.name as string | undefined),
    };
  }
  const viewer = _viewerId();
  if (viewer) return { mode: "viewer", token: viewer };
  return null;
}

/** Headers the API client attaches for identity (synchronous). */
export function authHeaders(): Record<string, string> {
  if (_session?.access_token) {
    return { Authorization: `Bearer ${_session.access_token}` };
  }
  const viewer = _viewerId();
  return viewer ? { "X-Viewer-Id": viewer } : {};
}

export async function signUpWithPassword(
  email: string,
  password: string
): Promise<{ needsConfirmation: boolean }> {
  const supabase = _require();
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
  });
  if (error) throw error;
  // No session back => the project requires email confirmation first.
  return { needsConfirmation: !data.session };
}

export async function signInWithPassword(
  email: string,
  password: string
): Promise<void> {
  const supabase = _require();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
}

async function _oauth(provider: "google" | "github"): Promise<void> {
  const supabase = _require();
  const { error } = await supabase.auth.signInWithOAuth({
    provider,
    options: { redirectTo: `${window.location.origin}/auth/callback` },
  });
  if (error) throw error;
  // supabase redirects the browser to the provider; nothing after this runs.
}

export function signInWithGoogle(): Promise<void> {
  return _oauth("google");
}

export function signInWithGitHub(): Promise<void> {
  return _oauth("github");
}

export async function signOut(): Promise<void> {
  try {
    sessionStorage.removeItem(VIEWER_KEY);
  } catch {
    /* ignore */
  }
  const supabase = getSupabase();
  if (supabase) await supabase.auth.signOut();
  _session = null;
  _emit();
}

/** Dev fallback: unverified viewer identity, valid only in the public posture. */
export function signInAsViewer(id: string): AuthState {
  const trimmed = id.trim().slice(0, 100);
  if (!trimmed) throw new Error("Viewer id required");
  try {
    sessionStorage.setItem(VIEWER_KEY, trimmed);
  } catch {
    /* private mode — the id still rides the in-memory emit for this tab */
  }
  _emit();
  return { mode: "viewer", token: trimmed };
}

function _require() {
  const supabase = getSupabase();
  if (!supabase) {
    throw new Error(
      "Supabase Auth is not configured (set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY)."
    );
  }
  return supabase;
}
