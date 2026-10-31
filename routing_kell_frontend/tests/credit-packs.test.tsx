// @vitest-environment jsdom
// Additional kell credits: the cards come from the wallet answer, every state in docs/website_brief_2026-11.md, the order request names only the pack,
// and a "paid" message from Checkout alone never changes the balance.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

import CreditPacks from "@/components/billing/CreditPacks";
import type { Loaded, PlanState, Wallet } from "@/lib/billing";
import type { Session } from "@/lib/session";

const SESSION = {} as Session;
const NOW = () => new Date("2026-11-01T10:00:00Z");
const PACKS = [
  { id: "credits_500", priceInr: 500, creditUsd: 2.5, inrPerUsd: 200 },
  { id: "credits_1000", priceInr: 1000, creditUsd: 5.25, inrPerUsd: 190.5 },
  { id: "credits_2500", priceInr: 2500, creditUsd: 13.5, inrPerUsd: 185.2 },
  { id: "credits_5000", priceInr: 5000, creditUsd: 28, inrPerUsd: 178.6 },
];
const wallet = (over: Partial<Wallet> = {}, credits = 0): Loaded<Wallet> => ({
  kind: "ok",
  data: { purchasedUsd: 0, spentUsd: 0, remainingUsd: 4.25, credits: Array.from({ length: credits }, (_, i) => ({ ref: `p${i}`, source: "razorpay", inr: 1000, at: "2026-10-30T00:00:00Z" })), packs: PACKS, packsNeedPlan: true, freeformTopup: false, ...over },
});
const plan = (p: PlanState["plan"], until: string | null): Loaded<PlanState> => ({ kind: "ok", data: { plan: p, seats: 0, teamName: null, activeUntil: until } });
const ACTIVE = plan("pro", "2026-11-20T00:00:00Z");
const NOPLAN = plan(null, null);
const ORDER = { kind: "ok" as const, data: { orderId: "order_1", amountPaise: 100000, keyId: "rzp_test", pack: "credits_1000", creditUsd: 5.25 } };
const none = (): never => { throw new Error("should not be called"); };

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function mount(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.useRealTimers(); });
const text = () => host!.textContent ?? "";
const btn = (label: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(label)) as HTMLButtonElement | undefined;
const buyButtons = () => [...host!.querySelectorAll("article button")] as HTMLButtonElement[];
const click = async (el: Element | undefined) => { await act(async () => { (el as HTMLElement).click(); }); };

describe("with purchases off: the packs are information, nothing can be bought", () => {
  it("shows the four packs and no buy button, signed in or out", async () => {
    await mount(<CreditPacks api={{ wallet: none, plan: none, packOrder: none }} session={SESSION} buy={false} />);
    expect(host!.querySelectorAll("article").length).toBe(4);
    expect(text()).toContain("₹500");
    expect(text()).toContain("$2.50 of credit");
    expect(text()).toContain("$28.00 of credit");
    expect(text()).toContain("₹178.6 per dollar of credit");
    expect(text()).toContain("Buying credits isn’t switched on yet");
    expect(host!.querySelector("button")).toBeNull();
    expect(text()).not.toContain("Sign in to add credit");
  });
});

