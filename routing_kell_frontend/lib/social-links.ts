/**
 * The footer's Company column: GitHub, X, LinkedIn, Discord and Support. Support is the Contact page, which exists in every build.
 * The four outside addresses come from this deployment's configuration and each one is shown only when it is set, so a build
 * without an address has no dead link and nothing is guessed:
 *   NEXT_PUBLIC_GITHUB_URL, NEXT_PUBLIC_X_URL, NEXT_PUBLIC_LINKEDIN_URL, NEXT_PUBLIC_DISCORD_URL   (https only)
 */
export interface CompanyLink { label: string; href: string; external: boolean }

/** An https URL reduced to a clean string, or null. Anything else (http, a javascript: URL, one with a password) is refused. */
export function parseSocialUrl(raw: string | undefined | null): string | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  let url: URL;
  try { url = new URL(value); } catch { return null; }
  if (url.protocol !== "https:" || url.username || url.password) return null;
  return url.toString();
}

export interface SocialEnv { github?: string; x?: string; linkedin?: string; discord?: string }

/** Next.js only inlines `process.env.NEXT_PUBLIC_*` when each name is written out in full, so they are read here one by one. */
const fromEnv = (): SocialEnv => ({
  github: process.env.NEXT_PUBLIC_GITHUB_URL, x: process.env.NEXT_PUBLIC_X_URL,
  linkedin: process.env.NEXT_PUBLIC_LINKEDIN_URL, discord: process.env.NEXT_PUBLIC_DISCORD_URL,
});

export function companyLinks(env: SocialEnv = fromEnv()): CompanyLink[] {
  const out: CompanyLink[] = [];
  for (const [label, raw] of [["GitHub", env.github], ["X", env.x], ["LinkedIn", env.linkedin], ["Discord", env.discord]] as const) {
    const href = parseSocialUrl(raw);
    if (href) out.push({ label, href, external: true });
  }
  out.push({ label: "Support", href: "/contact", external: false });
  return out;
}
