"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@/lib/session";
import { anyPlanActive, openCheckout, type Loaded, type Pack, type PlanState, type Wallet, type billingApi } from "@/lib/billing";
import { PACK_INFO, inrPerCreditUsd } from "@/lib/plans";

const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const usd = (n: number) => `$${n.toFixed(2)}`;
const link = { textDecoration: "underline" } as const;

type Api = Pick<ReturnType<typeof billingApi>, "wallet" | "plan" | "packOrder">;

/** How long we look for the confirmation after Checkout says "paid": 24 looks, 2.5 s apart, is about a minute. */
export const WAIT_LOOKS = 24;
export const WAIT_MS = 2500;

/**
 * Additional kell credits: fixed packs a person adds on top of a plan, and the balance.
 *
 * Rules (docs/website_brief_2026-11.md, section 2):
 *  - the cards are drawn from the server's wallet answer (`packs`), never from a price worked out here; the order request names the pack and
 *    nothing else;
 *  - packs are for people with an ACTIVE plan: with `packs_need_plan` and no active plan the cards are disabled with "Available with an
 *    active plan" and a link to the plans (the server also refuses, 403);
 *  - a payment is not a credit: when Checkout reports success we say we are waiting, then look at the wallet; only the server's signed
 *    webhook adds credit, so a "paid" message alone never shows a different balance;
 *  - there is no field for an amount: free-form top-ups are not offered;
 *  - credit does not expire and is spendable only on kell (not transferable, not withdrawable). It is stated, not promised beyond that.
 * `buy` is the NEXT_PUBLIC_KELL_BUY_PLANS flag: with it off the packs are shown as information and nothing can be bought.
 * `session` is undefined while it is being checked, null when signed out.
 */
