"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import StateNotice from "@/components/NotConnected";
import { getCreditsBalance, getCreditsHistory, getStanding, type CreditEvent, type StandingResult } from "@/lib/kel-api";
import { getSession } from "@/lib/session";
import type { ApiState } from "@/lib/api";

const REASON_LABEL: Record<string, string> = {
  new_procedure: "Accepted procedure",
  improvement: "Accepted improvement",
  verified_reuse: "Verified independent reuse",
  admin_adjustment: "Adjustment",
  clawback: "Reversal",
};

function formatAmount(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n}`;
}

export default function CreditsPage() {
  // Read once on mount — this page is only ever useful client-side (it's
  // entirely about the signed-in visitor's own private data), so there's
  // no SSR/hydration state to reconcile the way Header's nav label has to.
  const [session] = useState(() => getSession());
  const [balance, setBalance] = useState<ApiState<{ balance: number }>>({ kind: "loading" });
  const [history, setHistory] = useState<ApiState<CreditEvent[]>>({ kind: "loading" });
  const [standing, setStanding] = useState<ApiState<StandingResult>>({ kind: "loading" });

  useEffect(() => {
    if (!session) {
      setBalance({ kind: "unauthenticated" });
      setHistory({ kind: "unauthenticated" });
      setStanding({ kind: "unauthenticated" });
      return;
    }
    const ac = new AbortController();
    getCreditsBalance(session.subject, ac.signal).then(setBalance);
    getCreditsHistory(session.subject, ac.signal).then((r) => setHistory(r.kind === "ok" ? { kind: "ok", data: r.data.events } : (r as ApiState<CreditEvent[]>)));
    getStanding(session.subject, ac.signal).then(setStanding);
    return () => ac.abort();
  }, [session]);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>YOUR CREDITS</b></div>
        <h1 className="display">What you&rsquo;ve earned.</h1>
        <p className="lead">Private to your account — never a public leaderboard.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        {balance.kind === "ok" ? (
          <div className="cells" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
            <div className="cell">
              <div className="n"><span>Balance</span></div>
              <div><h3 className="h3">{balance.data.balance} Credits</h3><p>An internal utility number, not cash — see <Link href="/docs#contribution-rewards" style={{ textDecoration: "underline" }}>how contribution and rewards work</Link>.</p></div>
            </div>
            <div className="cell">
              <div className="n"><span>Standing</span></div>
              {standing.kind === "ok" ? (
                <div>
                  <h3 className="h3">{standing.data.standing_score}</h3>
                  <p>Trust from historical contribution quality — separate from Credits. More Credits doesn&rsquo;t mean more trusted.</p>
                </div>
              ) : (
                <div><p className="small dim">Not available.</p></div>
              )}
            </div>
          </div>
        ) : (
          <StateNotice state={balance} empty={undefined} />
        )}

        {standing.kind === "ok" && (
          <div style={{ gridColumn: "1 / span 12" }}>
            <div className="caption dim" style={{ margin: "8px 0" }}>Standing is built from</div>
            <ul className="list" style={{ marginTop: 0 }}>
              <li><span className="n">01</span><div><h3 style={{ fontSize: 18 }}>Accepted new procedures</h3></div><span>{standing.data.accepted_new_procedures}</span></li>
              <li><span className="n">02</span><div><h3 style={{ fontSize: 18 }}>Accepted improvements</h3></div><span>{standing.data.accepted_improvements}</span></li>
              <li><span className="n">03</span><div><h3 style={{ fontSize: 18 }}>Accepted benchmarks</h3></div><span>{standing.data.accepted_benchmarks}</span></li>
              <li><span className="n">04</span><div><h3 style={{ fontSize: 18 }}>Verified independent reuse of your work</h3></div><span>{standing.data.verified_independent_outcomes}</span></li>
              <li><span className="n">05</span><div><h3 style={{ fontSize: 18 }}>Reliable evidence you&rsquo;ve recorded</h3></div><span>{standing.data.reliable_evidence_count}</span></li>
            </ul>
          </div>
        )}

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Recent earnings</h2>
          <p className="small dim">Every entry traces to a real, server-recorded event — never a client-side total.</p>
        </div>
        {history.kind === "ok" && history.data.length > 0 ? (
          <ul className="list" style={{ marginTop: 0 }}>
            {history.data.map((e) => {
              const isReversal = e.reason === "clawback";
              return (
                <li key={e.id}>
                  <span className="n" style={{ fontSize: 18, color: isReversal ? "var(--grey)" : "var(--ink)" }}>{formatAmount(e.amount)}</span>
                  <div>
                    <h3 style={{ fontSize: 18 }}>{isReversal ? "Reward reversed" : REASON_LABEL[e.reason] ?? e.reason}</h3>
                    <p className="desc">{new Date(e.created_at).toLocaleString()}</p>
                  </div>
                  <span className="status" data-s={isReversal ? "unknown" : "verified"}>{isReversal ? "reversal" : "credited"}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <StateNotice state={history} empty={history.kind === "ok" ? "No Credits yet — they come from an accepted contribution or a verified, independent reuse of something you made." : undefined} />
        )}
      </section>
    </>
  );
}
