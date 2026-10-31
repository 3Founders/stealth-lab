// @vitest-environment jsdom
// Buying Pro (one person, a 30-day pass) and the Pilot (a team, two weeks): every state, the request shape, and that "paid" alone never shows a plan.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

import BuyPlan from "@/components/billing/BuyPlan";
import type { Loaded, PlanState } from "@/lib/billing";
import type { Session } from "@/lib/session";

const SESSION = {} as Session;
const NOW = () => new Date("2026-11-01T10:00:00Z");
const none = (): never => { throw new Error("should not be called"); };
const state = (plan: PlanState["plan"], until: string | null = null, seats = 0, teamName: string | null = null): Loaded<PlanState> => ({ kind: "ok", data: { plan, seats, teamName, activeUntil: until } });
const ORDER = { kind: "ok" as const, data: { orderId: "order_9", amountPaise: 194700, keyId: "rzp_test", plan: "pilot", seats: 3, periodDays: 14 } };

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function mount(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.useRealTimers(); });
const text = () => host!.textContent ?? "";
const btn = (label: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(label)) as HTMLButtonElement | undefined;
const click = async (el: Element | undefined) => { await act(async () => { (el as HTMLElement).click(); }); };
const type = async (id: string, value: string, proto: "input" | "select" = "input") => {
  const el = host!.querySelector(`#${id}`) as HTMLInputElement | HTMLSelectElement;
  const P = proto === "input" ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(P, "value")!.set!.call(el, value); el.dispatchEvent(new Event(proto === "input" ? "input" : "change", { bubbles: true })); });
};

