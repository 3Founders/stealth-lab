// The same token as scripts/outreach/unsub.py: base64url(email) + "." + base64url(HMAC-SHA256(secret, email)). Web Crypto only, so it runs in
// Supabase Edge Functions (Deno) and in Node (the cross-check test).
const enc = new TextEncoder();
const b64 = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64 = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4)), (c) => c.charCodeAt(0));

async function mac(secret, bytes) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  return key;
}

export async function sign(secret, email) {
  const e = enc.encode(email.trim().toLowerCase());
  const key = await mac(secret);
  return b64(e) + "." + b64(new Uint8Array(await crypto.subtle.sign("HMAC", key, e)));
}

// -> the address, or null. crypto.subtle.verify compares in constant time.
export async function verify(secret, token) {
  try {
    const [left, right] = String(token).split(".", 2);
    const e = unb64(left);
    const ok = await crypto.subtle.verify("HMAC", await mac(secret), unb64(right), e);
    return ok ? new TextDecoder().decode(e) : null;
  } catch {
    return null;
  }
}
