// @vitest-environment jsdom
// /pricing: the founders' plans, none buyable online yet, each with a way to ask; no credits, balance or checkout on the page.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import PricingPage from "@/app/pricing/page";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<PricingPage />); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("/pricing", () => {
  it("shows the four plans with the founders' prices, none buyable online, and a way to ask", async () => {
    await render();
    const t = host!.textContent ?? "";
    for (const price of ["₹999", "₹1,800", "₹649", "Custom"]) expect(t).toContain(price);
    expect(t).toContain("before GST");
    expect(t).toContain("Not available online yet");
    expect(t).toContain("Invoiced directly");
    const asks = [...host!.querySelectorAll("article a")].map((a) => a.getAttribute("href"));
    expect(asks).toEqual(["/contact", "/contact", "/contact", "/contact"]);
    expect(host!.querySelector("button")).toBeNull();
  });

  it("has no credits, balance or checkout, and makes no claim it cannot back", async () => {
    await render();
    const t = host!.textContent ?? "";
    expect(t).not.toMatch(/credit|balance|top-up|checkout|razorpay/i);
    expect(t).not.toMatch(/half the price|% (cheaper|less|saving)|Claude|ChatGPT|per developer|zero.egress|SOC 2 (compliant|certified)/i);
    expect(t).toContain("does not yet have a SOC 2 report");
  });
});
