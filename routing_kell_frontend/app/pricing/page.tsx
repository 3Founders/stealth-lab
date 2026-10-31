"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import BuyPlan from "@/components/billing/BuyPlan";
import TopUp from "@/components/billing/TopUp";
import { DEFAULT_MAX_INR, DEFAULT_MIN_INR, billingApi, buyPlansOn, gstMode, gstSentence, marginPct, workedExample } from "@/lib/billing";
import { PLANS } from "@/lib/plans";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

const api = billingApi({ token: getAccessToken });
const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const link = { textDecoration: "underline" } as const;

/**
 * Plans and prepaid credits (docs/pricing_page.md, docs/billing.md). Pilot and Pro can be bought online only when NEXT_PUBLIC_KELL_BUY_PLANS=on; otherwise (and for Teams and Enterprise) each plan has a way to ask for it.
 * Credits can be bought by signed-in people once the server has billing switched on, and the page says so plainly while it is off.
 */
export default function PricingPage() {
  const pct = marginPct();
  const example = workedExample(10, pct);
  const buy = buyPlansOn();                       // off until the founders turn plan purchases on
  const buyable = (id: string) => buy && (id === "pilot" || id === "pro");
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
            <p className="small dim">Prices are in rupees, before GST. {buy ? "The Pilot and Pro are paid once, for their period, in the sections below. Teams and Enterprise are set up with you directly: ask and we will." : "Ask for a plan and we will set it up with you directly."}</p>
            <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 16 }}>
              {PLANS.map((p) => (
                <article key={p.id} className="card" aria-label={p.name} style={{ border: "1px solid transparent", boxShadow: "var(--sh-float)", background: "rgba(255,255,255,.72)", borderRadius: 10, padding: "18px 20px", display: "grid", gap: 10, alignContent: "start" }}>
                  <h3 className="h3" style={{ fontSize: 22 }}>{p.name}</h3>
                  <p><b style={{ fontSize: 28, fontWeight: 400 }}>{p.price}</b> <span className="small dim">{p.per}</span></p>
                  <p className="small dim">{p.forWhom}</p>
                  <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.includes.map((t) => <li key={t}>{t}</li>)}</ul>
                  {buyable(p.id)
                    ? <a className="btn-line" href={`#buy-${p.id}`}><span>{p.id === "pilot" ? "Start a pilot" : "Buy Pro"}</span></a>
                    : <Link className="btn-line" href="/contact"><span>{p.cta}</span></Link>}
                </article>
              ))}
            </div>
            <p className="small dim" style={{ marginTop: 14 }}>Extra usage on any plan is bought in advance as credits, below.</p>
          </div>

          {buy && <BuyPlan plan="pilot" api={api} session={session} />}
          {buy && <BuyPlan plan="pro" api={api} session={session} />}

          <div className="profile-card">
            <h2 className="h3">Extra usage: prepaid credits</h2>
            <p className="small">
              You buy prepaid credits in rupees. When a model runs for you through keळ, the credit it uses is the provider&rsquo;s own charge for that run
              {pct === null ? " plus a margin" : <> plus {pct}%</>}. There is no subscription and no minimum spend beyond the first top-up.
            </p>
            <p className="small" style={{ marginTop: 10 }}>
              <b>Illustration, not a quote:</b>{" "}
              {example
                ? <>if a provider charged ₹{example.provider} for a run and the margin were {pct}%, the run would use {inr(example.total)} of credit ({inr(example.provider)} + {inr(example.margin)}).</>
                : <>if a provider charged ₹10 for a run, the run would use ₹10 plus the margin. The margin is set before launch and will be shown here as a number.</>}
              {" "}Real runs use the provider&rsquo;s real charge, which differs by model and by length.
            </p>
            <p className="small dim" style={{ marginTop: 10 }}>{gstSentence(gstMode())}</p>
          </div>

          <div className="profile-card">
            <h2 className="h3">What credits are</h2>
            <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 6 }}>
              <li>Credits pay for keळ&rsquo;s own service only. They can&rsquo;t be transferred to anyone and can&rsquo;t be turned back into cash.</li>
              <li>Top-ups run from {inr(DEFAULT_MIN_INR)} to {inr(DEFAULT_MAX_INR)}. Payments are in rupees, for India to start.</li>
              <li>The managed tier runs open models. If you use your own provider key, that provider bills you directly and no credit is used.</li>
              <li>Credits are added after the payment is confirmed to us by the payment provider, not when this page says you paid.</li>
              <li>Cancellations and refunds follow the <Link href="/legal/refunds" style={link}>refund and cancellation terms</Link> (not published yet).</li>
            </ul>
          </div>

          <TopUp api={api} session={session} />

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