describe("with purchases on", () => {
  it("signed out: a sign-in link back where you were, the packs as information, and no buy button", async () => {
    await mount(<CreditPacks api={{ wallet: none, plan: none, packOrder: none }} session={null} />);
    expect(text()).toContain("Sign in to add credit");
    expect(host!.querySelector('a[href="/sign-in?redirect=/pricing"]')).not.toBeNull();
    expect(host!.querySelector("button")).toBeNull();
  });

  it("billing off (503): says so, shows the packs as information and no buy button", async () => {
    await mount(<CreditPacks api={{ wallet: async () => ({ kind: "off" }), plan: async () => ({ kind: "off" }), packOrder: none }} session={SESSION} />);
    expect(text()).toContain("Buying credits isn’t switched on yet");
    expect(host!.querySelectorAll("article").length).toBe(4);
    expect(host!.querySelector("button")).toBeNull();
  });

  it("an ended session and a server error each say so", async () => {
    await mount(<CreditPacks api={{ wallet: async () => ({ kind: "signed-out" }), plan: async () => ACTIVE, packOrder: none }} session={SESSION} />);
    expect(text()).toContain("Your session has ended");
    act(() => root?.unmount()); host?.remove();
    await mount(<CreditPacks api={{ wallet: async () => ({ kind: "error", message: "The server answered 500." }), plan: async () => ACTIVE, packOrder: none }} session={SESSION} />);
    expect(text()).toContain("The server answered 500.");
  });

  it("draws the cards from the wallet answer, not from a built-in list", async () => {
    const two = [{ id: "credits_x", priceInr: 750, creditUsd: 4, inrPerUsd: 187.5 }, { id: "credits_y", priceInr: 3000, creditUsd: 16, inrPerUsd: 187.5 }];
    await mount(<CreditPacks api={{ wallet: async () => wallet({ packs: two }), plan: async () => ACTIVE, packOrder: none }} session={SESSION} now={NOW} />);
    expect(host!.querySelectorAll("article").length).toBe(2);
    expect(text()).toContain("₹750");
    expect(text()).toContain("$4.00 of credit");
    expect(text()).toContain("₹3,000");
    expect(text()).not.toContain("₹500");
    expect(text()).toContain("Balance: $4.25");
  });

  it("with no active plan the cards are disabled with 'Available with an active plan' and a link to the plans", async () => {
    const packOrder = vi.fn(async (_id: string) => ORDER);
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => NOPLAN, packOrder }} session={SESSION} now={NOW} />);
    expect(buyButtons().length).toBe(4);
    for (const b of buyButtons()) { expect(b.disabled).toBe(true); expect(b.textContent).toContain("Available with an active plan"); }
    expect(host!.querySelector('a[href="/pricing#plans-title"]')).not.toBeNull();
    await click(buyButtons()[0]);
    expect(packOrder).not.toHaveBeenCalled();
  });

  it("an ended plan counts as no plan", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => plan("plus", "2026-10-01T00:00:00Z"), packOrder: none }} session={SESSION} now={NOW} />);
    for (const b of buyButtons()) expect(b.disabled).toBe(true);
  });

  it("when the server does not require a plan, the cards are open without one", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet({ packsNeedPlan: false }), plan: async () => NOPLAN, packOrder: none }} session={SESSION} now={NOW} />);
    for (const b of buyButtons()) expect(b.disabled).toBe(false);
  });

  it("with an active plan: the balance, the rules in words, the legal line, and a buy button on every card", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => ACTIVE, packOrder: none }} session={SESSION} now={NOW} />);
    expect(buyButtons().length).toBe(4);
    for (const b of buyButtons()) expect(b.disabled).toBe(false);
    expect(text()).toContain("does not expire");
    expect(text()).toContain("can’t be transferred or withdrawn");
    expect(text()).toContain("a little more credit per rupee");
    expect(text()).toContain("By paying you accept the Terms and the Refund policy");
    expect(host!.querySelector('a[href="/docs/legal/terms"]')).not.toBeNull();
    expect(host!.querySelector('a[href="/legal/refunds"]')).not.toBeNull();
  });

  it("has no field for an amount and no preset or custom top-up", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet({ freeformTopup: true }), plan: async () => ACTIVE, packOrder: none }} session={SESSION} now={NOW} />);
    expect(host!.querySelector("input")).toBeNull();
    expect(text()).not.toMatch(/Amount in rupees|custom amount|preset/i);
  });

  it("makes no claim that a plan always beats a pack, and none of the ruled-out claims", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => ACTIVE, packOrder: none }} session={SESSION} now={NOW} />);
    expect(text()).not.toMatch(/always the better value|best value|zero data retention|SOC 2|bring your own key|Claude|ChatGPT/i);
  });
});

