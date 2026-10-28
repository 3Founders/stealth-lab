// @vitest-environment jsdom
// The footer's Company column: Support always, the four outside links only when configured, and only over https.
// @ts-ignore this app does not install @types/react-dom; the test only needs renderToStaticMarkup at runtime
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { companyLinks, parseSocialUrl } from "@/lib/social-links";

vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("next/image", () => ({ default: () => null }));

import Footer from "@/components/Footer";

afterEach(() => vi.unstubAllEnvs());

describe("social links", () => {
  it("accepts clean https addresses and refuses everything else", () => {
    expect(parseSocialUrl("https://github.com/kell")).toBe("https://github.com/kell");
    for (const bad of ["", "  ", undefined, "http://x.com/kell", "javascript:alert(1)", "https://user:pw@x.com", "not a url", "ftp://x.com"]) expect(parseSocialUrl(bad as string)).toBeNull();
  });
  it("with nothing configured there is only Support, pointing at /contact", () => {
    expect(companyLinks({})).toEqual([{ label: "Support", href: "/contact", external: false }]);
  });
  it("shows each configured link in the order GitHub, X, LinkedIn, Discord, then Support", () => {
    const l = companyLinks({ discord: "https://discord.gg/abc", github: "https://github.com/kell", linkedin: "http://insecure.example", x: "https://x.com/kell" });
    expect(l.map((x) => x.label)).toEqual(["GitHub", "X", "Discord", "Support"]);   // the http one is dropped
    expect(l.filter((x) => x.external)).toHaveLength(3);
  });
});

describe("Footer", () => {
  it("has a Company column with Support, and outside links open in a new tab without a reference to this page", () => {
    vi.stubEnv("NEXT_PUBLIC_GITHUB_URL", "https://github.com/kell");
    vi.stubEnv("NEXT_PUBLIC_X_URL", "https://x.com/kell");
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain('aria-label="Company"');
    expect(html).toContain("Company");
    expect(html).toContain('href="/contact"');
    expect(html).toMatch(/<a href="https:\/\/github\.com\/kell" target="_blank" rel="noopener noreferrer">GitHub<\/a>/);
    expect(html).toMatch(/<a href="https:\/\/x\.com\/kell" target="_blank" rel="noopener noreferrer">X<\/a>/);
    expect(html).not.toContain("LinkedIn");                                         // not configured, so no dead link
    expect(html).not.toContain("Discord");
  });
  it("with nothing configured the Company column still has Support", () => {
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain("Support");
    expect(html).not.toContain("GitHub");
  });
});
