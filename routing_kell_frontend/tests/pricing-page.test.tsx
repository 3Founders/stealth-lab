// @vitest-environment jsdom
// /pricing: the founders' plans (the ladder Starter, Pro, Plus with Plus recommended; Teams and Enterprise), the referral-code
// section above them, and what a valid code does to the plans area: exactly one card, the one-person Pilot it unlocks. Also the
// prepaid-credit packs beneath, and the copy rules (no countdown, no scarcity, no crossed-out price, no savings claim).
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

vi.mock("@/lib/session", () => ({ getAccessToken: async () => null, getSession: async () => null, onSessionChange: () => () => {} }));

import PricingPage from "@/app/pricing/page";
import { PLANS, perDay, plusVersusPro } from "@/lib/plans";
import { recallCode } from "@/lib/referral";

const json = (status: number, body: unknown) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
/** The billing surface, as the page meets it: off (503) unless a test says otherwise. No network. */
function stubServer(opts: { plans?: unknown; codeStatus?: number; codeBody?: unknown } = {}) {
  const f = vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith("/v1/billing/plans")) return opts.plans ? json(200, { plans: opts.plans }) : json(503, {});
    if (u.endsWith("/v1/billing/codes/check")) return json(opts.codeStatus ?? 200, opts.codeBody ?? { valid: true });
    return json(503, {});
  });
  vi.stubGlobal("fetch", f as unknown as typeof fetch);
  return f;
}

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<PricingPage />); });
  await act(async () => {});                                   // let the plans request land
}
beforeEach(() => { localStorage.clear(); });
afterEach(() => { act(() => root?.unmount()); host?.remove(); root = null; vi.unstubAllEnvs(); vi.unstubAllGlobals(); localStorage.clear(); });