describe("buying a pack", () => {
  it("the order names the pack and nothing else, and a closed window charges nothing and adds nothing", async () => {
    const packOrder = vi.fn(async (_id: string) => ORDER);
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => ACTIVE, packOrder }} session={SESSION} checkout={async () => "closed"} now={NOW} />);
    await click(buyButtons()[1]);
    expect(packOrder).toHaveBeenCalledWith("credits_1000");
    expect(packOrder.mock.calls[0]).toHaveLength(1);                              // only the pack id
    expect(text()).toContain("window was closed");
    expect(text()).toContain("Balance: $4.25");
  });

  it("a failed payment says no credit was bought", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => ACTIVE, packOrder: async () => ORDER }} session={SESSION} checkout={async () => "failed"} now={NOW} />);
    await click(buyButtons()[0]);
    expect(text()).toContain("didn't go through");
  });

  it("an order the server refuses (no plan, 403) shows its sentence and never opens Checkout", async () => {
    const checkout = vi.fn();
    const msg = "additional credits are for people with an active plan; buy a plan first";
    await mount(<CreditPacks api={{ wallet: async () => wallet({ packsNeedPlan: false }), plan: async () => NOPLAN, packOrder: async () => ({ kind: "error", message: msg }) }} session={SESSION} checkout={checkout} now={NOW} />);
    await click(buyButtons()[0]);
    expect(text()).toContain(msg);
    expect(checkout).not.toHaveBeenCalled();
  });

  it("billing switched off at order time returns to the 'not switched on' state", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => ACTIVE, packOrder: async () => ({ kind: "off" }) }} session={SESSION} checkout={vi.fn()} now={NOW} />);
    await click(buyButtons()[0]);
    expect(text()).toContain("isn’t switched on yet");
    expect(host!.querySelector("article button")).toBeNull();
  });

  it("'paid' from Checkout alone shows waiting, not a new balance; the credit shows only when the server's wallet changes", async () => {
    vi.useFakeTimers();
    let credits = 0;
    let remaining = 4.25;
    const walletFn = vi.fn(async () => wallet({ remainingUsd: remaining }, credits));
    await mount(<CreditPacks api={{ wallet: walletFn, plan: async () => ACTIVE, packOrder: async () => ORDER }} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={5} now={NOW} />);
    await click(buyButtons()[1]);
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).toContain("Balance: $4.25");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Payment confirmed");
    credits = 1; remaining = 9.5;                                                  // the signed webhook arrives
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("Balance: $9.50");
  });

  it("if confirmation never arrives it says so and offers Contact with the order id", async () => {
    vi.useFakeTimers();
    await mount(<CreditPacks api={{ wallet: async () => wallet(), plan: async () => ACTIVE, packOrder: async () => ORDER }} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={3} now={NOW} />);
    await click(buyButtons()[0]);
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(text()).toContain("haven't had the confirmation yet");
    expect(text()).toContain("order_1");
    expect(host!.querySelector('a[href="/contact"]')).not.toBeNull();
  });
});

describe("the account billing page lists what was added", () => {
  it("shows each credit with its amount, date and reference", async () => {
    await mount(<CreditPacks api={{ wallet: async () => wallet({}, 2), plan: async () => ACTIVE, packOrder: none }} session={SESSION} history returnTo="/account/billing" now={NOW} />);
    expect(text()).toContain("Credits added so far");
    expect(host!.querySelectorAll('ul[aria-label="Credits added so far"] li').length).toBe(2);
    expect(text()).toContain("₹1,000");
    expect(text()).toContain("30 October 2026");
    expect(text()).toContain("ref p0");
  });
  it("signed out, the sign-in link returns to the billing page", async () => {
    await mount(<CreditPacks api={{ wallet: none, plan: none, packOrder: none }} session={null} returnTo="/account/billing" />);
    expect(host!.querySelector('a[href="/sign-in?redirect=/account/billing"]')).not.toBeNull();
  });
});
