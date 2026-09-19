import fs from "node:fs";
import path from "node:path";
import { marked } from "marked";

export type LegalDoc = { slug: string; title: string; file: string; blurb: string };

// Order = display order. Sources are the repo's docs/legal drafts, copied to content/legal by scripts/sync-legal.mjs.
export const LEGAL_DOCS: LegalDoc[] = [
  { slug: "terms", title: "Terms of Service", file: "TERMS_OF_SERVICE.md", blurb: "The agreement for using the service." },
  { slug: "privacy", title: "Privacy Policy", file: "PRIVACY_POLICY.md", blurb: "What data is collected and what happens to it." },
  { slug: "acceptable-use", title: "Acceptable Use Policy", file: "ACCEPTABLE_USE_POLICY.md", blurb: "What you may not do, and how violations are caught." },
  { slug: "global-commons", title: "Global Commons Terms", file: "GLOBAL_COMMONS_TERMS.md", blurb: "Terms for the shared, public library of procedures." },
  { slug: "verification", title: "Verification Disclaimer", file: "VERIFICATION_DISCLAIMER.md", blurb: "What “candidate” and “verified” actually mean." },
  { slug: "copyright", title: "Copyright & Third-Party Sources", file: "COPYRIGHT_AND_THIRD_PARTY_SOURCES.md", blurb: "Policy for material ingested from public sources." },
  { slug: "cookies", title: "Cookies & Tracking", file: "COOKIES_AND_TRACKING.md", blurb: "What the web app stores, and why." },
  { slug: "subprocessors", title: "Subprocessors", file: "SUBPROCESSORS.md", blurb: "Third parties that process data." },
  { slug: "security", title: "Security Overview", file: "SECURITY_OVERVIEW.md", blurb: "Public summary of the security posture." },
];

const byFile = new Map(LEGAL_DOCS.map((d) => [d.file, d.slug]));
const dir = path.join(process.cwd(), "content", "legal");

export function getLegalDoc(slug: string) {
  return LEGAL_DOCS.find((d) => d.slug === slug);
}

/** Render a legal draft to HTML. Links to sibling legal docs are routed in-site; links to other repo files are kept as plain text. */
export function renderLegal(doc: LegalDoc): string {
  const md = fs.readFileSync(path.join(dir, doc.file), "utf-8");
  const renderer = new marked.Renderer();
  renderer.link = function ({ href, title, tokens }) {
    const text = this.parser.parseInline(tokens);
    const file = href.replace(/^\.\//, "").split("#")[0];
    const slug = byFile.get(file);
    if (slug) return `<a href="/docs/legal/${slug}"${title ? ` title="${title}"` : ""}>${text}</a>`;
    if (/^(https?:|mailto:|#)/.test(href)) return `<a href="${href}" rel="noopener">${text}</a>`;
    return text;
  };
  return marked.parse(md, { renderer, async: false }) as string;
}
