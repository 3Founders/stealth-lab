"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Session } from "@/lib/session";
import { addDays, anyPlanActive, openCheckout, plainDate, planActive, type Loaded, type PlanState, type billingApi } from "@/lib/billing";
import { planById, type BuyablePlanId } from "@/lib/plans";

const link = { textDecoration: "underline" } as const;

type Api = Pick<ReturnType<typeof billingApi>, "planOrder">;

/** About a minute: 24 looks, 2.5 s apart. */
export const PLAN_WAIT_LOOKS = 24;
export const PLAN_WAIT_MS = 2500;

/**
 * The purchase controls inside a plan's card: the buy button and everything that happens around it. There is no separate payment block;
 * the card is where a person pays. Built to the contract in docs/pricing_page.md and docs/website_brief_2026-11.md:
 *  - the request names the plan and nothing else; the server works out the amount (a plan is a 30-day pass paid once and renewed by hand,
 *    not a subscription);
 *  - a payment is not a plan: when Checkout reports success we say we are waiting and look at GET /v1/billing/plan again; only the server's
 *    signed webhook makes a plan active, and a renewal is confirmed only when `active_until` moves, so a "paid" message alone shows nothing;
 *  - the days come from the plan's data (`periodDays`).
 * `state` is the account's plan as the page loaded it (null while loading) and `reload` loads it again and returns it; one request serves
 * every card. `session` is undefined while it is being checked, null when signed out. Shown only when NEXT_PUBLIC_KELL_BUY_PLANS=on.
 */
export default function PlanBuy({ plan, api, session, state, reload, returnTo = "/pricing", checkout = openCheckout, waitMs = PLAN_WAIT_MS, looks = PLAN_WAIT_LOOKS, now }: {
  plan: BuyablePlanId; api: Api; session: Session | null | undefined;
  state: Loaded<PlanState> | null; reload: () => Promise<Loaded<PlanState>>;
  returnTo?: string; checkout?: typeof openCheckout; waitMs?: number; looks?: number; now?: () => Date;
}) {
  const p = planById(plan);
  const days = p.periodDays ?? 30;
  const clock = now ?? (() => new Date());
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [orderId, setOrderId] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; if (timer.current) clearTimeout(timer.current); }; }, []);

  const current = state?.kind === "ok" ? state.data : null;
  const active = planActive(current, plan, clock());
  const otherActive = !active && anyPlanActive(current, clock());              // another plan runs now (the server names the higher one)
  const ended = current?.plan === plan && !active;
  const signInHref = `/sign-in?redirect=${returnTo}`;

  // After Checkout reports a payment, the plan appears when the provider's webhook reaches the server: look a number of times, then say so.
  function waitForPlan(beforeUntil: string | null, id: string, tries = 0) {
    timer.current = setTimeout(async () => {
      const s = await reload();
      if (!live.current) return;
      if (s.kind === "ok" && planActive(s.data, plan, clock()) && s.data.activeUntil !== beforeUntil) { setNote(`Payment confirmed. ${p.name} is active.`); setOrderId(""); return; }
      if (tries + 1 >= looks) { setNote("We haven't had the confirmation yet. Your plan starts as soon as it arrives."); setOrderId(id); return; }
      waitForPlan(beforeUntil, id, tries + 1);
    }, waitMs);
  }

  async function buy() {
    if (busy) return;
    setBusy(true); setNote(""); setOrderId("");
    const beforeUntil = current?.plan === plan ? current.activeUntil : null;
    const order = await api.planOrder({ plan });
    if (order.kind === "off") { await reload(); setNote("Buying plans isn’t switched on yet. Nothing was charged."); setBusy(false); return; }
    if (order.kind === "signed-out") { setNote("Your session has ended. Sign in again to buy."); setBusy(false); return; }
    if (order.kind === "error") { setNote(order.message); setBusy(false); return; }
    const result = await checkout(order.data, `keळ ${p.name}, ${days} days`);
    if (!live.current) return;
    setBusy(false);
    if (result === "paid") { setNote("Payment received. Waiting for it to be confirmed…"); waitForPlan(beforeUntil, order.data.orderId); }
    else if (result === "closed") setNote("The payment window was closed. You haven't been charged.");
    else setNote("The payment didn't go through. You haven't been charged; try again.");
  }

  return (
    <div className="plan-buy" style={{ display: "grid", gap: 8 }}>
      {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && <Link className="btn-line" href={signInHref}><span>Sign in to buy {p.name}</span></Link>}
      {session && state === null && <p className="small dim" role="status">Checking your plan…</p>}
      {session && state?.kind === "off" && (
        <p className="small" role="status"><b>Buying plans isn&rsquo;t switched on yet.</b> Nothing is charged until it is. <Link href="/contact" style={link}>Ask us</Link> and we will set it up with you directly.</p>
      )}
      {session && state?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href={signInHref} style={link}>Sign in again</Link>.</p>}
      {session && state?.kind === "error" && <p className="small" role="alert">{state.message} <button type="button" className="btn-line" onClick={() => void reload()}>Try again</button></p>}

      {session && state?.kind === "ok" && (
        <>
          {active && current && <p className="small" role="status"><b>{p.name} active until {plainDate(current.activeUntil!)}.</b></p>}
          {otherActive && current?.plan && <p className="small dim" role="status">{planById(current.plan).name} is active until {plainDate(current.activeUntil!)}. If you buy {p.name} too, both run, and the higher plan applies.</p>}
          {ended && current?.activeUntil && <p className="small" role="status">Your {p.name} ended on {plainDate(current.activeUntil)}.</p>}
          <p className="small dim">Runs from today, {plainDate(clock())}, to {plainDate(addDays(clock(), days))} ({days} days). A {days}-day pass paid once, not a subscription.</p>
          {/* Reserved for referral codes, which are not built (docs/referral_legality_2026-11.md). Empty and hidden on purpose. */}
          <div data-slot="have-a-code" hidden />
          <button className="btn-ink" type="button" disabled={busy} onClick={() => void buy()}>
            <span>{busy ? "Opening payment…" : active ? "Renew" : `Buy ${p.name}`}</span><span className="sq" aria-hidden="true">→</span>
          </button>
          {note && <p className="small" role="status">{note}</p>}
          {orderId && <p className="small dim">If your bank has taken the money and no plan appears, <Link href="/contact" style={link}>contact us</Link> and quote order <code>{orderId}</code>.</p>}
          <p className="small dim">By paying you accept the <Link href="/docs/legal/terms" style={link}>Terms</Link> and the <Link href="/legal/refunds" style={link}>Refund policy</Link>.</p>
        </>
      )}
    </div>
  );
}
