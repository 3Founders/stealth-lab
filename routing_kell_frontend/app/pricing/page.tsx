"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import { PLANS } from "@/lib/plans";
import {
  DEFAULT_MAX_INR, DEFAULT_MIN_INR, PRESET_AMOUNTS_INR, amountProblem, billingApi, gstMode, gstSentence, marginPct, openCheckout,
  remainingInr, workedExample, type Loaded, type Wallet,
} from "@/lib/billing";
import { getAccessToken, getSession, onSessionChange, type Session } from "@/lib/session";

const api = billingApi({ token: getAccessToken });
const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

/** Pricing and prepaid credits. The explanation is public; the balance and the pay button need a signed-in account. */
export default function PricingPage() {
  const pct = marginPct();
  const example = workedExample(10, pct);
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
            <p className="small dim" style={{ marginTop: 14 }}>Extra usage on any plan is bought in advance as credits, below.</p>
          </div>

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
              <li>Cancellations and refunds follow the <Link href="/legal/refunds" style={{ textDecoration: "underline" }}>refund and cancellation terms</Link> (draft).</li>
            </ul>
          </div>

          <TopUp />

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

function TopUp() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [wallet, setWallet] = useState<Loaded<Wallet> | null>(null);
  const [amount, setAmount] = useState("500");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); if (timer.current) clearTimeout(timer.current); };
  }, []);

  const load = useCallback(async () => { const w = await api.wallet(); setWallet(w); return w; }, []);
  useEffect(() => { if (session) void load(); }, [session, load]);

  const min = wallet?.kind === "ok" ? wallet.data.minInr : DEFAULT_MIN_INR;
  const max = wallet?.kind === "ok" ? wallet.data.maxInr : DEFAULT_MAX_INR;
  const problem = amountProblem(amount, min, max);

  // After the window reports a payment, credits arrive when the provider's webhook reaches us: look a few times, then say so.
  function waitForCredit(before: number, tries = 0) {
    timer.current = setTimeout(async () => {
      const w = await load();
      const now = w.kind === "ok" ? w.data.credits.length : before;
      if (now > before) { setNote("Payment confirmed. Your credit has been added."); return; }
      if (tries >= 8) { setNote("We haven't had the confirmation yet. Credit is added as soon as it arrives; check back shortly."); return; }
      waitForCredit(before, tries + 1);
    }, 2500);
  }

  async function pay() {
    if (problem || busy) return;
    setBusy(true); setNote("");
    const before = wallet?.kind === "ok" ? wallet.data.credits.length : 0;
    const order = await api.order(Number(amount));
    if (order.kind === "off") { setWallet({ kind: "off" }); setBusy(false); return; }
    if (order.kind !== "ok") { setNote(order.kind === "error" ? order.message : "Sign in again to buy credits."); setBusy(false); return; }
    const result = await openCheckout(order.data);
    setBusy(false);
    if (result === "paid") { setNote("Payment received. Waiting for it to be confirmed…"); waitForCredit(before); }
    else if (result === "failed") setNote("The payment didn't go through. You haven't been charged for credit; try again.");
  }

  return (
    <div className="profile-card">
      <h2 className="h3">Your credit</h2>
      {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && (
        <p className="small">
          <Link href="/sign-in?redirect=/pricing" style={{ textDecoration: "underline" }}>Sign in</Link> to see your balance and add credit.
        </p>
      )}
      {session && wallet === null && <p className="small dim" role="status">Loading your balance…</p>}
      {session && wallet?.kind === "off" && (
        <div className="empty" role="status">
          <b>Buying credits isn&rsquo;t switched on yet.</b>
          <p className="small">Paid top-ups open soon. Nothing is charged and no balance changes until then.</p>
        </div>
      )}
      {session && wallet?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href="/sign-in?redirect=/pricing" style={{ textDecoration: "underline" }}>Sign in again</Link>.</p>}
      {session && wallet?.kind === "error" && <p className="small" role="alert">{wallet.message} <button type="button" className="btn-line" onClick={() => void load()}>Try again</button></p>}
      {session && wallet?.kind === "ok" && (
        <>
          <p className="small">
            Balance: <b>{remainingInr(wallet.data) !== null ? inr(remainingInr(wallet.data)!) : "—"}</b>
            <span className="dim"> · {wallet.data.credits.length} top-up{wallet.data.credits.length === 1 ? "" : "s"} so far</span>
          </p>
          <form className="cform" onSubmit={(e) => { e.preventDefault(); void pay(); }} style={{ marginTop: 14 }}>
            <div className="cluster" role="group" aria-label="Top-up amount">
              {PRESET_AMOUNTS_INR.map((a) => (
                <button key={a} type="button" className={amount === String(a) ? "btn-ink" : "btn-line"} aria-pressed={amount === String(a)} onClick={() => setAmount(String(a))}>
                  <span>{inr(a)}</span>
                </button>
              ))}
            </div>
            <div className="cfield">
              <label htmlFor="b-amount">Amount in rupees</label>
              <input id="b-amount" inputMode="numeric" value={amount} onChange={(e) => { setAmount(e.target.value); setNote(""); }} aria-invalid={Boolean(problem)} />
              <span className="hint">{problem ?? `Between ${inr(min)} and ${inr(max)}.`}</span>
            </div>
            <div className="cform-submit">
              <button className="btn-ink" type="submit" disabled={Boolean(problem) || busy}>
                <span>{busy ? "Opening payment…" : `Pay ${problem ? "" : inr(Number(amount))}`.trim()}</span><span className="sq" aria-hidden="true">→</span>
              </button>
              {note && <span className="small" role="status">{note}</span>}
            </div>
          </form>
        </>
      )}
    </div>
  );
}
