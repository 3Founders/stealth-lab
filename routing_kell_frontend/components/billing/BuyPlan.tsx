"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@/lib/session";
import {
  PILOT_MAX_SEATS, PILOT_MIN_SEATS, TEAM_FROM_SEATS, TEAM_NAME_MAX, addDays, anyPlanActive, gstMode, gstSentence, openCheckout, plainDate, planActive,
  seatsProblem, teamNameProblem, type Loaded, type PlanName, type PlanState, type billingApi,
} from "@/lib/billing";
import { planById, usageBuys, type BuyablePlanId } from "@/lib/plans";

const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const link = { textDecoration: "underline" } as const;

type Api = Pick<ReturnType<typeof billingApi>, "plan" | "planOrder">;

/** About a minute: 24 looks, 2.5 s apart. */
export const PLAN_WAIT_LOOKS = 24;
export const PLAN_WAIT_MS = 2500;

/**
 * Buy a plan online: Starter, Pro or Plus (one person, a 30-day pass paid once and renewed by hand; not a subscription) or the Pilot (one
 * seat is an individual pilot, two to ten seats a team workspace you own, 30 days, one payment). Built to the contract in
 * docs/pricing_page.md, Phase 2:
 *  - the request names the plan, and for a pilot the seats and, from two seats, the team name; it never carries an amount, the server works
 *    that out;
 *  - a payment is not a plan: when Checkout reports success we say we are waiting and look at GET /v1/billing/plan; only the server's signed
 *    webhook makes a plan active, so a "paid" message alone never shows one;
 *  - bounds (1 to 10 seats, a team name of 2 to 60 characters from two seats) are checked before any request;
 *  - the days come from the plan's data (`periodDays`), never a number written here.
 * `session` is undefined while it is being checked, null when signed out. Where this appears is decided by the page, behind
 * NEXT_PUBLIC_KELL_BUY_PLANS=on.
 */
