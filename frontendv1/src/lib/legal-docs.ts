import fs from "node:fs";
import path from "node:path";

// Renders the repo's `docs/legal/*.md` drafts inside the app so they have a
// real, linkable route instead of living only as files nobody sees. This is
// a plain-text render (no markdown-to-HTML pipeline is wired into this repo
// yet) -- see docs/legal/AUDIT_REPORT.md for the fuller wiring plan.
//
// Path resolution: frontendv1's process.cwd() is frontendv1/ in both `next
// dev` and `next build` (Next.js does not change cwd), so `../docs/legal`
// reaches the repo-root docs/legal directory this content actually lives in.
const LEGAL_DOCS_DIR = path.join(process.cwd(), "..", "docs", "legal");

export interface LegalDocMeta {
  slug: string;
  title: string;
  filename: string;
}

// Order here is also the order shown on /legal.
export const LEGAL_DOCS: LegalDocMeta[] = [
  { slug: "terms", title: "Terms of Service", filename: "TERMS_OF_SERVICE.md" },
  { slug: "privacy", title: "Privacy Policy", filename: "PRIVACY_POLICY.md" },
  { slug: "acceptable-use", title: "Acceptable Use Policy", filename: "ACCEPTABLE_USE_POLICY.md" },
  { slug: "global-commons", title: "Global Commons Terms", filename: "GLOBAL_COMMONS_TERMS.md" },
  { slug: "verification", title: "Verification Disclaimer", filename: "VERIFICATION_DISCLAIMER.md" },
  { slug: "copyright", title: "Copyright & Third-Party Sources", filename: "COPYRIGHT_AND_THIRD_PARTY_SOURCES.md" },
  { slug: "cookies", title: "Cookies & Tracking", filename: "COOKIES_AND_TRACKING.md" },
  { slug: "subprocessors", title: "Subprocessors", filename: "SUBPROCESSORS.md" },
  { slug: "security", title: "Security Overview", filename: "SECURITY_OVERVIEW.md" },
];

export function getLegalDocMeta(slug: string): LegalDocMeta | undefined {
  return LEGAL_DOCS.find((d) => d.slug === slug);
}

export function readLegalDoc(filename: string): string {
  const full = path.join(LEGAL_DOCS_DIR, filename);
  return fs.readFileSync(full, "utf-8");
}