describe("Pro, for one person", () => {
  it("signed out: a sign-in link back where you were, and no buy button", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: none, planOrder: none }} session={null} returnTo="/account/billing" />);
    expect(text()).toContain("Sign in to buy Pro");
    expect(host!.querySelector('a[href="/sign-in?redirect=/account/billing"]')).not.toBeNull();
    expect(host!.querySelector("button")).toBeNull();
  });

  it("billing off (503): says plans aren't switched on and offers Contact, with no buy button", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: async () => ({ kind: "off" }), planOrder: none }} session={SESSION} />);
    expect(text()).toContain("Buying plans isn’t switched on yet");
    expect(btn("Buy Pro")).toBeUndefined();
  });

  it("an ended session and a server error each say so", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: async () => ({ kind: "signed-out" }), planOrder: none }} session={SESSION} />);
    expect(text()).toContain("Your session has ended");
    act(() => root?.unmount()); host?.remove();
    await mount(<BuyPlan plan="pro" api={{ plan: async () => ({ kind: "error", message: "The server answered 500." }), planOrder: none }} session={SESSION} />);
    expect(text()).toContain("The server answered 500.");
  });

  it("no plan yet: the price, the plain 30-day wording, the dates, the terms line and a buy button", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state(null), planOrder: none }} session={SESSION} now={NOW} />);
    expect(text()).toContain("₹999");
    expect(text()).toContain("Renew when it ends");
    expect(text()).toContain("not a subscription");
    expect(text()).toContain("public or non-sensitive material only");
    expect(text()).toContain("1 November 2026");
    expect(text()).toContain("1 December 2026");                                  // 30 days later
    expect(text()).toContain("By paying you accept the Terms and the Refund policy");
    expect(host!.querySelector('a[href="/docs/legal/terms"]')).not.toBeNull();
    expect(host!.querySelector('a[href="/legal/refunds"]')).not.toBeNull();
    expect(btn("Buy Pro (30 days)")).toBeDefined();
  });

  it("the order names the plan and nothing else: no amount, no seats, no team", async () => {
    const planOrder = vi.fn(async (_req?: object) => ORDER);
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state(null), planOrder }} session={SESSION} checkout={async () => "closed"} now={NOW} />);
    await click(btn("Buy Pro (30 days)"));
    expect(planOrder).toHaveBeenCalledWith({ plan: "pro" });
    expect(text()).toContain("window was closed");
  });

  it("active Pro shows the end date and offers Renew", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state("pro", "2026-11-20T00:00:00Z"), planOrder: none }} session={SESSION} now={NOW} />);
    expect(text()).toContain("Pro active until 20 November 2026");
    expect(btn("Renew")).toBeDefined();
    expect(btn("Buy Pro")).toBeUndefined();
  });

  it("an ended Pro says when it ended and can be bought again", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state("pro", "2026-10-01T00:00:00Z"), planOrder: none }} session={SESSION} now={NOW} />);
    expect(text()).toContain("Your Pro ended on 1 October 2026");
    expect(btn("Buy Pro (30 days)")).toBeDefined();
  });

  it("'paid' from Checkout alone shows waiting, not an active plan; the plan appears only when the server says so", async () => {
    vi.useFakeTimers();
    let until: string | null = null;
    const plan = vi.fn(async () => state(until ? "pro" : null, until));
    await mount(<BuyPlan plan="pro" api={{ plan, planOrder: async () => ORDER }} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={5} now={NOW} />);
    await click(btn("Buy Pro (30 days)"));
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Pro active until");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Pro active until");
    until = "2026-12-01T00:00:00Z";                                            // the signed webhook arrives
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("Pro active until 1 December 2026");
  });

  it("renewing is confirmed only when the end date moves", async () => {
    vi.useFakeTimers();
    let until = "2026-11-20T00:00:00Z";
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state("pro", until), planOrder: async () => ORDER }} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={5} now={NOW} />);
    await click(btn("Renew"));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).not.toContain("Payment confirmed");                          // still the old end date: nothing has changed
    until = "2026-12-20T00:00:00Z";
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("Pro active until 20 December 2026");
  });

  it("if confirmation never arrives it says so and offers Contact with the order id", async () => {
    vi.useFakeTimers();
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state(null), planOrder: async () => ORDER }} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={3} now={NOW} />);
    await click(btn("Buy Pro (30 days)"));
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(text()).toContain("haven't had the confirmation yet");
    expect(text()).toContain("order_9");
    expect(host!.querySelector('a[href="/contact"]')).not.toBeNull();
  });

  it("a failed payment, a refused order and an order that finds billing off each say so", async () => {
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state(null), planOrder: async () => ORDER }} session={SESSION} checkout={async () => "failed"} />);
    await click(btn("Buy Pro (30 days)"));
    expect(text()).toContain("didn't go through");
    act(() => root?.unmount()); host?.remove();
    const checkout = vi.fn();
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state(null), planOrder: async () => ({ kind: "error", message: "Unknown plan." }) }} session={SESSION} checkout={checkout} />);
    await click(btn("Buy Pro (30 days)"));
    expect(text()).toContain("Unknown plan.");
    expect(checkout).not.toHaveBeenCalled();
    act(() => root?.unmount()); host?.remove();
    await mount(<BuyPlan plan="pro" api={{ plan: async () => state(null), planOrder: async () => ({ kind: "off" }) }} session={SESSION} checkout={checkout} />);
    await click(btn("Buy Pro (30 days)"));
    expect(text()).toContain("isn’t switched on yet");
  });
});

