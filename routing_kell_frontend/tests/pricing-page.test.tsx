// @vitest-environment jsdom
// /pricing: the explanation is public and honest; a server that has billing off shows a clean state and no pay button.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let signedIn = true;
vi.mock("@/lib/session", () => ({
  getAccessToken: async () => (signedIn ? "tok" : null),
  getSession: async () => (signedIn ? { subject: "u1" } : null),
  onSessionChange: () => () => {},
}));
vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

import PricingPage from "@/app/pricing/page";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render(status: number, body: unknown) {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<PricingPage />); });
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.unstubAllGlobals(); signedIn = true; });

describe("/pricing", () => {
  it("billing switched off (503): says so, offers no pay button", async () => {
    await render(503, { error: { message: "off" } });
    expect(host!.textContent).toContain("isn’t switched on yet");
    expect(host!.querySelector("button[type=submit]")).toBeNull();
  });

  it("explains the rule without a made-up number when no margin is configured, and never mentions reselling a closed model", async () => {
    await render(503, {});
    const t = host!.textContent ?? "";
    expect(t).toContain("plus a margin");
    expect(t).toContain("Illustration, not a quote");
    expect(t).not.toMatch(/Claude|Codex|SOC 2 (compliant|certified)|zero.egress|save[sd]? \d/i);
    expect(t).toContain("does not yet have a SOC 2 report");
  });

  it("signed out: the balance and pay button are not shown, a sign-in link is", async () => {
    signedIn = false;
    await render(200, {});
    expect(host!.querySelector("a[href^='/sign-in']")).not.toBeNull();
    expect(host!.querySelector("button[type=submit]")).toBeNull();
  });

  it("signed in with billing on: balance in rupees and a pay button for the chosen amount", async () => {
    await render(200, { purchased_usd: "10", spent_usd: "0", remaining_usd: "0.74", usd_per_100_inr: "0.74", min_inr: 100, max_inr: 50000, credits: [] });
    expect(host!.textContent).toContain("₹100");
    expect(host!.querySelector("button[type=submit]")?.textContent).toContain("Pay ₹500");
  });

  it("shows the four plans with the founders' prices, none buyable online, and a way to ask", async () => {
    await render(503, {});
    const t = host!.textContent ?? "";
    for (const price of ["₹999", "₹1,800", "₹649", "Custom"]) expect(t).toContain(price);
    expect(t).toContain("before GST");
    expect(t).toContain("Not available online yet");
    expect(t).toContain("Invoiced directly");
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude Pro|ChatGPT|per developer/i);
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(["/contact", "/contact", "/contact", "/contact"]);
    // no plan has a checkout: the only submit button is the credit top-up, and billing is off here
    expect(host!.querySelector("button[type=submit]")).toBeNull();
  });
});
