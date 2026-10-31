"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { anyPlanActive, plainDate, type Loaded, type PlanState, type billingApi } from "@/lib/billing";
import { planById } from "@/lib/plans";
import type { Session } from "@/lib/session";

const link = { textDecoration: "underline" } as const;

/** The plan the signed-in account has: which one, until when, and where to buy or renew. Buying happens on /pricing. */
export default function PlanSummary({ api, session, returnTo = "/account/billing", now }: {
  api: Pick<ReturnType<typeof billingApi>, "plan">; session: Session | null | undefined; returnTo?: string; now?: () => Date;
}) {
  const [state, setState] = useState<Loaded<PlanState> | null>(null);
  useEffect(() => {
    if (!session) return;
    let live = true;
    api.plan().then((s) => { if (live) setState(s); });
    return () => { live = false; };
  }, [session, api]);
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
          <b>{planById(current.plan!).name} is active until {plainDate(current.activeUntil!)}</b>
          {current.plan === "pilot" ? <> for {current.seats} seat{current.seats === 1 ? "" : "s"}{current.teamName ? <> in {current.teamName}</> : null}</> : null}.
          {" "}<Link href="/pricing" style={link}>Renew or change plan</Link>.
        </p>
      )}
      {session && current && !active && (
        <p className="small" role="status">
          {current.plan && current.activeUntil ? <>Your {planById(current.plan).name} ended on {plainDate(current.activeUntil)}. </> : <>You have no plan yet. </>}
          <Link href="/pricing" style={link}>See the plans</Link>.
        </p>
      )}
    </div>
  );
}