const cards = () => [...host!.querySelectorAll("article.plan-card")].map((a) => a.getAttribute("aria-label"));
const card = (name: string) => host!.querySelector(`article[aria-label="${name}"]`)?.textContent ?? "";
const button = (label: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(label)) as HTMLButtonElement | undefined;
const profileCard = (labelledBy: string) => [...host!.querySelectorAll(".profile-card")].find((c) => c.getAttribute("aria-labelledby") === labelledBy)!;
async function typeCode(v: string) {
  const el = host!.querySelector("input#referral-code") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submitCode() {
  await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await act(async () => {});
}

describe("/pricing", () => {
  it("shows the plans with the founders' prices; with purchases off every card goes to Contact", async () => {
    stubServer();
    await render();
    const t = host!.textContent ?? "";
    for (const price of ["₹200", "₹1,200", "₹2,000", "₹1,800", "Custom"]) expect(t).toContain(price);
    const inCards = [...host!.querySelectorAll("article")].map((a) => a.textContent ?? "").join(" ");
    expect(inCards).not.toContain("₹649");                                                     // no pilot card without a code
    expect(inCards).not.toMatch(/pilot/i);
    expect(t).not.toContain("₹999");                                                            // the old Pro price
    expect(t).toContain("before GST");
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(Array(5).fill("/contact"));
  });

  it("the code section above the plans asks for a code and shows no card until one is applied", async () => {
    stubServer();
    await render();
    const t = host!.textContent ?? "";
    expect(t).toContain("Have a code?");
    expect(t).not.toContain("A code unlocks one plan for one person");
    expect(t).not.toContain("Without a code the plans below");
    expect(host!.querySelector("input#referral-code")).not.toBeNull();
    expect(button("Apply")).toBeDefined();
    expect(cards()).toEqual(["Starter", "Pro", "Plus", "Teams", "Enterprise"]);
  });

  it("with purchases on, Starter, Pro and Plus are bought in their own cards (no link to a payment block)", async () => {
    stubServer();
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "on");
    await render();
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(["/sign-in?redirect=/pricing", "/sign-in?redirect=/pricing", "/sign-in?redirect=/pricing", "/contact", "/contact"]);
  });

  it("recommends Plus with a plain label, and no other card carries it", async () => {
    stubServer();
    await render();
    const labelled = [...host!.querySelectorAll("article")].filter((a) => a.textContent?.includes("Recommended"));
    expect(labelled.map((a) => a.getAttribute("aria-label"))).toEqual(["Plus"]);
    expect(host!.querySelectorAll(".plan-plus").length).toBe(1);
  });

  it("shows the per-day figure taken from the plan data, and no usage line, no 'lower-cost models' line and no estimate on any card", async () => {
    stubServer();
    await render();
    expect(card("Starter")).toContain("about ₹7 a day");
    expect(card("Pro")).toContain("about ₹40 a day");
    expect(card("Plus")).toContain("about ₹67 a day");
    for (const p of PLANS.filter((x) => x.priceInr && x.id !== "pilot")) expect(card(p.name)).toContain(perDay(p) ?? p.price);
    const inCards = [...host!.querySelectorAll("article")].map((a) => a.textContent ?? "").join(" ");
    expect(inCards).not.toMatch(/of usage included|Lower-cost models|Estimate: about|delegated tasks/i);
  });

  it("says 'Plus gives twice Pro's usage for ₹800 more', computed from the data and not typed", async () => {
    stubServer();
    await render();
    expect(host!.textContent).toContain("Plus gives twice Pro's usage for ₹800 more.");
    const plus = PLANS.find((p) => p.id === "plus")!, pro = PLANS.find((p) => p.id === "pro")!;
    expect(plusVersusPro(plus, pro)).toBe("Plus gives twice Pro's usage for ₹800 more.");
    expect(plusVersusPro({ ...plus, priceInr: 2500, includedUsd: 18 }, pro)).toBe("Plus gives three times Pro's usage for ₹1,300 more.");
    expect(plusVersusPro({ ...plus, includedUsd: 9 }, pro)).toBe("Plus gives 1.5 times Pro's usage for ₹800 more.");
  });

  it("uses no countdown, no scarcity, no crossed-out price and no pre-selected upsell", async () => {
    stubServer();
    await render();
    expect(host!.textContent).not.toMatch(/only \d+ left|ends (today|soon|in)|hurry|limited time|countdown|most popular|best value|save \d/i);
    expect(host!.querySelector("s, del, strike")).toBeNull();
    expect([...host!.querySelectorAll("input[type=checkbox], input[type=radio]")].some((i) => (i as HTMLInputElement).checked)).toBe(false);
  });

  it("with purchases off, shows the four credit packs as information, with no buy button and no amount field in the packs card", async () => {
    stubServer();
    await render();
    const t = host!.textContent ?? "";
    expect(t).toContain("Additional kell credits");
    for (const [price] of [["₹500"], ["₹1,000"], ["₹2,500"], ["₹5,000"]]) expect(t).toContain(price);
    expect(t).not.toContain("of credit");
    expect(t).not.toContain("per dollar of credit");
    expect(t).toContain("Buying credits isn’t switched on yet");
    expect(t).toContain("people with an active plan");
    expect(t).toContain("does not expire");
    expect(t).toContain("How GST is applied is being confirmed"); // GST mode unset
    const packs = profileCard("packs-title");
    expect(packs.querySelector("button")).toBeNull();              // no Pay or buy button
    expect(packs.querySelector("input")).toBeNull();               // no amount field
    expect(host!.querySelector("input[type=number]")).toBeNull();  // the only field on the page is the referral code
    expect(t).not.toMatch(/Extra usage: prepaid credits|Illustration, not a quote|Amount in rupees|margin/i);
  });

  it("with purchases on, offers sign-in when signed out and links the legal pages", async () => {
    stubServer();
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "on");
    await render();
    const t = host!.textContent ?? "";
    expect(t).toContain("Sign in to add credit");
    expect([...host!.querySelectorAll("button")].some((b) => /Buy /.test(b.textContent ?? ""))).toBe(false);   // no buy button while signed out
    const hrefs = [...host!.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    for (const h of ["/docs/legal/terms", "/docs/legal/privacy", "/legal/refunds", "/contact", "/sign-in?redirect=/pricing"]) expect(hrefs).toContain(h);
  });

  it("makes no claim it cannot back: no savings, no other company's prices, no security or retention claim, no bring-your-own-key", async () => {
    stubServer();
    await render();
    const t = host!.textContent ?? "";
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude|ChatGPT|Codex|per developer|zero.egress|zero data retention|no.retention|SOC 2 (compliant|certified)|bring your own key|non-sensitive/i);
    expect(t).not.toMatch(/Where your data goes/);
  });

  it("a valid code replaces the whole plans area with the one card it unlocks, and the code is remembered", async () => {
    stubServer({ codeStatus: 200, codeBody: { valid: true } });
    await render();
    await typeCode("friend");
    await submitCode();
    expect(cards()).toEqual(["First Trial"]);
    const t = host!.textContent ?? "";
    expect(t).toContain("Code FRIEND applied.");
    expect(t).toContain("₹649");
    expect(t).toContain("about ₹22 a day");
    expect(t).not.toContain("₹1,200");                    // the ladder is gone
    expect(t).not.toContain("₹2,000");
    expect(t).not.toContain("For teams");
    expect(t).not.toContain("Plus gives twice");
    expect(recallCode()).toBe("FRIEND");
  });

  it("an unrecognised code says why and leaves the ladder as it was", async () => {
    stubServer({ codeStatus: 400, codeBody: { error: { message: "we do not recognise that code; check it and try again" } } });
    await render();
    await typeCode("nope");
    await submitCode();
    expect(host!.textContent).toContain("we do not recognise that code; check it and try again");
    expect(cards()).toEqual(["Starter", "Pro", "Plus", "Teams", "Enterprise"]);
    expect(recallCode()).toBeNull();
  });

  it("while billing is off, a code cannot be checked and nothing is applied", async () => {
    stubServer({ codeStatus: 503 });
    await render();
    await typeCode("friend");
    await submitCode();
    expect(host!.textContent).toContain("We can't check codes yet: buying plans is not switched on");
    expect(cards()).toEqual(["Starter", "Pro", "Plus", "Teams", "Enterprise"]);
  });

  it("an applied code survives a rebuild of the page (the sign-in round trip)", async () => {
    stubServer({ codeStatus: 200, codeBody: { valid: true } });
    await render();
    await typeCode("friend");
    await submitCode();
    act(() => root?.unmount()); host?.remove();
    await render();
    await act(async () => {});
    expect(cards()).toEqual(["First Trial"]);
    expect(host!.textContent).toContain("Code FRIEND applied.");
  });

  it("removing the code brings the ladder and the teams cards back", async () => {
    stubServer({ codeStatus: 200, codeBody: { valid: true } });
    await render();
    await typeCode("friend");
    await submitCode();
    await act(async () => { button("Remove code")!.click(); });
    expect(cards()).toEqual(["Starter", "Pro", "Plus", "Teams", "Enterprise"]);
    expect(recallCode()).toBeNull();
  });

  it("the server's current numbers replace the built-in ones, and the derived sentences follow them", async () => {
    stubServer({ plans: [
      { plan: "starter", inr: 200, days: 30, included_usd: "1" },
      { plan: "pro", inr: 1300, days: 30, included_usd: "6" },
      { plan: "plus", inr: 2000, days: 30, included_usd: "12" },
      { plan: "pilot", inr: 700, days: 30, included_usd: "5" },
    ], codeStatus: 200 });
    await render();
    expect(card("Pro")).toContain("₹1,300");
    expect(card("Pro")).toContain("about ₹43 a day");
    expect(host!.textContent).not.toContain("₹1,200");
    expect(host!.textContent).toContain("Plus gives twice Pro's usage for ₹700 more.");
    await typeCode("firsttrial");
    await submitCode();
    expect(card("First Trial")).toContain("₹700");
    expect(card("First Trial")).toContain("about ₹23 a day");
    expect(host!.textContent).not.toContain("₹649");
  });
});
