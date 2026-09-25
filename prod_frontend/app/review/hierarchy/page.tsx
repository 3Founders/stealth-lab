"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import StateNotice from "@/components/NotConnected";
import {
  closeHierarchyReviewItem, decideProposedRelation, getMyProfile, listHierarchyReviewItems, listProposedRelations,
  type HierarchyReviewItem, type ProposedRelation,
} from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";

/** Reviewer queue for the Goal hierarchy. Accepting only confirms a relationship
 * placement PROPOSED; the server re-checks everything (reviewer scope, visibility,
 * no cycles, no redundant edges), so this page can never create an edge on its own. */
export default function HierarchyReviewPage() {
  const [allowed, setAllowed] = useState<ApiState<boolean>>({ kind: "loading" });
  const [relations, setRelations] = useState<ApiState<ProposedRelation[]>>({ kind: "loading" });
  const [items, setItems] = useState<ApiState<HierarchyReviewItem[]>>({ kind: "loading" });
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = (signal?: AbortSignal) => {
    listProposedRelations(0, signal).then((r) => setRelations(r.kind === "ok" ? { kind: "ok", data: r.data.items } : (r as ApiState<ProposedRelation[]>)));
    listHierarchyReviewItems(signal).then((r) => setItems(r.kind === "ok" ? { kind: "ok", data: r.data.items } : (r as ApiState<HierarchyReviewItem[]>)));
  };

  useEffect(() => {
    const ac = new AbortController();
    getMyProfile(ac.signal).then((r) => {
      if (r.kind !== "ok") return setAllowed(r as ApiState<boolean>);
      setAllowed({ kind: "ok", data: Boolean(r.data.is_reviewer) });
      if (r.data.is_reviewer) load(ac.signal);
    });
    return () => ac.abort();
  }, []);

  const keyOf = (r: ProposedRelation) => `${r.specific_goal.id}:${r.abstract_goal.id}`;

  async function decide(r: ProposedRelation, decision: "accept" | "reject") {
    const key = keyOf(r);
    const reason = (reasons[key] || "").trim();
    if (!reason) {
      setMessage("Give a short reason for the decision.");
      return;
    }
    setBusy(key);
    setMessage(null);
    const res = await decideProposedRelation(r.specific_goal.id, r.abstract_goal.id, decision, reason);
    setBusy(null);
    setMessage(res.kind === "ok" ? `Relationship ${decision === "accept" ? "accepted" : "rejected"}.`
      : res.kind === "error" ? res.message : "Could not record the decision.");
    load();
  }

  async function close(item: HierarchyReviewItem, status: "resolved" | "dismissed") {
    setBusy(item.id);
    const res = await closeHierarchyReviewItem(item.id, status);
    setBusy(null);
    if (res.kind !== "ok") setMessage(res.kind === "error" ? res.message : "Could not update the item.");
    load();
  }

  if (allowed.kind !== "ok") {
    return <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}><StateNotice state={allowed} /></section>;
  }
  if (!allowed.data) {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <div className="empty"><b>Reviewers only.</b><p>This queue is for accounts with review rights.</p></div>
      </section>
    );
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>REVIEW</b><span>/ goal hierarchy</span></div>
        <h1 className="h1" style={{ gridColumn: "1 / span 10", fontSize: "clamp(26px, 3.2vw, 44px)" }}>Proposed relationships</h1>
        <p className="lead">
          “A is a more specific case of B.” Placement proposed these with low confidence. Accepting adds the edge to the
          hierarchy (a goal can have several parents); rejecting keeps it out. Neither changes whether any goal is resolved.
        </p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 100, rowGap: 24 }}>
        {message && <p className="small dim" style={{ gridColumn: "1 / span 12" }}>{message}</p>}
        {relations.kind !== "ok" || relations.data.length === 0 ? (
          <div style={{ gridColumn: "1 / span 12" }}>
            <StateNotice state={relations} empty={relations.kind === "ok" ? "Nothing to review. No proposed relationships are waiting." : undefined} />
          </div>
        ) : (
          relations.data.map((r) => {
            const key = keyOf(r);
            return (
              <div className="log" key={key} style={{ gridColumn: "1 / span 12", padding: "16px 18px" }}>
                <div><span>More specific</span><span><Link href={`/goals/${r.specific_goal.id}`}>{r.specific_goal.canonical_name}</Link></span></div>
                <div><span>More abstract</span><span><Link href={`/goals/${r.abstract_goal.id}`}>{r.abstract_goal.canonical_name}</Link></span></div>
                <div><span>Proposed by</span><span>{r.provenance ?? "placement"}{r.confidence != null ? ` · confidence ${r.confidence.toFixed(2)}` : ""}{r.judge.reason ? ` · ${r.judge.reason}` : ""}</span></div>
                <div>
                  <span>Decision</span>
                  <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <input type="text" placeholder="Reason (required)" aria-label="Reason" value={reasons[key] ?? ""}
                           onChange={(e) => setReasons({ ...reasons, [key]: e.target.value })} style={{ minWidth: 240 }} />
                    <button type="button" className="btn-ink" disabled={busy === key} onClick={() => decide(r, "accept")}><span>Accept</span></button>
                    <button type="button" className="btn-ink" style={{ background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}
                            disabled={busy === key} onClick={() => decide(r, "reject")}><span>Reject</span></button>
                  </span>
                </div>
              </div>
            );
          })
        )}

        <div style={{ gridColumn: "1 / span 12", marginTop: 24 }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Goals placement could not decide</h2>
          <p className="small dim">“Orphan”: no related goal found. “Uncertain”: the judgment was inconclusive. Standalone goals are fine; this is only a prompt to look.</p>
        </div>
        {items.kind !== "ok" || items.data.length === 0 ? (
          <div style={{ gridColumn: "1 / span 12" }}>
            <StateNotice state={items} empty={items.kind === "ok" ? "No flagged goals." : undefined} />
          </div>
        ) : (
          items.data.map((item) => (
            <div className="log" key={item.id} style={{ gridColumn: "1 / span 12", padding: "14px 18px", display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
              <div>
                <span className="status" data-s="unknown" style={{ marginRight: 10 }}>{item.reason}</span>
                <Link href={`/goals/${item.goal_id}`}>{item.canonical_name}</Link>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="btn-ink" disabled={busy === item.id} onClick={() => close(item, "resolved")}><span>Resolved</span></button>
                <button type="button" className="btn-ink" style={{ background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}
                        disabled={busy === item.id} onClick={() => close(item, "dismissed")}><span>Dismiss</span></button>
              </div>
            </div>
          ))
        )}
      </section>
    </>
  );
}
