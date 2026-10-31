// @vitest-environment jsdom
// Buying a plan inside its own card (Starter, Pro, Plus; 30 days): every state, the request shape, and that "paid" alone never shows a plan.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("next/link", () => ({ default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> }));

import PlanBuy from "@/components/billing/PlanBuy";
import type { Loaded, PlanState } from "@/lib/billing";
import type { Session } from "@/lib/session";
import type { BuyablePlanId } from "@/lib/plans";

const SESSION = {} as Session;
const NOW = () => new Date("2026-11-01T10:00:00Z");
const none = (): never => { throw new Error("should not be called"); };
const ok = (plan: PlanState["plan"], until: string | null = null): Loaded<PlanState> => ({ kind: "ok", data: { plan, activeUntil: until } });
const orderFor = (plan: string) => ({ kind: "ok" as const, data: { orderId: "order_9", amountPaise: 120000, keyId: "rzp_test", plan, periodDays: 30 } });

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

/** The card's controls with a state that can change when reload() is called, as the page does. */
function Harness({ plan, states, planOrder = vi.fn(async (_req?: object) => orderFor(plan)), session = SESSION, checkout, waitMs = 1000, looks = 5 }: {
  plan: BuyablePlanId; states: Array<Loaded<PlanState> | null>; planOrder?: ReturnType<typeof vi.fn>; session?: Session | null | undefined;
  checkout?: () => Promise<"paid" | "closed" | "failed">; waitMs?: number; looks?: number;
}) {
  const [i, setI] = React.useState(0);
  const idx = React.useRef(0);
  const reload = async () => { idx.current = Math.min(idx.current + 1, states.length - 1); setI(idx.current); return states[idx.current] ?? states[0]!; };
  return <PlanBuy plan={plan} api={{ planOrder: planOrder as never }} session={session} state={states[i]} reload={reload} checkout={checkout} waitMs={waitMs} looks={looks} now={NOW} />;
}
const at = (state: Loaded<PlanState> | null, over: Partial<React.ComponentProps<typeof Harness>> = {}) => <Harness plan="plus" states={[state]} {...over} />;

describe("in the plan's own card", () => {
  it("signed out: a sign-in link back to /pricing and no buy button", async () => {
    await mount(at(null, { session: null }));
    expect(text()).toContain("Sign in to buy Plus");
    expect(host!.querySelector('a[href="/sign-in?redirect=/pricing"]')).not.toBeNull();
    expect(host!.querySelector("button")).toBeNull();
  });

  it("checking the session and the plan show a plain status, with no buy button", async () => {
    await mount(<PlanBuy plan="plus" api={{ planOrder: none }} session={undefined} state={null} reload={async () => ok(null)} />);
    expect(text()).toContain("Checking your session");
    act(() => root?.unmount()); host?.remove();
    await mount(at(null));
    expect(text()).toContain("Checking your plan");
    expect(host!.querySelector("button")).toBeNull();
  });

  it("billing off (503): says plans aren't switched on and offers Contact, with no buy button", async () => {
    await mount(at({ kind: "off" }));
    expect(text()).toContain("Buying plans isn’t switched on yet");
    expect(host!.querySelector('a[href="/contact"]')).not.toBeNull();
    expect(btn("Buy Plus")).toBeUndefined();
  });

  it("an ended session and a server error each say so", async () => {
    await mount(at({ kind: "signed-out" }));
    expect(text()).toContain("Your session has ended");
    act(() => root?.unmount()); host?.remove();
    await mount(at({ kind: "error", message: "The server answered 500." }));
    expect(text()).toContain("The server answered 500.");
  });

  it("no plan yet: the 30-day dates, 'not a subscription', the terms line and a buy button", async () => {
    await mount(at(ok(null)));
    expect(text()).toContain("1 November 2026");
    expect(text()).toContain("1 December 2026");                                   // 30 days later, from the plan's period_days
    expect(text()).toContain("30 days");
    expect(text()).toContain("not a subscription");
    expect(text()).toContain("By paying you accept the Terms and the Refund policy");
    expect(host!.querySelector('a[href="/docs/legal/terms"]')).not.toBeNull();
    expect(host!.querySelector('a[href="/legal/refunds"]')).not.toBeNull();
    expect(btn("Buy Plus")).toBeDefined();
    expect(text()).not.toMatch(/pilot|seat|team name|bring your own key|zero data retention/i);
  });

  it("the order names the plan and nothing else: no amount, no seats, no team", async () => {
    const planOrder = vi.fn(async (_req?: object) => orderFor("plus"));
    await mount(at(ok(null), { planOrder, checkout: async () => "closed" }));
    await click(btn("Buy Plus"));
    expect(planOrder).toHaveBeenCalledWith({ plan: "plus" });
    expect(Object.keys(planOrder.mock.calls[0][0] as object)).toEqual(["plan"]);
    expect(text()).toContain("window was closed");
  });

  for (const [plan, name] of [["starter", "Starter"], ["pro", "Pro"], ["plus", "Plus"]] as const) {
    it(`${name}: the button and the order carry that plan`, async () => {
      const planOrder = vi.fn(async (_req?: object) => orderFor(plan));
      await mount(<Harness plan={plan} states={[ok(null)]} planOrder={planOrder} checkout={async () => "closed"} />);
      await click(btn(`Buy ${name}`));
      expect(planOrder).toHaveBeenCalledWith({ plan });
    });
  }

  it("keeps an empty, hidden slot for a referral code and shows no code field", async () => {
    await mount(at(ok(null)));
    const slot = host!.querySelector('[data-slot="have-a-code"]') as HTMLElement;
    expect(slot.hidden).toBe(true);
    expect(slot.childElementCount).toBe(0);
    expect(text()).not.toMatch(/have a code|promo|referral|discount/i);
    expect(host!.querySelector("input")).toBeNull();
  });

  it("an active plan shows the end date and offers Renew, not Buy", async () => {
    await mount(at(ok("plus", "2026-11-20T00:00:00Z")));
    expect(text()).toContain("Plus active until 20 November 2026");
    expect(btn("Renew")).toBeDefined();
    expect(btn("Buy Plus")).toBeUndefined();
  });

  it("an ended plan says when it ended and can be bought again", async () => {
    await mount(at(ok("plus", "2026-10-01T00:00:00Z")));
    expect(text()).toContain("Your Plus ended on 1 October 2026");
    expect(btn("Buy Plus")).toBeDefined();
  });

  it("a higher plan running says both run, with the higher applying, and a lower one can still be bought", async () => {
    await mount(<Harness plan="starter" states={[ok("plus", "2026-11-25T00:00:00Z")]} />);
    expect(text()).toContain("Plus is active until 25 November 2026");
    expect(text()).toContain("If you buy Starter too, both run, and the higher plan applies");
    expect(btn("Buy Starter")).toBeDefined();
  });
});

