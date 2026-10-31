"use client";
import Link from "next/link";
import type { Session } from "@/lib/session";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEFAULT_MAX_INR, DEFAULT_MIN_INR, PRESET_AMOUNTS_INR, amountProblem, openCheckout, remainingInr, type Loaded, type Wallet, type billingApi,
} from "@/lib/billing";

const inr = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const link = { textDecoration: "underline" } as const;

type Api = Pick<ReturnType<typeof billingApi>, "wallet" | "order">;

/** How long we look for the confirmation after Checkout says "paid": 24 looks, 2.5 s apart, is about a minute. */
export const WAIT_LOOKS = 24;
export const WAIT_MS = 2500;

/**
 * Prepaid credits: the balance, preset and custom amounts, and a Pay button that opens Razorpay Checkout.
 *
 * Two rules from docs/pricing_page.md hold everywhere here. Nothing the browser sends sets a price (the order request carries the
 * amount to top up and nothing else). And a payment is not a credit: when Checkout reports success we say we are waiting, then look for
 * the balance to change; only the server's signed webhook changes it, so a "paid" message alone never shows a different balance.
 * `session` is undefined while it is being checked, null when signed out.
 */
export default function TopUp({ api, session, checkout = openCheckout, waitMs = WAIT_MS, looks = WAIT_LOOKS }: {
  api: Api; session: Session | null | undefined;
  checkout?: typeof openCheckout; waitMs?: number; looks?: number;
}) {
  const [wallet, setWallet] = useState<Loaded<Wallet> | null>(null);
  const [amount, setAmount] = useState("500");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [orderId, setOrderId] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const live = useRef(true);

  useEffect(() => { live.current = true; return () => { live.current = false; if (timer.current) clearTimeout(timer.current); }; }, []);

  const load = useCallback(async () => { const w = await api.wallet(); if (live.current) setWallet(w); return w; }, [api]);
  useEffect(() => { if (session) void load(); }, [session, load]);

  const min = wallet?.kind === "ok" ? wallet.data.minInr : DEFAULT_MIN_INR;
  const max = wallet?.kind === "ok" ? wallet.data.maxInr : DEFAULT_MAX_INR;
  const problem = amountProblem(amount, min, max);

  // After the window reports a payment, credit arrives when the provider's webhook reaches us: look a number of times, then say so.
  function waitForCredit(before: number, id: string, tries = 0) {
    timer.current = setTimeout(async () => {
      const w = await load();
      if (!live.current) return;
      const now = w.kind === "ok" ? w.data.credits.length : before;
      if (now > before) { setNote("Payment confirmed. Your credit has been added."); setOrderId(""); return; }
      if (tries + 1 >= looks) { setNote("We haven't had the confirmation yet. Credit is added as soon as it arrives."); setOrderId(id); return; }
      waitForCredit(before, id, tries + 1);
    }, waitMs);
  }

  async function pay() {
    if (problem || busy) return;
    setBusy(true); setNote(""); setOrderId("");
    const before = wallet?.kind === "ok" ? wallet.data.credits.length : 0;
    const order = await api.order(Number(amount));
    if (order.kind === "off") { setWallet({ kind: "off" }); setBusy(false); return; }
    if (order.kind === "signed-out") { setWallet({ kind: "signed-out" }); setBusy(false); return; }
    if (order.kind === "error") { setNote(order.message); setBusy(false); return; }
    const result = await checkout(order.data);
    if (!live.current) return;
    setBusy(false);
    if (result === "paid") { setNote("Payment received. Waiting for it to be confirmed…"); waitForCredit(before, order.data.orderId); }
    else if (result === "closed") setNote("The payment window was closed. You haven't been charged.");
    else setNote("The payment didn't go through. You haven't been charged for credit; try again.");
  }

  return (
    <div className="profile-card" aria-labelledby="topup-title">
      <h2 className="h3" id="topup-title">Your credit</h2>
      {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && (
        <p className="small">
          <Link href="/sign-in?redirect=/pricing" style={link}>Sign in to add credit</Link>. You will come back to this page.
        </p>
      )}
      {session && wallet === null && <p className="small dim" role="status">Loading your balance…</p>}
      {session && wallet?.kind === "off" && (
        <div className="empty" role="status">
          <b>Buying credits isn&rsquo;t switched on yet.</b>
          <p className="small">Paid top-ups open soon. Nothing is charged and no balance changes until then.</p>
        </div>
      )}
      {session && wallet?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href="/sign-in?redirect=/pricing" style={link}>Sign in again</Link>.</p>}
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
              <input id="b-amount" inputMode="numeric" value={amount} onChange={(e) => { setAmount(e.target.value); setNote(""); setOrderId(""); }} aria-invalid={Boolean(problem)} />
              <span className="hint">{problem ?? `Between ${inr(min)} and ${inr(max)}.`}</span>
            </div>
            <p className="small dim">
              By paying you accept the <Link href="/docs/legal/terms" style={link}>Terms</Link> and the <Link href="/legal/refunds" style={link}>Refund policy</Link>.
            </p>
            <div className="cform-submit">
              <button className="btn-ink" type="submit" disabled={Boolean(problem) || busy}>
                <span>{busy ? "Opening payment…" : `Pay ${problem ? "" : inr(Number(amount))}`.trim()}</span><span className="sq" aria-hidden="true">→</span>
              </button>
              {note && <span className="small" role="status">{note}</span>}
            </div>
            {orderId && (
              <p className="small dim">
                If your bank has taken the money and no credit appears, <Link href="/contact" style={link}>contact us</Link> and quote order <code>{orderId}</code>.
              </p>
            )}
          </form>
        </>
      )}
    </div>
  );
}
