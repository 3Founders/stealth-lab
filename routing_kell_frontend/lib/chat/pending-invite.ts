/**
 * Remembers an invite code across sign-in and onboarding. A person who opens an invite link without an account is sent through
 * sign-in (possibly out to Google or GitHub and back) and onboarding; any step that rebuilds the address from the path alone drops
 * the `?code=`, and they land on "This link is missing its invite code". The join page keeps the code here when it sees it and reads
 * it back when the address has lost it. It is cleared once the join succeeds, and expires after a day. The code is a single-use-ish
 * invite secret, held only in this browser's storage.
 */
const KEY = "kel.pending.invite";
const TTL_MS = 24 * 3600 * 1000;

export function rememberInvite(code: string, now: number = Date.now(), store: Storage = window.localStorage): void {
  try { store.setItem(KEY, JSON.stringify({ code, at: now })); } catch { /* storage may be blocked */ }
}

export function recallInvite(now: number = Date.now(), store: Storage = window.localStorage): string {
  try {
    const v = JSON.parse(store.getItem(KEY) ?? "null") as { code?: unknown; at?: unknown } | null;
    return v && typeof v.code === "string" && typeof v.at === "number" && now - v.at < TTL_MS ? v.code : "";
  } catch { return ""; }
}

export function forgetInvite(store: Storage = window.localStorage): void {
  try { store.removeItem(KEY); } catch { /* none */ }
}
