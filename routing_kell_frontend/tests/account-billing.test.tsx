// @vitest-environment jsdom
// /account/billing (a person's own plan, credit and top-ups) and /pricing with plan purchases switched on.
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
  it("signed out, with plan purchases off: says plans aren't sold online, and offers sign-in back to this page for credit", async () => {
    await render(<AccountBillingPage />);
    const t = host!.textContent ?? "";
    expect(t).toContain("Billing");
    expect(t).toContain("Your plan");
    expect(t).toContain("Plans can’t be bought online yet");
    expect(t).toContain("Sign in to add credit");
    expect(hrefs()).toContain("/sign-in?redirect=/account/billing");
    for (const h of ["/pricing", "/docs/legal/terms", "/docs/legal/privacy", "/legal/refunds", "/contact"]) expect(hrefs()).toContain(h);
    expect(host!.querySelector("button")).toBeNull();
  });

  it("with plan purchases on, the Pro purchase sits on the page and sends people back here after sign-in", async () => {
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "on");
    await render(<AccountBillingPage />);
    expect(host!.textContent).toContain("Sign in to buy Pro");
    expect(hrefs().filter((h) => h === "/sign-in?redirect=/account/billing").length).toBe(2);   // Pro and credit
  });
});

describe("/pricing with plan purchases on", () => {
  it("keeps the Pilot and Pro labels off, adds the buy blocks, and leaves Teams and Enterprise as 'contact us'", async () => {
    vi.stubEnv("NEXT_PUBLIC_KELL_BUY_PLANS", "on");
    await render(<PricingPage />);
    const t = host!.textContent ?? "";
    expect(t).not.toContain("Invoiced directly");
    expect(t).toContain("Not available online yet");                                         // Teams
    expect(t).toContain("Planned");                                                           // Enterprise
    expect(t).toContain("Sign in to start the pilot");
    expect(t).toContain("Sign in to buy Pro");
    expect(hrefs()).toContain("#buy-pilot");
    expect(hrefs()).toContain("#buy-pro");
    const contacts = [...host!.querySelectorAll("article a")].filter((a) => a.getAttribute("href") === "/contact");
    expect(contacts.length).toBe(2);                                                          // Teams and Enterprise
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude|ChatGPT|Codex|zero.egress|SOC 2 (compliant|certified)/i);
  });

  it("with the flag off (the default) the cards look as before", async () => {
    await render(<PricingPage />);
    expect(host!.textContent).toContain("Invoiced directly");
    expect(host!.querySelector("#buy-pilot")).toBeNull();
    expect(hrefs()).not.toContain("#buy-pilot");
  });
});
