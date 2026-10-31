"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import BuyPlan from "@/components/billing/BuyPlan";
import TopUp from "@/components/billing/TopUp";
import { billingApi, buyPlansOn } from "@/lib/billing";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

const api = billingApi({ token: getAccessToken });
const link = { textDecoration: "underline" } as const;

/**
 * Your billing: the plan you have (and when it ends), your prepaid credit and the top-ups so far. Everything here is the signed-in
 * person's own; a signed-out visitor is sent to sign in and back. Plans can be bought only once the founders turn on
 * NEXT_PUBLIC_KELL_BUY_PLANS, and credit only once the server has billing switched on; until then each part says so.
 */
export default function AccountBillingPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);
  const back = "/account/billing";
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ACCOUNT</b></div>
        <h1 className="display"><AnimatedHeading>Billing</AnimatedHeading></h1>
        <p className="lead">Your plan, your prepaid credit, and what you have paid. Prices are in rupees, before GST.</p>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="profile-wrap" style={{ gridColumn: "1 / span 12", display: "grid", gap: 28 }}>
          {buyPlansOn() ? (
            <BuyPlan plan="pro" api={api} session={session} returnTo={back} />
          ) : (
            <div className="profile-card">
              <h2 className="h3">Your plan</h2>
              <p className="small">Plans can&rsquo;t be bought online yet. <Link href="/pricing" style={link}>See the plans</Link> and <Link href="/contact" style={link}>ask for one</Link>; we will set it up with you directly.</p>
            </div>
          )}
          <TopUp api={api} session={session} history returnTo={back} />
          <p className="small dim">
            <Link href="/pricing" style={link}>Plans and pricing</Link> · <Link href="/docs/legal/terms" style={link}>Terms</Link> · <Link href="/docs/legal/privacy" style={link}>Privacy</Link> · <Link href="/legal/refunds" style={link}>Refunds and cancellation</Link> · <Link href="/contact" style={link}>Contact</Link>
          </p>
        </div>
      </section>
    </>
  );
}
