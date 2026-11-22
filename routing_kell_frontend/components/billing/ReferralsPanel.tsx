"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { billingApi, Loaded, ReferralState } from "@/lib/billing";
import type { Session } from "@/lib/session";

const link = { textDecoration: "underline" } as const;

const STATUS_TEXT: Record<string, string> = {
  released: "In your wallet",
  pending: "Being confirmed",
  void: "Not counted",
  reversed: "Refunded by them, taken back",
};
const when = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
};
/** Rupees as people read them: "₹100", not "100.000000". */
const rupees = (v: string | number) => `₹${Number(v).toLocaleString("en-IN")}`;

/**
 * Your referrals (/referrals): the code to share, how many of your 30 paid referrals it has earned on, and what has been
 * paid into your own wallet.
 *
 * Built to docs/referral_legality_2026-11.md, which says the safe shape is one level, paid on a completed sale, credit
 * only. So this page states the reward and its conditions plainly before anyone shares:
 *  - credit, not cash: it is usage credit in your own kell wallet, not withdrawable and not transferable;
 *  - paid only after the person you invited pays, never for a signup, and an account counts once;
 *  - the allowance is the server's number (30 today), shown as "x of 30 used", never a countdown or a "hurry";
 *  - a share line that says you earn credit when someone uses it (the disclosure the endorsement rules ask for);
 *  - nothing here names the people who used the code: the server sends an opaque row id and a date, nothing else;
 *  - your own code never counts for you.
 */
export default function ReferralsPanel({ api, session, returnTo = "/referrals" }: {
  api: Pick<ReturnType<typeof billingApi>, "referrals">;
  session: Session | null | undefined; returnTo?: string;
}) {
  const [state, setState] = useState<Loaded<ReferralState> | null>(null);
  const [copied, setCopied] = useState<"code" | "message" | null>(null);
  useEffect(() => {
    if (!session) return;
    let live = true;
    api.referrals().then((r) => { if (live) setState(r); });
    return () => { live = false; };
  }, [session, api]);

  const data = state?.kind === "ok" ? state.data : null;
  const origin = typeof window !== "undefined" ? window.location.origin : "https://www.kell.org.in";
  const shareText = data?.code
    ? `I use kell for coding work. Use my code ${data.code} at ${origin}/pricing. I earn usage credit if you do.`
    : "";

  async function copy(what: "code" | "message", text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(what); setTimeout(() => setCopied(null), 1800); }
    catch { setCopied(null); }                                  // clipboard may be blocked: the text stays selectable
  }

  return (
    <div className="profile-card" aria-labelledby="referrals-title">
      <h2 className="h3" id="referrals-title">Your referral code</h2>
      {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
      {session === null && <p className="small"><Link href={`/sign-in?redirect=${returnTo}`} style={link}>Sign in to see your code</Link>.</p>}
      {session && state === null && <p className="small dim" role="status">Looking up your referrals…</p>}
      {session && state?.kind === "signed-out" && <p className="small" role="alert">Your session has ended. <Link href={`/sign-in?redirect=${returnTo}`} style={link}>Sign in again</Link>.</p>}
      {session && state?.kind === "error" && <p className="small" role="alert">{state.message}</p>}
      {session && state?.kind === "off" && <p className="small" role="status">Referrals aren&rsquo;t switched on yet. <Link href="/contact" style={link}>Contact us</Link> to hear when they are.</p>}

      {data && !data.enabled && (
        <p className="small" role="status" style={{ marginTop: 10 }}>
          Referral credit isn&rsquo;t running yet. Your code still unlocks the one-person plan for anyone you give it to.
        </p>
      )}

      {data && data.enabled && !data.code && (
        <p className="small" role="status" style={{ marginTop: 10 }}>
          Your code is made when your first plan is activated. <Link href="/pricing" style={link}>See the plans</Link>.
        </p>
      )}

      {data && data.code && (
        <div style={{ display: "grid", gap: 14, marginTop: 12 }}>
          <p className="small">
            Share this code. When someone uses it to buy a plan and pays, you get <b>{rupees(data.rewardInr)} of kell credit</b>{" "}
            in your own wallet, straight away. It is usage credit for kell, not cash.
          </p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <code style={{ fontSize: 20, letterSpacing: ".06em", padding: "8px 12px", border: "1px solid var(--rule-strong)", borderRadius: 6 }}>{data.code}</code>
            <button type="button" className="btn-line" onClick={() => void copy("code", data.code!)}><span>{copied === "code" ? "Copied" : "Copy code"}</span></button>
            <button type="button" className="btn-line" onClick={() => void copy("message", shareText)}><span>{copied === "message" ? "Copied" : "Copy a message to send"}</span></button>
          </div>

          <p className="small">
            <b>{data.used} of {data.limit}</b> paid referrals used. {data.remaining} left.
            {Number(data.pendingInr) > 0 ? <> {rupees(data.pendingInr)} is being confirmed.</> : null}
            {Number(data.earnedInr) > 0 ? <> <b>{rupees(data.earnedInr)} earned</b> so far.</> : null}
          </p>

          <details className="small">
            <summary style={{ cursor: "pointer" }}>How it works</summary>
            <ul style={{ margin: "8px 0 0", paddingLeft: 18, display: "grid", gap: 4 }}>
              <li>You earn only after the person you invited <b>pays</b>. A signup earns nothing.</li>
              <li>The credit is added to your own kell wallet as soon as their payment goes through.</li>
              <li>An account can be referred once, and your own code never counts for you.</li>
              <li>One account can earn on {data.limit} paid referrals.</li>
              <li>Credits go into your own kell wallet. They aren&rsquo;t cash, can&rsquo;t be withdrawn and can&rsquo;t be transferred.</li>
              <li>If their payment is refunded or reversed, that referral&rsquo;s credit is taken back.</li>
              <li>We may withhold credit for suspected abuse. If you share the code, say that you earn credit.</li>
            </ul>
          </details>

          {data.referrals.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="small" style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--rule-strong)" }}>
                    <th style={{ padding: "6px 8px 6px 0", fontWeight: 400 }}>When</th>
                    <th style={{ padding: "6px 8px 6px 0", fontWeight: 400 }}>Status</th>
                    <th style={{ padding: "6px 0", fontWeight: 400 }}>Credit</th>
                  </tr>
                </thead>
                <tbody>
                  {data.referrals.map((r) => (
                    <tr key={r.id} style={{ borderBottom: "1px solid var(--rule)" }}>
                      <td style={{ padding: "6px 8px 6px 0" }}>{when(r.releasedAt ?? r.at)}</td>
                      <td style={{ padding: "6px 8px 6px 0" }} title={r.reason ?? undefined}>{STATUS_TEXT[r.status] ?? r.status}</td>
                      <td style={{ padding: "6px 0" }}>{rupees(r.rewardInr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
