"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { anyPlanActive, plainDate, type Loaded, type PlanState, type SubscriptionState, type billingApi } from "@/lib/billing";
import { planById, type PlanId } from "@/lib/plans";
import type { Session } from "@/lib/session";

const link = { textDecoration: "underline" } as const;

/** The plan the signed-in account has: which one, until when, and where to buy or renew. Buying happens on /pricing.
 * A buying account also has a personal referral code (made when its plan was activated): it unlocks the one-person Pilot for
 * whoever uses it. No reward is paid for sharing it (docs/referral_legality_2026-11.md). */
export default function PlanSummary({ api, session, returnTo = "/account/billing", now }: {
  api: Pick<ReturnType<typeof billingApi>, "plan" | "myReferral" | "mySubscription" | "cancelSubscription">;
  session: Session | null | undefined; returnTo?: string; now?: () => Date;
}) {
  const [state, setState] = useState<Loaded<PlanState> | null>(null);
  const [referral, setReferral] = useState<string | null>(null);
  const [sub, setSub] = useState<Loaded<SubscriptionState> | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    if (!session) return;
    let live = true;
    api.plan().then((s) => { if (live) setState(s); });
    api.myReferral().then((r) => { if (live && r.kind === "ok") setReferral(r.data.code); });
    api.mySubscription().then((r) => { if (live) setSub(r); });
    return () => { live = false; };
  }, [session, api]);
  const subscription = sub?.kind === "ok" && sub.data.plan ? sub.data : null;

  async function cancel() {
    if (!window.confirm("Cancel auto-renew? The plan you have paid for keeps running to its end; no further charges are made.")) return;
    const r = await api.cancelSubscription();
    setMsg(r.kind === "ok" ? "Auto-renew cancelled. No further charges." : r.kind === "error" ? r.message : `Not cancelled (${r.kind}).`);
    if (r.kind === "ok") setSub(await api.mySubscription());
  }
  const current = state?.kind === "ok" ? state.data : null;
  const active = anyPlanActive(current, now ? now() : new Date());

  return (
    <div className="profile-card" aria-labelledby="your-plan-title">
      <h2 className="h3" id="your-plan-title">Your plan</h2>
      {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && <p className="small"><Link href={`/sign-in?redirect=${returnTo}`} style={link}>Sign in to see your plan</Link>.</p>}
      {session && state === null && <p className="small dim" role="status">Checking your plan…</p>}
      {session && state?.kind === "off" && <p className="small" role="status">Buying plans isn&rsquo;t switched on yet. <Link href="/pricing" style={link}>See the plans</Link> or <Link href="/contact" style={link}>ask us</Link>.</p>}
      {session && state?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href={`/sign-in?redirect=${returnTo}`} style={link}>Sign in again</Link>.</p>}
      {session && state?.kind === "error" && <p className="small" role="alert">{state.message}</p>}
      {session && current && active && (
        <p className="small" role="status">
          <b>{planById(current.plan!).name} is active until {plainDate(current.activeUntil!)}</b>. <Link href="/pricing" style={link}>Renew or change plan</Link>.
        </p>
      )}
      {session && current && !active && (
        <p className="small" role="status">
          {current.plan && current.activeUntil ? <>Your {planById(current.plan).name} ended on {plainDate(current.activeUntil)}. </> : <>You have no plan yet. </>}
          <Link href="/pricing" style={link}>See the plans</Link>.
        </p>
      )}
      {session && referral && (
        <p className="small" role="status" style={{ marginTop: 10 }}>
          Your code to share: <code>{referral}</code>. Someone who uses it unlocks the one-person {planById("pilot").name} plan on
          the pricing page. No reward is paid for sharing it.
        </p>
      )}
      {session && subscription && (
        <p className="small" role="status" style={{ marginTop: 10 }}>
          Auto-renew: <b>{planById(subscription.plan as PlanId).name}</b> every {subscription.periodDays} days
          {subscription.currentEnd ? <>, next charge about {plainDate(subscription.currentEnd)}</> : null}
          {subscription.status && subscription.status !== "active" ? ` (${subscription.status})` : ""}.{" "}
          <button type="button" className="btn-line" onClick={() => void cancel()}>Cancel auto-renew</button>
          {msg && <> {msg}</>}
        </p>
      )}
    </div>
  );
}
