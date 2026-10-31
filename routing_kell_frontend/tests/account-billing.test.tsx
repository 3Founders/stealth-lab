// @vitest-environment jsdom
// /account/billing (a person's own plan, credit and top-ups) and /pricing with the plan buttons wired to their payment blocks.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));
vi.mock("@/lib/session", () => ({ getAccessToken: async () => null, getSession: async () => null, onSessionChange: () => () => {} }));

import AccountBillingPage from "@/app/account/billing/page";
import PricingPage from "@/app/pricing/page";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.unstubAllEnvs(); });
const hrefs = () => [...host!.querySelectorAll("a")].map((a) => a.getAttribute("href"));

describe("/account/billing", () => {
  it("signed out: the Pro purchase and the credit top-up both offer sign-in back to this page", async () => {
    await render(<AccountBillingPage />);
    const t = host!.textContent ?? "";
    expect(t).toContain("Billing");
    expect(t).toContain("Sign in to buy Pro");
    expect(t).toContain("Sign in to add credit");
    expect(hrefs().filter((h) => h === "/sign-in?redirect=/account/billing").length).toBe(2);
    for (const h of ["/pricing", "/docs/legal/terms", "/docs/legal/privacy", "/legal/refunds", "/contact"]) expect(hrefs()).toContain(h);
    expect(host!.querySelector("button")).toBeNull();
  });

  it("with plan purchases switched off, it says plans go through Contact", async () => {
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "off");
    await render(<AccountBillingPage />);
    expect(host!.textContent).toContain("Plans can’t be bought online yet");
    expect(host!.textContent).not.toContain("Sign in to buy Pro");
  });
});

describe("/pricing", () => {
  it("wires the Pilot and Pro buttons to their payment blocks and leaves Teams and Enterprise as 'contact us'", async () => {
    await render(<PricingPage />);
    const t = host!.textContent ?? "";
    expect(hrefs()).toContain("#buy-pilot");
    expect(hrefs()).toContain("#buy-pro");
    expect(host!.querySelector("#buy-pilot")).not.toBeNull();
    expect(host!.querySelector("#buy-pro")).not.toBeNull();
    expect(t).toContain("Sign in to start the pilot");
    expect(t).toContain("Sign in to buy Pro");
    const contacts = [...host!.querySelectorAll("article a")].filter((a) => a.getAttribute("href") === "/contact");
    expect(contacts.length).toBe(2);                                                           // Teams and Enterprise
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude|ChatGPT|Codex|zero.egress|SOC 2 (compliant|certified)/i);
  });

  it("the cards end with their button: no status caption and no 'not included' lines", async () => {
    await render(<PricingPage />);
    const cards = [...host!.querySelectorAll("article")];
    expect(cards.length).toBe(4);
    for (const c of cards) {
      expect(c.querySelector(".caption")).toBeNull();
      expect(c.querySelector("ul.dim")).toBeNull();
      expect(c.lastElementChild?.tagName).toBe("A");
    }
    const t = cards.map((c) => c.textContent).join(" ");
    expect(t).not.toContain("Not available online yet");
    expect(t).not.toContain("Invoiced directly");
    expect(t).not.toContain("Public or non-sensitive material only");
  });

  it("with purchases switched off, the cards link to Contact and there are no payment blocks", async () => {
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "off");
    await render(<PricingPage />);
    expect(host!.querySelector("#buy-pilot")).toBeNull();
    expect(hrefs()).not.toContain("#buy-pilot");
    expect([...host!.querySelectorAll("article a")].every((a) => a.getAttribute("href") === "/contact")).toBe(true);
  });
});
