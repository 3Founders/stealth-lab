/**
 * Second-factor sign-in for the admin console, on Supabase Auth's TOTP factors (an authenticator app: 1Password, Authy, Google
 * Authenticator and the like). Verifying a code lifts the session from assurance level aal1 to aal2; the backend's billing routes
 * check that claim in the token, so hiding a page here is a courtesy and never the control.
 *
 * Limits, said plainly: Supabase TOTP has no recovery codes. A person who loses their authenticator needs another admin (or the
 * Supabase dashboard) to remove their factor, so keep at least two admins. Security keys and passkeys are not offered yet.
 */
import { getSupabase } from "@/lib/supabase";

export type Level = "aal1" | "aal2" | null;
export interface MfaState { current: Level; next: Level; factors: Array<{ id: string; name: string | null; status: string }>; configured: boolean }

/** The console enforces a second factor only when this deployment says so (NEXT_PUBLIC_ADMIN_REQUIRE_MFA=on). */
export const mfaRequired = (): boolean => (process.env.NEXT_PUBLIC_ADMIN_REQUIRE_MFA || "").trim().toLowerCase() === "on";

export async function mfaState(): Promise<MfaState> {
  const c = getSupabase();
  if (!c) return { current: null, next: null, factors: [], configured: false };
  const [lvl, list] = await Promise.all([c.auth.mfa.getAuthenticatorAssuranceLevel(), c.auth.mfa.listFactors()]);
  const totp = list.data?.totp ?? [];
  return {
    current: (lvl.data?.currentLevel as Level) ?? null, next: (lvl.data?.nextLevel as Level) ?? null, configured: true,
    factors: totp.map((f) => ({ id: f.id, name: f.friendly_name ?? null, status: f.status })),
  };
}

export async function enrollTotp(): Promise<{ id: string; qr: string; secret: string } | { error: string }> {
  const c = getSupabase();
  if (!c) return { error: "Sign-in isn't configured here." };
  const { data, error } = await c.auth.mfa.enroll({ factorType: "totp", friendlyName: `Authenticator ${new Date().toISOString().slice(0, 10)}` });
  if (error || !data) return { error: error?.message || "Couldn't start enrolment. Turn on TOTP multi-factor in the Supabase project's Auth settings first." };
  return { id: data.id, qr: data.totp.qr_code, secret: data.totp.secret };
}

/** Confirms a code for a factor (both to finish enrolling and to sign in). Returns an error sentence or null. */
export async function verifyTotp(factorId: string, code: string): Promise<string | null> {
  const c = getSupabase();
  if (!c) return "Sign-in isn't configured here.";
  const { error } = await c.auth.mfa.challengeAndVerify({ factorId, code: code.replace(/\s+/g, "") });
  return error ? "That code was not accepted. Codes change every 30 seconds; try the current one." : null;
}

export async function removeFactor(factorId: string): Promise<string | null> {
  const c = getSupabase();
  if (!c) return "Sign-in isn't configured here.";
  const { error } = await c.auth.mfa.unenroll({ factorId });
  return error ? "Couldn't remove it. Removing a factor needs a session that already passed the second step." : null;
}

/** Where a signed-in person must go before the console opens: nowhere (null), enrolment, or the code prompt. */
export function nextStep(s: MfaState): "enroll" | "verify" | null {
  if (s.current === "aal2") return null;
  return s.factors.some((f) => f.status === "verified") ? "verify" : "enroll";
}
