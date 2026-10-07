/**
 * Supabase Auth browser client (V1 authentication).
 *
 * This is the ONE place the frontend talks to Supabase directly — every
 * other module (lib/session.ts, the sign-in/onboarding/settings pages)
 * goes through the functions exported here, never `createClient` itself.
 *
 * Configuration is public, deployment-side config, not a secret:
 * NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY (the anon key is
 * safe in a browser bundle by Supabase's own design — it identifies the
 * project, RLS/the backend's own token verification is what actually
 * gates access). No service-role key belongs anywhere in this app; the
 * backend verifies every access token itself (app/services/authn.py's
 * Supabase Auth preset) rather than trusting the browser.
 *
 * Session storage is Supabase's own supported mechanism (localStorage,
 * managed by the SDK, auto-refreshed) — this file does not reimplement
 * token storage.
 */
"use client";

import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

let _client: SupabaseClient | null = null;

/** null when this deployment hasn't configured Supabase Auth yet — every
 * caller must handle that honestly (see lib/session.ts), never pretend a
 * session exists. */
export function getSupabase(): SupabaseClient | null {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;
  if (!_client) {
    _client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return _client;
}

export const supabaseConfigured = (): boolean => Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

export type { Session as SupabaseSession };
