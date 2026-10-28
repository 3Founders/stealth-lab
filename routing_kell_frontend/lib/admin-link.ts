/**
 * Where the admin console lives. The admin app (admin/) is deliberately a separate app on its own origin: it is never
 * framed, never cached, and carries its own sign-in, and the backend alone decides who holds the operator scopes. So the
 * website does not embed admin pages; it links to them. The link exists only when NEXT_PUBLIC_ADMIN_URL is set for this
 * deployment, so no address is guessed and a build without an admin console shows no dead link.
 */
export function parseAdminUrl(raw: string | undefined | null): string | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  let url: URL;
  try { url = new URL(value); } catch { return null; }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) return null; // never send an operator to plain http
  if (url.username || url.password) return null;
  return url.origin;
}

/** The admin console's sign-in page, or null when this deployment has none. */
export function adminSignInUrl(): string | null {
  const origin = parseAdminUrl(process.env.NEXT_PUBLIC_ADMIN_URL);
  return origin ? `${origin}/sign-in` : null;
}
