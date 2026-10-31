// @vitest-environment jsdom
// /pricing: the founders' plans (none buyable online yet, each with a way to ask) and the prepaid-credit top-up beneath them.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

vi.mock("@/lib/session", () => ({ getAccessToken: async () => null, getSession: async () => null, onSessionChange: () => () => {} }));

import PricingPage from "@/app/pricing/page";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<PricingPage />); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("/pricing", () => {
  it("shows the four plans with the founders' prices; Pilot and Pro go to their payment blocks, Teams and Enterprise to Contact", async () => {
    await render();
    const t = host!.textContent ?? "";
    for (const price of ["₹999", "₹1,800", "₹649", "Custom"]) expect(t).toContain(price);
    expect(t).toContain("before GST");
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(["#buy-pro", "/contact", "/contact", "#buy-pilot"]);
  });

  it("explains extra usage and credits, offers sign-in when signed out, and links the legal pages", async () => {
    await render();
    const t = host!.textContent ?? "";
    expect(t).toContain("Extra usage: prepaid credits");
    expect(t).toContain("Illustration, not a quote");
    expect(t).toContain("Sign in to add credit");
    expect(t).toContain("How GST is applied is being confirmed"); // GST mode unset
    expect(host!.querySelector("button")).toBeNull();            // no Pay or buy button while signed out
    const hrefs = [...host!.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    for (const h of ["/docs/legal/terms", "/docs/legal/privacy", "/legal/refunds", "/contact", "/sign-in?redirect=/pricing"]) expect(hrefs).toContain(h);
  });

  it("makes no claim it cannot back", async () => {
    await render();
    const t = host!.textContent ?? "";
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude|ChatGPT|Codex|per developer|zero.egress|SOC 2 (compliant|certified)/i);
    expect(t).toContain("does not yet have a SOC 2 report");
  });
});