export default function CreditPacks({ api, session, buy = true, checkout = openCheckout, waitMs = WAIT_MS, looks = WAIT_LOOKS, history = false, returnTo = "/pricing", now }: {
  api: Api; session: Session | null | undefined; buy?: boolean;
  checkout?: typeof openCheckout; waitMs?: number; looks?: number;
  /** Also list the credits added so far (the account billing page shows them; /pricing does not). */
  history?: boolean; returnTo?: string; now?: () => Date;
}) {
  const [wallet, setWallet] = useState<Loaded<Wallet> | null>(null);
  const [plan, setPlan] = useState<Loaded<PlanState> | null>(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [orderId, setOrderId] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const live = useRef(true);

  useEffect(() => { live.current = true; return () => { live.current = false; if (timer.current) clearTimeout(timer.current); }; }, []);

  const load = useCallback(async () => { const w = await api.wallet(); if (live.current) setWallet(w); return w; }, [api]);
  useEffect(() => {
    if (!session || !buy) return;
    void load();
    void api.plan().then((p) => { if (live.current) setPlan(p); });
  }, [session, buy, load, api]);

  const ok = wallet?.kind === "ok" ? wallet.data : null;
  const planOk = plan?.kind === "ok" ? plan.data : null;
  const hasPlan = anyPlanActive(planOk, now ? now() : new Date());
  const locked = Boolean(ok?.packsNeedPlan) && !hasPlan;

  // After the window reports a payment, credit arrives when the provider's webhook reaches us: look a number of times, then say so.
  function waitForCredit(before: number, id: string, tries = 0) {
    timer.current = setTimeout(async () => {
      const w = await load();
      if (!live.current) return;
      const count = w.kind === "ok" ? w.data.credits.length : before;
      if (count > before) { setNote("Payment confirmed. Your credit has been added."); setOrderId(""); return; }
      if (tries + 1 >= looks) { setNote("We haven't had the confirmation yet. Credit is added as soon as it arrives."); setOrderId(id); return; }
      waitForCredit(before, id, tries + 1);
    }, waitMs);
  }

  async function purchase(p: Pack) {
    if (busy || locked) return;
    setBusy(p.id); setNote(""); setOrderId("");
    const before = ok?.credits.length ?? 0;
    const order = await api.packOrder(p.id);
    if (order.kind === "off") { setWallet({ kind: "off" }); setBusy(""); return; }
    if (order.kind === "signed-out") { setWallet({ kind: "signed-out" }); setBusy(""); return; }
    if (order.kind === "error") { setNote(order.message); setBusy(""); return; }
    const result = await checkout(order.data, `keळ credit ${usd(p.creditUsd)}`);
    if (!live.current) return;
    setBusy("");
    if (result === "paid") { setNote("Payment received. Waiting for it to be confirmed…"); waitForCredit(before, order.data.orderId); }
    else if (result === "closed") setNote("The payment window was closed. You haven't been charged.");
    else setNote("The payment didn't go through. You haven't been charged for credit; try again.");
  }

  // While billing is off, or purchases are switched off, there is no server list: the packs are shown as information.
  const info: Pack[] = PACK_INFO.map((p) => ({ id: p.id, priceInr: p.priceInr, creditUsd: p.creditUsd, inrPerUsd: Number(inrPerCreditUsd(p.priceInr, p.creditUsd)) }));
  const showInfoOnly = !buy || wallet?.kind === "off";
  const cards: Pack[] = showInfoOnly ? info : ok?.packs ?? [];

  return (
    <div className="profile-card" aria-labelledby="packs-title">
      <h2 className="h3" id="packs-title">Additional kell credits</h2>
      <p className="small">
        Add credit on top of a plan. It does not expire and it can be spent only on keळ: it can&rsquo;t be transferred or withdrawn. A bigger pack gives
        a little more credit per rupee. Credits are for people with an active plan. Prices are in rupees, before GST.
      </p>

      {session === undefined && buy && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && buy && <p className="small"><Link href={`/sign-in?redirect=${returnTo}`} style={link}>Sign in to add credit</Link>. You will come back to this page.</p>}
      {session && buy && wallet === null && <p className="small dim" role="status">Loading your balance…</p>}
      {session && buy && wallet?.kind === "off" && (
        <div className="empty" role="status">
          <b>Buying credits isn&rsquo;t switched on yet.</b>
          <p className="small">Nothing is charged and no balance changes until it is.</p>
        </div>
      )}
      {!buy && <p className="small dim" role="status">Buying credits isn&rsquo;t switched on yet. <Link href="/contact" style={link}>Ask us</Link> if you need credit sooner.</p>}
      {session && wallet?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href={`/sign-in?redirect=${returnTo}`} style={link}>Sign in again</Link>.</p>}
      {session && wallet?.kind === "error" && <p className="small" role="alert">{wallet.message} <button type="button" className="btn-line" onClick={() => void load()}>Try again</button></p>}

      {session && ok && (
        <p className="small">
          Balance: <b>{usd(ok.remainingUsd)}</b>
          <span className="dim"> · {ok.credits.length} credit{ok.credits.length === 1 ? "" : "s"} added so far</span>
        </p>
      )}
      {session && ok && history && (
        <div style={{ marginTop: 10 }}>
          <h3 className="h3" style={{ fontSize: 18 }}>Credits added so far</h3>
          {ok.credits.length === 0
            ? <p className="small dim">None yet.</p>
            : (
              <ul className="small" style={{ display: "grid", gap: 4, paddingLeft: 0, listStyle: "none" }} aria-label="Credits added so far">
                {ok.credits.map((c) => (
                  <li key={c.ref}><b>{inr(c.inr)}</b> <span className="dim">· {c.at ? new Date(c.at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) : "date unavailable"} · ref {c.ref}</span></li>
                ))}
              </ul>
            )}
        </div>
      )}

      {session && buy && locked && <p className="small" role="status" style={{ marginTop: 10 }}><b>Available with an active plan.</b> <Link href="/pricing#plans-title" style={link}>See the plans</Link>.</p>}

      {(showInfoOnly || session === null || ok) && cards.length > 0 && (
        <div className="pack-grid" style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", marginTop: 14 }} role="group" aria-label="Credit packs">
          {cards.map((p) => (
            <article key={p.id} className="card pack-card" aria-label={`${inr(p.priceInr)} pack`} data-pack={p.id}
              style={{ border: "1px solid transparent", boxShadow: "var(--sh-float)", background: "rgba(255,255,255,.72)", borderRadius: 10, padding: "16px 18px", display: "grid", gap: 6, alignContent: "start" }}>
              <p><b style={{ fontSize: 24, fontWeight: 400 }}>{inr(p.priceInr)}</b></p>
              <p className="small"><b>{usd(p.creditUsd)}</b> of credit</p>
              {p.inrPerUsd !== null && <p className="small dim">₹{p.inrPerUsd.toFixed(1)} per dollar of credit</p>}
              {buy && ok && !showInfoOnly && (
                <button type="button" className="btn-line" disabled={Boolean(busy) || locked} onClick={() => void purchase(p)} aria-label={`Add ${usd(p.creditUsd)} for ${inr(p.priceInr)}`}>
                  <span>{busy === p.id ? "Opening payment…" : locked ? "Available with an active plan" : "Add credit"}</span>
                </button>
              )}
            </article>
          ))}
        </div>
      )}

      {session && buy && ok && (
        <>
          <p className="small dim" style={{ marginTop: 12 }}>
            By paying you accept the <Link href="/docs/legal/terms" style={link}>Terms</Link> and the <Link href="/legal/refunds" style={link}>Refund policy</Link>.
          </p>
          {note && <p className="small" role="status">{note}</p>}
          {orderId && (
            <p className="small dim">
              If your bank has taken the money and no credit appears, <Link href="/contact" style={link}>contact us</Link> and quote order <code>{orderId}</code>.
            </p>
          )}
        </>
      )}
    </div>
  );
}
