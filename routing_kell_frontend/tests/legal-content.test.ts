import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { LEGAL_DOCS, renderLegal } from "@/lib/legal";
import { legalFooterHtml } from "@/lib/legal-config";

// The public legal pages must read as a company's own pages: no reference to our repository or its files, no unfilled
// placeholder, no internal note, and one name for the product. These checks hold that line for every later edit.
const dir = path.resolve(__dirname, "..", "content", "legal");
const files = fs.readdirSync(dir).filter((f) => f.endsWith(".md"));

/** The text a reader sees as prose: inline code and code blocks removed (a command may spell the name in lower case). */
const prose = (md: string) => md.replace(/```[\s\S]*?```/g, "").replace(/`[^`\n]*`/g, "");

describe("legal documents", () => {
  it("the folder holds exactly the documents the site lists", () => {
    expect(files.sort()).toEqual(LEGAL_DOCS.map((d) => d.file).sort());
  });

  for (const f of files) {
    const md = fs.readFileSync(path.join(dir, f), "utf8");

    it(`${f}: no repository paths, file names or internal vocabulary`, () => {
      expect(md).not.toMatch(/\b(backend|frontendv\d|prod_frontend|routing_kell_frontend|final_prod_docs|packaging|experiments)\/|\.(py|tsx?|mjs|sql)\b/);
      expect(md).not.toMatch(/\b(SECURITY|DATA_STATEMENT|README)\.md\b|LAUNCH-COMPLIANCE|compliance (spec|ledger)|\bthe codebase\b|\baudit\b.*\bcode/i);
      expect(md).not.toMatch(/\b(GET|POST|PUT|DELETE) \/v1\//);
      expect(md).not.toMatch(/\.md\)/); // links go to /docs/legal/<name>, never to a file
    });

    it(`${f}: no placeholder, draft banner or note to the founders`, () => {
      expect(md).not.toMatch(/\[[A-Z][A-Z0-9 ,/.:'’-]{3,}\]/); // [LEGAL ENTITY NAME], [CONTACT EMAIL]
      expect(md).not.toMatch(/\b(TODO|FIXME|TBD)\b|\bFOUNDER\b|\bSTATUS: ?DRAFT|REQUIRES LEGAL REVIEW|Generated from/);
      expect(md).not.toMatch(/example\.invalid|stealthlab\.example/i);
    });

    it(`${f}: the product is called Kell`, () => {
      const text = prose(md);
      expect(text).not.toMatch(/stealthlab|stealth lab|keळ|\bkel\b/i);
      expect(text).not.toMatch(/\bkell\b/); // lower case only inside commands and file names
      expect(md.startsWith("# ")).toBe(true);
    });

    it(`${f}: carries a date`, () => {
      expect(md).toMatch(/^Last updated: [A-Z][a-z]+ 20\d\d\.$/m);
    });
  }

  it("every link between documents is an in-site /docs/legal path that exists", () => {
    const slugs = new Set(LEGAL_DOCS.map((d) => d.slug));
    for (const f of files) {
      const md = fs.readFileSync(path.join(dir, f), "utf8");
      for (const m of md.matchAll(/\]\((\/docs\/legal\/([a-z-]+))\)/g)) expect(slugs.has(m[2]), `${f} links to ${m[1]}`).toBe(true);
      for (const m of md.matchAll(/\]\(([^)]+)\)/g)) expect(m[1]).toMatch(/^\/docs\/legal\/[a-z-]+$/);
    }
  });

  it("renders every document without a stray file link or an empty page", () => {
    for (const d of LEGAL_DOCS) {
      const html = renderLegal(d);
      expect(html.length).toBeGreaterThan(800);
      expect(html).not.toMatch(/href="[^"]*\.md/);
    }
  });
});

describe("company details", () => {
  it("shows nothing at all when none are set, rather than a placeholder", () => {
    expect(legalFooterHtml({ entity: "", email: "", law: "" })).toBe("");
  });
  it("shows exactly what is set, escaped", () => {
    const html = legalFooterHtml({ entity: "Acme <Ltd>", email: "legal@example.com", law: "" });
    expect(html).toContain("Acme &lt;Ltd&gt;");
    expect(html).toContain('href="mailto:legal@example.com"');
    expect(html).not.toContain("Governing law");
  });
});
