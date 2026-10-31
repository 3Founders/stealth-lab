// @vitest-environment jsdom
// The credit top-up on /pricing: every state, the shape of the order request, and that "paid" from Checkout alone never changes the balance.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

import TopUp from "@/components/billing/TopUp";
import type { Loaded, Wallet } from "@/lib/billing";
import type { Session } from "@/lib/session";

const SESSION = {} as Session;
const wallet = (credits = 0, remainingUsd = 1): Loaded<Wallet> => ({
  kind: "ok",
  data: { purchasedUsd: 1, spentUsd: 0, remainingUsd, usdPerInr: 0.01, minInr: 100, maxInr: 50000, credits: Array.from({ length: credits }, (_, i) => ({ ref: `p${i}`, source: "razorpay", inr: 100, at: "" })) },
});
const ORDER = { kind: "ok" as const, data: { orderId: "order_1", amountPaise: 50000, keyId: "rzp_test" } };
const none = (): never => { throw new Error("should not be called"); };

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function mount(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.useRealTimers(); });
const text = () => host!.textContent ?? "";
const pay = () => [...host!.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Pay")) as HTMLButtonElement | undefined;
const click = async (el: Element | undefined) => { await act(async () => { (el as HTMLElement).click(); }); };

describe("credit top-up states", () => {
  it("signed out: a sign-in link back to /pricing and no Pay button", async () => {
    await mount(<TopUp api={{ wallet: none, order: none }} session={null} />);
    expect(text()).toContain("Sign in to add credit");
    expect(host!.querySelector('a[href="/sign-in?redirect=/pricing"]')).not.toBeNull();
    expect(pay()).toBeUndefined();
  });

  it("billing off (503): says buying isn't switched on, with no Pay button", async () => {
    await mount(<TopUp api={{ wallet: async () => ({ kind: "off" }), order: none }} session={SESSION} />);
    expect(text()).toContain("Buying credits isn’t switched on yet");
    expect(pay()).toBeUndefined();
  });

  it("an ended session and a server error each say so", async () => {
    await mount(<TopUp api={{ wallet: async () => ({ kind: "signed-out" }), order: none }} session={SESSION} />);
    expect(text()).toContain("Your session has ended");
    act(() => root?.unmount()); host?.remove();
    await mount(<TopUp api={{ wallet: async () => ({ kind: "error", message: "The server answered 500." }), order: none }} session={SESSION} />);
    expect(text()).toContain("The server answered 500.");
  });

  it("signed in: the balance in rupees, the legal line above Pay, and links to Terms and the Refund policy", async () => {
    await mount(<TopUp api={{ wallet: async () => wallet(2, 3), order: none }} session={SESSION} />);
    expect(text()).toContain("₹300");
    expect(text()).toContain("2 top-ups so far");
    expect(text()).toContain("By paying you accept the Terms and the Refund policy");
    expect(host!.querySelector('a[href="/docs/legal/terms"]')).not.toBeNull();
    expect(host!.querySelector('a[href="/legal/refunds"]')).not.toBeNull();
    expect(pay()?.textContent).toContain("₹500");
  });
});

describe("paying", () => {
  it("sends the amount only, and a closed window charges nothing and adds nothing", async () => {
    const order = vi.fn(async () => ORDER);
    const checkout = vi.fn(async () => "closed" as const);
    await mount(<TopUp api={{ wallet: async () => wallet(1), order }} session={SESSION} checkout={checkout} />);
    await click(pay());
    expect(order).toHaveBeenCalledWith(500);              // the amount to top up, nothing that sets a price
    expect(checkout).toHaveBeenCalledWith(ORDER.data);
    expect(text()).toContain("window was closed");
    expect(text()).toContain("1 top-up so far");
  });

  it("a failed payment says no credit was bought", async () => {
    await mount(<TopUp api={{ wallet: async () => wallet(0), order: async () => ORDER }} session={SESSION} checkout={async () => "failed"} />);
    await click(pay());
    expect(text()).toContain("didn't go through");
  });

  it("an order the server refuses shows its sentence and never opens Checkout", async () => {
    const checkout = vi.fn();
    await mount(<TopUp api={{ wallet: async () => wallet(0), order: async () => ({ kind: "error", message: "That amount is not allowed." }) }} session={SESSION} checkout={checkout} />);
    await click(pay());
    expect(text()).toContain("That amount is not allowed.");
    expect(checkout).not.toHaveBeenCalled();
  });

  it("billing switched off at order time returns to the 'not switched on' state", async () => {
    await mount(<TopUp api={{ wallet: async () => wallet(0), order: async () => ({ kind: "off" }) }} session={SESSION} checkout={vi.fn()} />);
    await click(pay());
    expect(text()).toContain("isn’t switched on yet");
    expect(pay()).toBeUndefined();
  });

  it("refuses an amount outside the range before any request", async () => {
    const order = vi.fn();
    await mount(<TopUp api={{ wallet: async () => wallet(0), order }} session={SESSION} />);
    const input = host!.querySelector("#b-amount") as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "50");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(pay()?.disabled).toBe(true);
    expect(text()).toContain("The smallest top-up is ₹100.");
    expect(order).not.toHaveBeenCalled();
  });

  it("'paid' from Checkout alone shows waiting, not a new balance; the balance changes only when the server's wallet does", async () => {
    vi.useFakeTimers();
    let credits = 0;
    const api = { wallet: vi.fn(async () => wallet(credits)), order: async () => ORDER };
    await mount(<TopUp api={api} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={5} />);
    await click(pay());
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Payment confirmed");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Waiting for it to be confirmed");     // the wallet has not changed, so nothing changes here
    expect(text()).toContain("0 top-ups so far");
    credits = 1;                                                   // the signed webhook arrives
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("1 top-up so far");
  });

  it("if confirmation never arrives it says so and offers Contact with the order id", async () => {
    vi.useFakeTimers();
    await mount(<TopUp api={{ wallet: async () => wallet(0), order: async () => ORDER }} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={3} />);
    await click(pay());
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(text()).toContain("haven't had the confirmation yet");
    expect(text()).toContain("order_1");
    expect(host!.querySelector('a[href="/contact"]')).not.toBeNull();
  });
});
