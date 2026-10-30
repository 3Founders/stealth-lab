"use client";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import { PLANS } from "@/lib/plans";

/** Plans and pricing. Nothing here can be bought online yet (docs/billing.md, "Plans"); each plan has a way to ask for it. */
export default function PricingPage() {
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
            <p className="small dim">Prices are in rupees, before GST. No plan can be bought online yet: monthly and per-seat billing is still being built. Ask for one and we will set it up with you directly.</p>
            <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 16 }}>
              {PLANS.map((p) => (
                <article key={p.id} className="card" aria-label={p.name} style={{ border: "1px solid var(--rule-strong)", borderRadius: 6, padding: "18px 20px", display: "grid", gap: 10, alignContent: "start" }}>
                  <h3 className="h3" style={{ fontSize: 22 }}>{p.name}</h3>
                  <p><b style={{ fontSize: 28, fontWeight: 400 }}>{p.price}</b> <span className="small dim">{p.per}</span></p>
                  <p className="small dim">{p.forWhom}</p>
                  <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.includes.map((t) => <li key={t}>{t}</li>)}</ul>
                  {p.notIncluded && <ul className="small dim" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.notIncluded.map((t) => <li key={t}>{t}</li>)}</ul>}
                  <p className="caption" role="status">{p.status}</p>
                  <Link className="btn-line" href="/contact"><span>{p.cta}</span></Link>
                </article>
              ))}
            </div>
          </div>

          <div className="profile-card">
            <h2 className="h3">Where your data goes</h2>
            <p className="small">
              Your prompt and the repository facts you send go to our server. The task then goes to the model provider that runs it. keळ does not yet
              have a SOC 2 report or an independent penetration test, so it is not a fit for data you couldn&rsquo;t send to a service in that position.
              See the <Link href="/docs/legal/privacy" style={{ textDecoration: "underline" }}>privacy policy</Link> and <Link href="/contact" style={{ textDecoration: "underline" }}>contact us</Link> with questions.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
