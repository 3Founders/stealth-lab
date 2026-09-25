"use client";
import { useEffect, useState } from "react";
import StateNotice from "@/components/NotConnected";
import {
  commitToGoal, getGoalCommitments, withdrawCommitment,
  type GoalCommitmentsResult, type GoalDemandTotals,
} from "@/lib/kel-api";
import { getSession } from "@/lib/session";
import type { ApiState } from "@/lib/api";

/** Community demand for one Goal: Credits people have committed as a bounty.
 * Committed Credits are locked; they go to the contributor of the Procedure that
 * resolves this Goal, and can be withdrawn until then. Demand is a signal of what
 * people want solved -- it never makes a Goal resolved or a Procedure correct. */
export default function GoalDemandPanel({ goalId, resolved }: { goalId: string; resolved: boolean }) {
  const [data, setData] = useState<ApiState<GoalCommitmentsResult>>({ kind: "loading" });
  const [signedIn, setSignedIn] = useState(false);
  const [credits, setCredits] = useState("10");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = (signal?: AbortSignal) => getGoalCommitments(goalId, signal).then(setData);

  useEffect(() => {
    const ac = new AbortController();
    load(ac.signal);
    getSession().then((s) => setSignedIn(Boolean(s)));
    return () => ac.abort();
  }, [goalId]);

  async function commit() {
    const amount = Number.parseInt(credits, 10);
    if (!Number.isFinite(amount) || amount < 1) {
      setMessage("Commit at least 1 Credit.");
      return;
    }
    setBusy(true);
    setMessage(null);
    const key = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const r = await commitToGoal(goalId, amount, key);
    setBusy(false);
    if (r.kind === "ok") {
      setMessage(`${amount} Credits committed. They are paid to whoever resolves this goal, or returned if you withdraw first.`);
      load();
    } else {
      setMessage(r.kind === "error" ? r.message : "Sign in to commit Credits.");
    }
  }

  async function withdraw(commitmentId: string) {
    setBusy(true);
    setMessage(null);
    const r = await withdrawCommitment(goalId, commitmentId);
    setBusy(false);
    if (r.kind === "ok") {
      setMessage(`${r.data.released} Credits returned to you.`);
      load();
    } else {
      setMessage(r.kind === "error" ? r.message : "Could not withdraw. Try again.");
    }
  }

  const line = (t: GoalDemandTotals) =>
    t.supporters === 0 ? "No open commitments" : `${t.committed_credits} Credits from ${t.supporters} supporter${t.supporters === 1 ? "" : "s"}`;

  return (
    <>
      <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
        <h2 className="h3" style={{ marginBottom: 4 }}>Demand</h2>
        <p className="small dim">
          Credits people have committed to see this goal solved. A community signal, not evidence: it never marks a goal
          resolved or a way correct.
        </p>
      </div>
      {data.kind !== "ok" ? (
        <div style={{ gridColumn: "1 / span 12" }}><StateNotice state={data} empty={undefined} /></div>
      ) : (
        <div className="log" style={{ gridColumn: "1 / span 12", maxWidth: "48em" }}>
          <div><span>On this goal</span><span>{line(data.data.direct)}</span></div>
          <div><span>Including more specific goals</span><span>{line(data.data.aggregated)}</span></div>
          {data.data.mine.map((c) => (
            <div key={c.id}>
              <span>Your commitment</span>
              <span>
                {c.credits} Credits ·{" "}
                {c.settlement === null ? "open" : c.settlement === "bounty_payout" ? "paid to the solver" : "returned to you"}
                {c.settlement === null && !data.data.resolved && (
                  <button type="button" className="btn-ink" style={{ marginLeft: 12, background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}
                          disabled={busy} onClick={() => withdraw(c.id)}>
                    <span>Withdraw</span>
                  </button>
                )}
              </span>
            </div>
          ))}
          {resolved || data.data.resolved ? (
            <div><span>Commit</span><span>This goal is resolved; open commitments were settled.</span></div>
          ) : signedIn ? (
            <div>
              <span>Commit Credits</span>
              <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input type="number" min={1} step={1} value={credits} aria-label="Credits to commit"
                       onChange={(e) => setCredits(e.target.value)} style={{ width: 96 }} />
                <button type="button" className="btn-ink" disabled={busy} onClick={commit}>
                  <span>{busy ? "…" : "Commit"}</span>
                </button>
              </span>
            </div>
          ) : (
            <div><span>Commit Credits</span><span>Sign in to commit Credits to this goal.</span></div>
          )}
          {message && <div><span /><span className="small dim">{message}</span></div>}
        </div>
      )}
    </>
  );
}
