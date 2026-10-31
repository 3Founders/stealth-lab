// @vitest-environment jsdom
// /pricing: the founders' plans (the ladder Starter, Pro, Plus with Plus recommended; the Pilot; Teams and Enterprise) and the prepaid-credit top-up beneath them.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

vi.mock("@/lib/session", () => ({ getAccessToken: async () => null, getSession: async () => null, onSessionChange: () => () => {} }));

import PricingPage from "@/app/pricing/page";
import { PLANS, perDay, plusVersusPro, tasksEstimate, usageBuys } from "@/lib/plans";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<PricingPage />); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.unstubAllEnvs(); });

describe("/pricing", () => {
  it("shows the plans with the founders' prices; with purchases off every card goes to Contact", async () => {
    await render();
    const t = host!.textContent ?? "";
    for (const price of ["₹200", "₹1,200", "₹2,000", "₹649", "₹1,800", "Custom"]) expect(t).toContain(price);
    expect(t).not.toContain("₹999");                                                            // the old Pro price
    expect(t).toContain("before GST");
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(Array(6).fill("/contact"));
  });

  it("with purchases on, the Starter, Pro, Plus and Pilot cards go to their payment blocks", async () => {
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "on");
    await render();
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(["#buy-starter", "#buy-pro", "#buy-plus", "#buy-pilot", "/contact", "/contact"]);
  });

  it("recommends Plus with a plain label, and no other card carries it", async () => {
    await render();
    const labelled = [...host!.querySelectorAll("article")].filter((a) => a.textContent?.includes("Recommended"));
    expect(labelled.map((a) => a.getAttribute("aria-label"))).toEqual(["Plus"]);
    expect(host!.querySelectorAll(".plan-plus").length).toBe(1);
  });

  it("shows the per-day figure, the usage in dollars and the estimate, all taken from the plan data", async () => {
    await render();
    const card = (name: string) => host!.querySelector(`article[aria-label="${name}"]`)!.textContent ?? "";
    expect(card("Starter")).toContain("about ₹7 a day");
    expect(card("Pro")).toContain("about ₹40 a day");
    expect(card("Plus")).toContain("about ₹67 a day");
    expect(card("Starter")).toContain("$1 of usage included");
    expect(card("Pro")).toContain("$6 of usage included");
    expect(card("Plus")).toContain("$12 of usage included");
    expect(card("Pilot")).toContain("$4 of usage included per seat");
    for (const p of PLANS.filter((x) => x.includedUsd)) {
      expect(card(p.name)).toContain(perDay(p) ?? "");
      expect(card(p.name)).toContain(usageBuys(p) ?? "");
      expect(usageBuys(p)).toMatch(/^Estimate: about /);                                       // marked as an estimate
    }
  });

  it("says 'Plus gives twice Pro's usage for ₹800 more', computed from the data and not typed", async () => {
    await render();
    expect(host!.textContent).toContain("Plus gives twice Pro's usage for ₹800 more.");
    const plus = PLANS.find((p) => p.id === "plus")!, pro = PLANS.find((p) => p.id === "pro")!;
    expect(plusVersusPro(plus, pro)).toBe("Plus gives twice Pro's usage for ₹800 more.");
    expect(plusVersusPro({ ...plus, priceInr: 2500, includedUsd: 18 }, pro)).toBe("Plus gives three times Pro's usage for ₹1,300 more.");
    expect(plusVersusPro({ ...plus, includedUsd: 9 }, pro)).toBe("Plus gives 1.5 times Pro's usage for ₹800 more.");
  });

  it("the usage estimate is rounded to two figures and is not a promise", () => {
    expect(tasksEstimate(1)).toBe(19);
    expect(tasksEstimate(6)).toBe(120);
    expect(tasksEstimate(12)).toBe(230);
    expect(tasksEstimate(4)).toBe(77);
  });

  it("uses no countdown, no scarcity, no crossed-out price and no pre-selected upsell", async () => {
    await render();
    expect(host!.textContent).not.toMatch(/only \d+ left|ends (today|soon|in)|hurry|limited time|countdown|most popular|best value|save \d/i);
    expect(host!.querySelector("s, del, strike")).toBeNull();
    expect([...host!.querySelectorAll("input[type=checkbox], input[type=radio]")].some((i) => (i as HTMLInputElement).checked)).toBe(false);
  });

  it("with purchases off, shows the four credit packs as information, with no buy button and no free-form top-up", async () => {
    await render();
    const t = host!.textContent ?? "";
    expect(t).toContain("Additional kell credits");
    for (const [price, credit] of [["₹500", "$2.50"], ["₹1,000", "$5.25"], ["₹2,500", "$13.50"], ["₹5,000", "$28.00"]]) { expect(t).toContain(price); expect(t).toContain(`${credit} of credit`); }
    expect(t).toContain("Buying credits isn’t switched on yet");
    expect(t).toContain("people with an active plan");
    expect(t).toContain("never expire");
    expect(t).toContain("How GST is applied is being confirmed"); // GST mode unset
    expect(host!.querySelector("button")).toBeNull();            // no Pay or buy button
    expect(host!.querySelector("input")).toBeNull();             // no amount field
    expect(t).not.toMatch(/Extra usage: prepaid credits|Illustration, not a quote|Amount in rupees|margin/i);
  });

  it("with purchases on, offers sign-in when signed out and links the legal pages", async () => {
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "on");
    await render();
    const t = host!.textContent ?? "";
    expect(t).toContain("Sign in to add credit");
    expect(host!.querySelector("button")).toBeNull();            // no Pay or buy button while signed out
    const hrefs = [...host!.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    for (const h of ["/docs/legal/terms", "/docs/legal/privacy", "/legal/refunds", "/contact", "/sign-in?redirect=/pricing"]) expect(hrefs).toContain(h);
  });

  it("makes no claim it cannot back: no savings, no other company's prices, no security or retention claim, no bring-your-own-key", async () => {
    await render();
    const t = host!.textContent ?? "";
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude|ChatGPT|Codex|per developer|zero.egress|zero data retention|no.retention|SOC 2 (compliant|certified)|bring your own key|non-sensitive/i);
    expect(t).toContain("does not yet have a SOC 2 report");
  });
});