describe("paying", () => {
  it("'paid' from Checkout alone shows waiting, not an active plan; the plan appears only when the server says so", async () => {
    vi.useFakeTimers();
    await mount(<Harness plan="plus" states={[ok(null), ok(null), ok("plus", "2026-12-01T00:00:00Z")]} checkout={async () => "paid"} />);
    await click(btn("Buy Plus"));
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Plus active until");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Waiting for it to be confirmed");
    expect(text()).not.toContain("Plus active until");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });                // the signed webhook has arrived
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("Plus active until 1 December 2026");
  });

  it("renewing is confirmed only when the end date moves", async () => {
    vi.useFakeTimers();
    await mount(<Harness plan="plus" states={[ok("plus", "2026-11-20T00:00:00Z"), ok("plus", "2026-11-20T00:00:00Z"), ok("plus", "2026-12-20T00:00:00Z")]} checkout={async () => "paid"} />);
    await click(btn("Renew"));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).not.toContain("Payment confirmed");                                  // still the old end date: nothing has changed
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(text()).toContain("Payment confirmed");
    expect(text()).toContain("Plus active until 20 December 2026");
  });

  it("if confirmation never arrives it says so and offers Contact with the order id", async () => {
    vi.useFakeTimers();
    await mount(<Harness plan="plus" states={[ok(null)]} checkout={async () => "paid"} looks={3} />);
    await click(btn("Buy Plus"));
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(text()).toContain("haven't had the confirmation yet");
    expect(text()).toContain("order_9");
    expect(host!.querySelector('a[href="/contact"]')).not.toBeNull();
  });

  it("a failed payment, a refused order and an order that finds billing off each say so", async () => {
    await mount(at(ok(null), { checkout: async () => "failed" }));
    await click(btn("Buy Plus"));
    expect(text()).toContain("didn't go through");
    act(() => root?.unmount()); host?.remove();
    const checkout = vi.fn(async () => "paid" as const);
    await mount(at(ok(null), { planOrder: vi.fn(async () => ({ kind: "error" as const, message: "Unknown plan." })), checkout }));
    await click(btn("Buy Plus"));
    expect(text()).toContain("Unknown plan.");
    expect(checkout).not.toHaveBeenCalled();
    act(() => root?.unmount()); host?.remove();
    await mount(at(ok(null), { planOrder: vi.fn(async () => ({ kind: "off" as const })), checkout }));
    await click(btn("Buy Plus"));
    expect(text()).toContain("isn’t switched on yet");
    expect(checkout).not.toHaveBeenCalled();
  });

  it("there is no payment block: the card holds the button and nothing renders a heading or a form of its own", async () => {
    await mount(at(ok(null)));
    expect(host!.querySelector("h2, h3, form")).toBeNull();
    expect(host!.querySelector("#buy-plus")).toBeNull();
  });
});
