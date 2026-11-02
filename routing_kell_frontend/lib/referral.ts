/**
 * The referral code a person applied on /pricing, remembered in this browser so it survives signing in and back (the code travels
 * through a redirect and the page is rebuilt). It is not a secret: it unlocks one plan for whoever uses it, and the server checks
 * it again when an order is made, so a stored value can never unlock anything by itself. Kept per browser rather than per account,
 * because it is applied before anyone signs in.
 */
const KEY = "kel.referral.code";

export function rememberCode(code: string, store: Pick<Storage, "setItem"> = window.localStorage): void {
  try { store.setItem(KEY, code.trim().toUpperCase()); } catch { /* storage may be blocked */ }
}

export function recallCode(store: Pick<Storage, "getItem"> = window.localStorage): string | null {
  try {
    const v = (store.getItem(KEY) ?? "").trim().toUpperCase();
    return v || null;
  } catch { return null; }
}

export function forgetCode(store: Pick<Storage, "removeItem"> = window.localStorage): void {
  try { store.removeItem(KEY); } catch { /* storage may be blocked */ }
}
