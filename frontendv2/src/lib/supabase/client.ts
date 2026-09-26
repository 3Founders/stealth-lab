/**
 * The one Supabase browser client. Supabase Auth is StealthLab's canonical
 * end-user identity provider (launch compliance Phase 1). This client issues
 * the session; the StealthLab backend is the only token *validator* — it
 * verifies "Authorization: Bearer <access_token>" against
 * {SUPABASE_URL}/auth/v1 JWKS (ES256/RS256).
 *
 * Only the ANON (publishable) key is used here — it is designed to be shipped
 * in the browser bundle. The service_role key must NEVER appear in frontend
 * code or NEXT_PUBLIC_* env: it bypasses Row Level Security.
 *
 * persistSession + autoRefreshToken: the session (incl. refresh token) is
 * stored in localStorage and the access token is refreshed transparently, so
 * a reload or a returning tab restores the signed-in state. flowType 'pkce'
 * is required for the OAuth (Google) redirect to work without a client
 * secret.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export function isSupabaseConfigured(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

let _client: SupabaseClient | null = null;

/**
 * Returns the singleton client, or null when Supabase is not configured
 * (local/dev public posture — the /auth page then offers the dev viewer id).
 */
export function getSupabase(): SupabaseClient | null {
  if (!isSupabaseConfigured()) return null;
  if (_client) return _client;
  _client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      flowType: "pkce",
    },
  });
  return _client;
}
