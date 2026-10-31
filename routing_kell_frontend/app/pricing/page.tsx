"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import CreditPacks from "@/components/billing/CreditPacks";
import PlanBuy from "@/components/billing/PlanBuy";
import { billingApi, buyPlansOn, gstMode, gstSentence, type Loaded, type PlanState } from "@/lib/billing";
import { LADDER, PLANS, isBuyable, perDay, plusVersusPro, usageBuys, type BuyablePlanId, type Plan } from "@/lib/plans";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

const api = billingApi({ token: getAccessToken });
const link = { textDecoration: "underline" } as const;

/** One plan card. Everything shown comes from lib/plans.ts: the price, the per-day figure, the usage and the estimate are not typed here. */
function PlanCard({ p, buy, session, state, reload }: { p: Plan; buy: boolean; session: Session | null | undefined; state: Loaded<PlanState> | null; reload: () => Promise<Loaded<PlanState>> }) {
  const day = perDay(p);
  const buys = usageBuys(p);
  return (
    <article className={`card plan-card${p.recommended ? " plan-plus" : ""}`} aria-label={p.name} data-recommended={p.recommended ? "true" : undefined}
      style={{ border: "1px solid transparent", boxShadow: "var(--sh-float)", background: "rgba(255,255,255,.72)", borderRadius: 10, padding: "18px 20px", display: "grid", gap: 10, alignContent: "start" }}>
      <h3 className="h3" style={{ fontSize: 22 }}>{p.name}{p.recommended && <span className="plan-rec">Recommended</span>}</h3>
      <p><b style={{ fontSize: 28, fontWeight: 400 }}>{p.price}</b> <span className="small dim">{p.per}</span></p>
      {day && <p className="small dim">{day}</p>}
      {p.includedUsd && <p className="small"><b>${p.includedUsd}</b> of usage included</p>}
      {buys && <p className="small dim">{buys}</p>}
      <p className="small dim">{p.forWhom}</p>
      <ul className="small" style={{ paddingLeft: 18, display: "grid", gap: 4 }}>{p.includes.map((t) => <li key={t}>{t}</li>)}</ul>
      {buy && isBuyable(p.id)
        ? <PlanBuy plan={p.id as BuyablePlanId} api={api} session={session} state={state} reload={reload} />
        : <Link className="btn-line" href="/contact"><span>{p.cta}</span></Link>}
    </article>
  );
}

/**
 * Plans and additional credits (docs/pricing_page.md, docs/website_brief_2026-11.md). A person pays inside the plan's own card, only when
 * NEXT_PUBLIC_KELL_BUY_PLANS=on (off by default); otherwise (and for Teams and Enterprise) each plan has a way to ask for it. There is no
 * separate plan for trying it with a team: discounts will come from referral codes. Additional credit packs are drawn from the server's wallet
 * answer once billing is on, and shown as information while it is off.
 */
export default function PricingPage() {
  const plusVsPro = plusVersusPro();
  const buy = buyPlansOn();                       // off until the founders turn plan purchases on
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [state, setState] = useState<Loaded<PlanState> | null>(null);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);
  // One request for the account's plan serves every card; a card asks for it again after a payment.
  const reload = useCallback(async () => { const s = await api.plan(); setState(s); return s; }, []);
  useEffect(() => { if (buy && session) void reload(); }, [buy, session, reload]);

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
            <p className="small dim">
              Prices are in rupees, before GST. {gstSentence(gstMode())} {buy ? "Pick a plan and pay in its card. Teams and Enterprise are set up with you directly: ask and we will." : "Ask for a plan and we will set it up with you directly."}
            </p>
            <div className="plan-ladder" style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 20 }}>
              {LADDER.map((p) => <PlanCard key={p.id} p={p} buy={buy} session={session} state={state} reload={reload} />)}
            </div>
            {plusVsPro && <p className="small" style={{ marginTop: 14 }}><b>{plusVsPro}</b> Included usage is in dollars of model usage; the estimate under each plan is how many delegated tasks that covers at the average cost we measured on a small sample.</p>}
            {buy && <p className="small dim" style={{ marginTop: 6 }}>If you buy a higher plan while a lower one is running, both run, and the higher plan applies.</p>}
            <h3 className="h3" style={{ fontSize: 20, marginTop: 28 }}>For teams</h3>
            <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 12 }}>
              {PLANS.filter((p) => p.id === "teams" || p.id === "enterprise").map((p) => <PlanCard key={p.id} p={p} buy={false} session={session} state={state} reload={reload} />)}
            </div>
            <p className="small dim" style={{ marginTop: 14 }}>People with an active plan can add credit in fixed packs, below.</p>
          </div>

          <CreditPacks api={api} session={session} buy={buy} />

          <p className="small dim">
            <Link href="/docs/legal/terms" style={link}>Terms</Link> · <Link href="/docs/legal/privacy" style={link}>Privacy</Link> · <Link href="/legal/refunds" style={link}>Refunds and cancellation</Link> · <Link href="/contact" style={link}>Contact</Link>
          </p>
        </div>
      </section>
    </>
  );
}
