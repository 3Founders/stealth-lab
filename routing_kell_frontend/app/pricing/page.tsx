"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import BuyPlan from "@/components/billing/BuyPlan";
import CreditPacks from "@/components/billing/CreditPacks";
import { billingApi, buyPlansOn, gstMode, gstSentence } from "@/lib/billing";
import { LADDER, PILOT_VISIBLE, PLANS, isBuyable, perDay, plusVersusPro, usageBuys, type Plan } from "@/lib/plans";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

const api = billingApi({ token: getAccessToken });
const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const link = { textDecoration: "underline" } as const;

/** One plan card. Everything shown comes from lib/plans.ts: the price, the per-day figure, the usage and the estimate are not typed here. */
function PlanCard({ p, buyable }: { p: Plan; buyable: boolean }) {
  const day = perDay(p);
  const buys = usageBuys(p);
  return (
    <article className={`card plan-card${p.recommended ? " plan-plus" : ""}`} aria-label={p.name} data-recommended={p.recommended ? "true" : undefined}
      style={{ border: "1px solid transparent", boxShadow: "var(--sh-float)", background: "rgba(255,255,255,.72)", borderRadius: 10, padding: "18px 20px", display: "grid", gap: 10, alignContent: "start" }}>
      <h3 className="h3" style={{ fontSize: 22 }}>{p.name}{p.recommended && <span className="plan-rec">Recommended</span>}</h3>
      <p><b style={{ fontSize: 28, fontWeight: 400 }}>{p.price}</b> <span className="small dim">{p.per}</span></p>
      {day && <p className="small dim">{day}</p>}
      {p.includedUsd && <p className="small"><b>${p.includedUsd}</b> of usage included{p.id === "pilot" ? " per seat" : ""}</p>}
      {buys && <p className="small dim">{buys}</p>}
      <p className="small dim">{p.forWhom}</p>
      <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.includes.map((t) => <li key={t}>{t}</li>)}</ul>
      {buyable
        ? <a className="btn-line" href={`#buy-${p.id}`}><span>{p.id === "pilot" ? "Start a pilot" : `Buy ${p.name}`}</span></a>
        : <Link className="btn-line" href="/contact"><span>{p.cta}</span></Link>}
    </article>
  );
}

/**
 * Plans and prepaid credits (docs/pricing_page.md, docs/billing.md). Starter, Pro, Plus and the Pilot can be bought online only when NEXT_PUBLIC_KELL_BUY_PLANS=on (off by default); otherwise (and for Teams and Enterprise) each plan has a way to ask for it.
 * Additional credit packs are drawn from the server's wallet answer once billing is on, and are shown as information while it is off.
 */
export default function PricingPage() {
  const plusVsPro = plusVersusPro();
  const buy = buyPlansOn();                       // off until the founders turn plan purchases on
  const buyable = (id: string) => buy && isBuyable(id);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>PRICING</b></div>
        <h1 className="display"><AnimatedHeading>Plans and pricing</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="profile-wrap" style={{ gridColumn: "1 / span 12", display: "grid", gap: 28 }}>
          <div className="profile-card" aria-labelledby="plans-title">
            <h2 className="h3" id="plans-title">Plans</h2>
            <p className="small dim">Prices are in rupees, before GST. {buy ? "Starter, Pro, Plus and the Pilot are paid once, for their period, in the sections below. Teams and Enterprise are set up with you directly: ask and we will." : "Ask for a plan and we will set it up with you directly."}</p>
            <div className="plan-ladder" style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 20 }}>
              {LADDER.map((p) => <PlanCard key={p.id} p={p} buyable={buyable(p.id)} />)}
            </div>
            {plusVsPro && <p className="small" style={{ marginTop: 14 }}><b>{plusVsPro}</b> Included usage is in dollars of model usage; the estimate under each plan is how many delegated tasks that covers at the average cost we measured on a small sample.</p>}
            {buy && <p className="small dim" style={{ marginTop: 6 }}>If you buy a higher plan while a lower one is running, both run, and the higher plan applies.</p>}
            <h3 className="h3" style={{ fontSize: 20, marginTop: 28 }}>For teams</h3>
            <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 12 }}>
              {PLANS.filter((p) => ["pilot", "teams", "enterprise"].includes(p.id) && (p.id !== "pilot" || PILOT_VISIBLE)).map((p) => <PlanCard key={p.id} p={p} buyable={buyable(p.id)} />)}
            </div>
            <p className="small dim" style={{ marginTop: 14 }}>People with an active plan can add credit in fixed packs, below.</p>
          </div>

          {buy && LADDER.map((p) => <BuyPlan key={p.id} plan={p.id as "starter" | "pro" | "plus"} api={api} session={session} />)}
          {buy && PILOT_VISIBLE && <BuyPlan plan="pilot" api={api} session={session} />}

          <CreditPacks api={api} session={session} buy={buy} />

          <div className="profile-card">
            <h2 className="h3">What credits are</h2>
            <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 6 }}>
              <li>Additional credits are for people with an active plan. They pay for keळ&rsquo;s own service only, never expire, can&rsquo;t be transferred to anyone and can&rsquo;t be turned back into cash.</li>
              <li>Credits are added after the payment is confirmed to us by the payment provider, not when this page says you paid.</li>
              <li>Payments are in rupees, for India to start. {gstSentence(gstMode())}</li>
              <li>Cancellations and refunds follow the <Link href="/legal/refunds" style={link}>refund and cancellation terms</Link> (not published yet).</li>
            </ul>
          </div>

          <div className="profile-card">
            <h2 className="h3">Where your data goes</h2>
            <p className="small">
              Your prompt and the repository facts you send go to our server. The task then goes to the model provider that runs it. keळ does not yet
              have a SOC 2 report or an independent penetration test, so it is not a fit for data you couldn&rsquo;t send to a service in that position.
              See the <Link href="/docs/legal/privacy" style={{ textDecoration: "underline" }}>privacy policy</Link> and <Link href="/contact" style={{ textDecoration: "underline" }}>contact us</Link> with questions.
            </p>
          </div>

          <p className="small dim">
            <Link href="/docs/legal/terms" style={link}>Terms</Link> · <Link href="/docs/legal/privacy" style={link}>Privacy</Link> · <Link href="/legal/refunds" style={link}>Refunds and cancellation</Link> · <Link href="/contact" style={link}>Contact</Link>
          </p>
        </div>
      </section>
    </>
  );
}