describe("the Pilot, for a team", () => {
  const api = (planOrder = vi.fn(async (_req?: object) => ORDER), plan = async () => state(null)) => ({ plan, planOrder });

  it("shows the team name field, 3 to 10 seats, the live total, the dates and what is not included, with no dollar figure", async () => {
    await mount(<BuyPlan plan="pilot" api={api()} session={SESSION} now={NOW} />);
    expect(host!.querySelector("#pilot-team")).not.toBeNull();
    const options = [...host!.querySelectorAll("#pilot-seats option")].map((o) => o.textContent);
    expect(options).toEqual(["3", "4", "5", "6", "7", "8", "9", "10"]);
    expect(text()).toContain("₹1,947");                                          // 649 × 3
    await type("pilot-seats", "10", "select");
    expect(text()).toContain("₹6,490");                                          // 649 × 10
    expect(text()).toContain("1 November 2026");
    expect(text()).toContain("15 November 2026");                                // 14 days later
    expect(text()).toContain("Not included: The website assistant room and the team admin pages.");
    expect(text()).not.toMatch(/\$\s?\d/);
    expect(btn("Start the pilot")).toBeDefined();
  });

  it("never sends a request without a team name (or with a one-letter one)", async () => {
    const planOrder = vi.fn(async (_req?: object) => ORDER);
    await mount(<BuyPlan plan="pilot" api={api(planOrder)} session={SESSION} now={NOW} />);
    await click(btn("Start the pilot"));
    expect(text()).toContain("Give the team a name");
    await type("pilot-team", "A");
    await click(btn("Start the pilot"));
    expect(planOrder).not.toHaveBeenCalled();
  });

  it("sends plan, seats and the trimmed team name, and no price", async () => {
    const planOrder = vi.fn(async (_req?: object) => ORDER);
    await mount(<BuyPlan plan="pilot" api={api(planOrder)} session={SESSION} checkout={async () => "closed"} now={NOW} />);
    await type("pilot-team", "  Acme pilot ");
    await type("pilot-seats", "5", "select");
    await click(btn("Start the pilot"));
    expect(planOrder).toHaveBeenCalledWith({ plan: "pilot", seats: 5, teamName: "  Acme pilot " });   // the API layer trims it; the page adds no amount
    expect(Object.keys(planOrder.mock.calls[0][0] as object).sort()).toEqual(["plan", "seats", "teamName"]);
  });

  it("signed out: 'Sign in to start the pilot', back to /pricing", async () => {
    await mount(<BuyPlan plan="pilot" api={api()} session={null} />);
    expect(text()).toContain("Sign in to start the pilot");
    expect(host!.querySelector('a[href="/sign-in?redirect=/pricing"]')).not.toBeNull();
  });

  it("'paid' alone shows waiting; the pilot shows active, with the team and seats, only when the server says so", async () => {
    vi.useFakeTimers();
    let active = false;
    const plan = async () => (active ? state("pilot", "2026-11-15T00:00:00Z", 3, "Acme pilot") : state(null));
    await mount(<BuyPlan plan="pilot" api={api(vi.fn(async (_req?: object) => ORDER), plan)} session={SESSION} checkout={async () => "paid"} waitMs={1000} looks={5} now={NOW} />);
    await type("pilot-team", "Acme pilot");
    await click(btn("Start the pilot"));
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Pilot active until");
    active = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("Pilot active until 15 November 2026 for 3 seats in Acme pilot");
    expect(host!.querySelector('a[href="/docs"]')).not.toBeNull();                 // what to do next
    expect(btn("Start the pilot")).toBeUndefined();                                // and no second pilot
  });

  it("an active pilot shows 'active until' instead of a buy button", async () => {
    await mount(<BuyPlan plan="pilot" api={api(undefined, async () => state("pilot", "2026-11-10T00:00:00Z", 4, "Acme pilot"))} session={SESSION} now={NOW} />);
    expect(text()).toContain("Pilot active until 10 November 2026 for 4 seats in Acme pilot");
    expect(btn("Start the pilot")).toBeUndefined();
    expect(host!.querySelector("#pilot-team")).toBeNull();
  });

  it("an ended pilot is not sold again online: it points to Contact", async () => {
    await mount(<BuyPlan plan="pilot" api={api(undefined, async () => state("pilot", "2026-10-01T00:00:00Z", 4, "Acme pilot"))} session={SESSION} now={NOW} />);
    expect(text()).toContain("Your pilot ended on 1 October 2026");
    expect(btn("Start the pilot")).toBeUndefined();
    expect(host!.querySelector('a[href="/contact"]')).not.toBeNull();
  });

  it("an active Pro does not stop a pilot being bought", async () => {
    await mount(<BuyPlan plan="pilot" api={api(undefined, async () => state("pro", "2026-11-20T00:00:00Z"))} session={SESSION} now={NOW} />);
    expect(btn("Start the pilot")).toBeDefined();
  });
});