export default function BuyPlan({ plan, api, session, returnTo = "/pricing", checkout = openCheckout, waitMs = PLAN_WAIT_MS, looks = PLAN_WAIT_LOOKS, now }: {
  plan: BuyablePlanId; api: Api; session: Session | null | undefined; returnTo?: string;
  checkout?: typeof openCheckout; waitMs?: number; looks?: number; now?: () => Date;
}) {
  const p = planById(plan);
  const pilot = plan === "pilot";
  const days = p.periodDays ?? 30;
  const clock = now ?? (() => new Date());
  const [state, setState] = useState<Loaded<PlanState> | null>(null);
  const [seats, setSeats] = useState(PILOT_MIN_SEATS);
  const [team, setTeam] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [orderId, setOrderId] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const live = useRef(true);

  useEffect(() => { live.current = true; return () => { live.current = false; if (timer.current) clearTimeout(timer.current); }; }, []);
  const load = useCallback(async () => { const s = await api.plan(); if (live.current) setState(s); return s; }, [api]);
  useEffect(() => { if (session) void load(); }, [session, load]);

  const current = state?.kind === "ok" ? state.data : null;
  const active = planActive(current, plan as PlanName, clock());
  const otherActive = !active && anyPlanActive(current, clock());                    // another plan runs now (the server names the higher one)
  const endedPilot = pilot && current?.plan === "pilot" && !active;                   // one pilot per account, even after it ends
  const endedPass = !pilot && current?.plan === plan && !active;
  const showForm = !endedPilot && !(pilot && active);                                 // one pilot per account; an active pilot has nothing left to buy
  const isTeam = pilot && seats >= TEAM_FROM_SEATS;
  const problem = pilot ? (seatsProblem(seats) ?? teamNameProblem(team, seats)) : null;
  const total = (p.priceInr ?? 0) * (pilot ? seats : 1);
  const start = clock();
  const end = addDays(start, days);
  const signInHref = `/sign-in?redirect=${returnTo}`;
  const buys = usageBuys(p);

  // After Checkout reports a payment, the plan appears when the provider's webhook reaches the server: look a number of times, then say so.
  function waitForPlan(beforeUntil: string | null, id: string, tries = 0) {
    timer.current = setTimeout(async () => {
      const s = await load();
      if (!live.current) return;
      if (s.kind === "ok" && planActive(s.data, plan as PlanName, clock()) && s.data.activeUntil !== beforeUntil) { setNote(`Payment confirmed. ${p.name} is active.`); setOrderId(""); return; }
      if (tries + 1 >= looks) { setNote("We haven't had the confirmation yet. Your plan starts as soon as it arrives."); setOrderId(id); return; }
      waitForPlan(beforeUntil, id, tries + 1);
    }, waitMs);
  }

  async function buy() {
    if (busy) return;
    if (problem) { setNote(problem); return; }
    setBusy(true); setNote(""); setOrderId("");
    const beforeUntil = current?.plan === plan ? current.activeUntil : null;
    const order = await api.planOrder(pilot ? { plan: "pilot", seats, ...(isTeam ? { teamName: team } : {}) } : { plan: plan as "starter" | "pro" | "plus" });
    if (order.kind === "off") { setState({ kind: "off" }); setBusy(false); return; }
    if (order.kind === "signed-out") { setState({ kind: "signed-out" }); setBusy(false); return; }
    if (order.kind === "error") { setNote(order.message); setBusy(false); return; }
    const result = await checkout(order.data, `keळ ${p.name}${pilot ? "" : `, ${days} days`}`);
    if (!live.current) return;
    setBusy(false);
    if (result === "paid") { setNote("Payment received. Waiting for it to be confirmed…"); waitForPlan(beforeUntil, order.data.orderId); }
    else if (result === "closed") setNote("The payment window was closed. You haven't been charged.");
    else setNote("The payment didn't go through. You haven't been charged; try again.");
  }

  const heading = pilot ? "Start a pilot" : `Buy ${p.name}`;
  const buyLabel = pilot ? "Start the pilot" : active ? "Renew" : `Buy ${p.name} (${days} days)`;
  const who = (c: PlanState) => `${c.seats} seat${c.seats === 1 ? "" : "s"}${c.teamName ? ` in ${c.teamName}` : ""}`;

  return (
    <div className="profile-card" id={`buy-${plan}`} aria-labelledby={`buy-${plan}-title`}>
      <h2 className="h3" id={`buy-${plan}-title`}>{heading}</h2>
      {pilot
        ? <p className="small dim">{p.price} {p.per}. One seat is an individual pilot; two to {PILOT_MAX_SEATS} seats make a team workspace you own. One payment covers everyone. Prices are in rupees, before GST.</p>
        : <p className="small dim">{p.price} {p.per}{p.includedUsd ? `, with $${p.includedUsd} of usage included` : ""}. Renew when it ends. This is a {days}-day pass paid once, not a subscription. Prices are in rupees, before GST.</p>}

      {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && <p className="small"><Link href={signInHref} style={link}>{pilot ? "Sign in to start the pilot" : `Sign in to buy ${p.name}`}</Link>. You will come back to this page.</p>}
      {session && state === null && <p className="small dim" role="status">Checking your plan…</p>}
      {session && state?.kind === "off" && (
        <div className="empty" role="status">
          <b>Buying plans isn&rsquo;t switched on yet.</b>
          <p className="small">Nothing is charged until it is. <Link href="/contact" style={link}>Ask us</Link> and we will set it up with you directly.</p>
        </div>
      )}
      {session && state?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href={signInHref} style={link}>Sign in again</Link>.</p>}
      {session && state?.kind === "error" && <p className="small" role="alert">{state.message} <button type="button" className="btn-line" onClick={() => void load()}>Try again</button></p>}

      {session && state?.kind === "ok" && (
        <>
          {active && current && (
            <p className="small" role="status">
              {pilot
                ? <><b>Pilot active until {plainDate(current.activeUntil!)}</b> for {who(current)}.</>
                : <><b>{p.name} active until {plainDate(current.activeUntil!)}.</b></>}
            </p>
          )}
          {active && pilot && (
            <p className="small">Next: connect your tool with the <Link href="/docs" style={link}>setup guide</Link>, and <Link href="/contact" style={link}>contact us</Link> about the joint report at the end.</p>
          )}
          {otherActive && current && (
            <p className="small dim" role="status">
              {planById(current.plan as BuyablePlanId).name} is active until {plainDate(current.activeUntil!)}. If you buy {p.name} too, both run, and the higher plan applies.
            </p>
          )}
          {endedPilot && current && (
            <p className="small" role="status">
              Your pilot ended{current.activeUntil ? <> on {plainDate(current.activeUntil)}</> : null}. A second pilot isn&rsquo;t sold online: <Link href="/contact" style={link}>talk to us</Link> about what comes next.
            </p>
          )}
          {endedPass && current?.activeUntil && <p className="small" role="status">Your {p.name} ended on {plainDate(current.activeUntil)}. Buy it again below.</p>}

          {showForm && (
            <form className="cform" onSubmit={(e) => { e.preventDefault(); void buy(); }} style={{ marginTop: 14 }}>
              {pilot && (
                <>
                  <div className="cfield">
                    <label htmlFor="pilot-seats">Seats</label>
                    <select id="pilot-seats" value={seats} onChange={(e) => { setSeats(Number(e.target.value)); setNote(""); }}>
                      {Array.from({ length: PILOT_MAX_SEATS - PILOT_MIN_SEATS + 1 }, (_, i) => PILOT_MIN_SEATS + i).map((n) => <option key={n} value={n}>{n}{n === 1 ? " (just you)" : ""}</option>)}
                    </select>
                  </div>
                  {isTeam && (
                    <div className="cfield">
                      <label htmlFor="pilot-team">Team name</label>
                      <input id="pilot-team" value={team} maxLength={TEAM_NAME_MAX} autoComplete="organization" placeholder="Acme pilot" onChange={(e) => { setTeam(e.target.value); setNote(""); }} aria-invalid={Boolean(teamNameProblem(team, seats)) && team.length > 0} />
                      <span className="hint">We create a team workspace with this name and you own it, so the other seats can join.</span>
                    </div>
                  )}
                </>
              )}
              <p className="small" aria-live="polite">
                <b>{inr(total)}</b>{pilot ? <> ({inr(p.priceInr ?? 0)} × {seats} seat{seats === 1 ? "" : "s"})</> : null}. {gstSentence(gstMode())}
              </p>
              <p className="small dim">
                {pilot ? "Starts today, " : "Runs from today, "}{plainDate(start)}, and ends {plainDate(end)} ({days} days).
              </p>
              {buys && <p className="small dim">{p.includedUsd ? `$${pilot ? p.includedUsd * seats : p.includedUsd} of usage included. ` : ""}{buys}</p>}
              {pilot && (
                <>
                  <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.includes.map((t) => <li key={t}>{t}</li>)}</ul>
                  {p.notIncluded && <ul className="small dim" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.notIncluded.map((t) => <li key={t}>Not included: {t}</li>)}</ul>}
                </>
              )}
              {/* Reserved for referral and promo codes, which are not built (docs/referral_legality_2026-11.md). Empty and hidden on purpose. */}
              <div data-slot="have-a-code" hidden />
              <p className="small dim">
                By paying you accept the <Link href="/docs/legal/terms" style={link}>Terms</Link> and the <Link href="/legal/refunds" style={link}>Refund policy</Link>.
              </p>
              <div className="cform-submit">
                <button className="btn-ink" type="submit" disabled={busy}>
                  <span>{busy ? "Opening payment…" : buyLabel}</span><span className="sq" aria-hidden="true">→</span>
                </button>
                {note && <span className="small" role="status">{note}</span>}
              </div>
              {orderId && (
                <p className="small dim">
                  If your bank has taken the money and no plan appears, <Link href="/contact" style={link}>contact us</Link> and quote order <code>{orderId}</code>.
                </p>
              )}
            </form>
          )}
          {!showForm && note && <p className="small" role="status">{note}</p>}
        </>
      )}
    </div>
  );
}
