"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import GoalDemandPanel from "@/components/GoalDemandPanel";
import {
  getGoalContributors, getMyProfile, getGoal, getGoalSolutions, getProcedure, getRankedProcedures,
  humanize, listBenchmarkSubmissions, listProcedureSubmissions, rankedProcedureRowId, reviewBenchmarkSubmission, reviewProcedureSubmission,
  type GoalContributor, type Goal, type ProcedureDetail, type RankedProcedure, type SubmissionResult,
} from "@/lib/kel-api";
import { goalResolutionLabel, rankingExplanation, rankingSignalSummary } from "@/lib/goal-display";
import { getSession } from "@/lib/session";
import { categoryOf } from "@/lib/mock-adapter";
import type { ApiState } from "@/lib/api";

const bucketToStatus: Record<string, string> = {
  verified: "verified", candidate: "candidate", needs_evidence: "unknown", verified_failure: "failed",
};

export default function GoalPage() {
  const { id } = useParams<{ id: string }>();
  const [goal, setGoal] = useState<ApiState<Goal>>({ kind: "loading" });
  const [procedures, setProcedures] = useState<ApiState<ProcedureDetail[]>>({ kind: "loading" });
  // The ordered, bucketed list of Ways is computed by the backend ONLY
  // (app/economy/ranking.py) — this page never re-derives rank/bucket
  // client-side. `procedures` above is fetched separately, only for
  // claims aggregation below (not in the ranked response).
  const [ranked, setRanked] = useState<ApiState<RankedProcedure[]>>({ kind: "loading" });
  // Canonical Goal-level contributor view (app/api/economy.py's
  // /goals/{id}/contributors) — computed server-side from accepted
  // submissions, never re-tallied here from fetched procedures.
  const [contributors, setContributors] = useState<ApiState<GoalContributor[]>>({ kind: "loading" });

  // Reviewer-only (Band 2.9's KNOWLEDGE_PUBLISH). Display convenience --
  // the review endpoints re-check this server-side regardless, so a stale
  // or forged `true` here can never actually accept/reject anything.
  const [isReviewer, setIsReviewer] = useState(false);
  const [pendingWays, setPendingWays] = useState<ApiState<SubmissionResult[]>>({ kind: "loading" });
  const [pendingBenchmarks, setPendingBenchmarks] = useState<ApiState<SubmissionResult[]>>({ kind: "loading" });
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const isPending = (s: SubmissionResult) => s.status === "candidate" || s.status === "needs_review";

  function loadPendingSubmissions(signal?: AbortSignal) {
    // No server-side OR-of-statuses filter -- fetch unfiltered and keep
    // only candidate/needs_review client-side (both count as "pending").
    listProcedureSubmissions(id, undefined, signal).then((r) =>
      setPendingWays(r.kind === "ok" ? { kind: "ok", data: r.data.submissions.filter(isPending) } : (r as ApiState<SubmissionResult[]>)));
    listBenchmarkSubmissions(id, undefined, signal).then((r) =>
      setPendingBenchmarks(r.kind === "ok" ? { kind: "ok", data: r.data.submissions.filter(isPending) } : (r as ApiState<SubmissionResult[]>)));
  }

  useEffect(() => {
    const ac = new AbortController();
    getSession().then((s) => {
      if (!s) return;
      getMyProfile(ac.signal).then((r) => {
        if (r.kind === "ok" && r.data.is_reviewer) {
          setIsReviewer(true);
          loadPendingSubmissions(ac.signal);
        }
      });
    });
    return () => ac.abort();
  }, [id]);

  async function review(kind: "way" | "benchmark", submissionId: string, decision: "accepted" | "rejected") {
    setReviewBusy(submissionId);
    setReviewError(null);
    const r = kind === "way"
      ? await reviewProcedureSubmission(submissionId, decision)
      : await reviewBenchmarkSubmission(submissionId, decision);
    setReviewBusy(null);
    if (r.kind !== "ok") {
      setReviewError(r.kind === "error" ? r.message : "Could not record that review. Try again.");
      return;
    }
    loadPendingSubmissions();
    // Accepting can change the ranked ways / benchmarks lists below.
    getRankedProcedures(id).then((rr) => setRanked(rr.kind === "ok" ? { kind: "ok", data: rr.data.ranked } : (rr as ApiState<RankedProcedure[]>)));
    getGoal(id).then(setGoal);
  }

  useEffect(() => {
    const ac = new AbortController();
    getGoal(id, ac.signal).then(setGoal);
    getGoalContributors(id, ac.signal).then((r) => setContributors(r.kind === "ok" ? { kind: "ok", data: r.data.contributors } : (r as ApiState<GoalContributor[]>)));

    (async () => {
      const [sol, ranking] = await Promise.all([
        getGoalSolutions(id, ac.signal),
        getRankedProcedures(id, undefined, ac.signal),
      ]);
      if (ranking.kind !== "ok") setRanked(ranking as ApiState<RankedProcedure[]>);
      else setRanked({ kind: "ok", data: ranking.data.ranked });
      if (sol.kind !== "ok") return setProcedures(sol as ApiState<ProcedureDetail[]>);
      const targets = sol.data.solutions.filter((solution) => solution.target_table === "procedures");
      const rowIds = targets
        .map((solution) => rankedProcedureRowId(ranking.kind === "ok" ? ranking.data.ranked : [], solution.target_id))
        .filter((rowId): rowId is string => Boolean(rowId));
      const details = await Promise.all(rowIds.map((rowId) => getProcedure(rowId, ac.signal)));
      const ok = details.filter((detail): detail is { kind: "ok"; data: ProcedureDetail } => detail.kind === "ok").map((detail) => detail.data);
      setProcedures({ kind: "ok", data: ok });
    })();

    return () => ac.abort();
  }, [id]);

  const claims = useMemo(() => {
    if (procedures.kind !== "ok") return [];
    const seen = new Map<string, Record<string, unknown>>();
    for (const p of procedures.data) for (const c of p.claims ?? []) {
      const cid = String(c.id ?? c.name ?? JSON.stringify(c));
      if (!seen.has(cid)) seen.set(cid, c as Record<string, unknown>);
    }
    return [...seen.values()];
  }, [procedures]);

  if (goal.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={goal} empty={undefined} />
      </section>
    );
  }
  const p = goal.data;
  const hierarchyUnavailable = p.hierarchy_available === false;
  const directSpecifics = p.specializes ?? [];
  const directAbstracts = p.abstracts ?? [];
  const rankingText = rankingExplanation(p.ranking);
  const rankingSignals = rankingSignalSummary(p.ranking);
  const groups: Array<{ bucket: RankedProcedure["bucket"]; label: string }> = [
    { bucket: "verified", label: "Verified" }, { bucket: "candidate", label: "Candidate" },
    { bucket: "needs_evidence", label: "Needs evidence" }, { bucket: "verified_failure", label: "Verified failure" },
  ];

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>GOAL</b><span>/ {categoryOf(p)}</span></div>
        <h1 className="h1" style={{ gridColumn: "1 / span 10", fontSize: "clamp(26px, 3.2vw, 44px)", lineHeight: 1.15 }}>
          <AnimatedHeading>{p.canonical_name}</AnimatedHeading>
        </h1>
        {p.description && <p className="lead">{p.description}</p>}
      </section>

      <section className="frame grid" style={{ paddingBottom: 100, rowGap: 40 }}>
        <div className="log" style={{ gridColumn: "1 / span 12", maxWidth: "48em" }}>
          {p.objective && (<div><span>Objective</span><span>{p.objective}</span></div>)}
          {p.rationale && (<div><span>Rationale</span><span>{p.rationale}</span></div>)}
          {p.expected_outcome && humanize(p.expected_outcome).map(([key, value]) => (
            <div key={`outcome-${key}`}><span>Success</span><span>{value}</span></div>
          ))}
          {p.verification_requirement && humanize(p.verification_requirement).map(([key, value]) => (
            <div key={`verification-${key}`}><span>Verification</span><span>{value}</span></div>
          ))}
          {Array.isArray(p.constraints) && p.constraints.length > 0 && (
            <div><span>Constraints</span><span>{p.constraints.map((constraint) => (typeof constraint === "string" ? constraint : JSON.stringify(constraint))).join(" · ")}</span></div>
          )}
          <div><span>Status</span><span style={{ textTransform: "capitalize" }}>{p.status ?? "unknown"}</span></div>
          <div><span>Direct resolution</span><span>{goalResolutionLabel(p)}{p.resolved_at ? ` · ${new Date(p.resolved_at).toLocaleDateString()}` : ""}</span></div>
          {p.coverage && p.coverage.total_count > 0 && <div><span>Descendant coverage</span><span>{p.coverage.resolved_count} of {p.coverage.total_count} more specific goals resolved (not this goal)</span></div>}
          <div><span>Ranking</span><span>{rankingText ?? "Not ranked"}</span></div>
          {rankingSignals.map((signal) => <div key={signal}><span>Signal</span><span>{signal}</span></div>)}
          {(p.scope_type || p.created_by || p.proposer) && (
            <div><span>Scope</span><span>{[p.scope_type, p.created_by ? `contributed by ${p.created_by}` : p.proposer ? `proposed by ${p.proposer}` : null].filter(Boolean).join(" · ") || "Not recorded"}</span></div>
          )}
        </div>

        <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Goal hierarchy</h2>
          <p className="small dim">Direct accepted relationships to this goal, separate from resolution on more specific goals.</p>
        </div>
        <div className="log" style={{ gridColumn: "1 / span 12", maxWidth: "48em" }}>
          {hierarchyUnavailable ? (
            <div><span>Hierarchy</span><span>Temporarily unavailable. Reload in a moment.</span></div>
          ) : (
            <>
              {typeof p.abstraction_level === "number" && (
                <div><span>Abstraction level</span><span>{p.abstraction_level === 0 ? "0 (no more abstract goal recorded)" : p.abstraction_level}</span></div>
              )}
              <div><span>More specific goals</span><span>{directSpecifics.length ? `${directSpecifics.length} direct` : "None recorded"}</span></div>
              <div><span>More abstract goals</span><span>{directAbstracts.length ? `${directAbstracts.length} direct` : "None recorded"}</span></div>
              {p.hierarchy_complete === false && (
                <div><span>Note</span><span>Some related goals couldn’t be loaded right now, so these lists may be incomplete. Reload in a moment.</span></div>
              )}
              {isReviewer && (
                <div><span>Review</span><span><Link href="/review/hierarchy" style={{ textDecoration: "underline" }}>Proposed relationships awaiting review</Link></span></div>
              )}
            </>
          )}
        </div>
        {directSpecifics.length > 0 && (
          <div className="cells" style={{ gridColumn: "1 / span 12" }}>
            {directSpecifics.map((specific) => (
              <div className="cell" key={specific.id}>
                <div className="n"><span>↓</span></div>
                <div>
                  <Link href={`/goals/${specific.id}`}>{specific.canonical_name}</Link>
                  {specific.description && <p>{specific.description}</p>}
                  <span className="status" data-s={specific.resolved_at ? "verified" : "unknown"} style={{ marginTop: 8 }}>{goalResolutionLabel(specific)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {directAbstracts.length > 0 && (
          <div className="cells" style={{ gridColumn: "1 / span 12" }}>
            {directAbstracts.map((abstract) => (
              <div className="cell" key={abstract.id}>
                <div className="n"><span>↑</span></div>
                <div>
                  <Link href={`/goals/${abstract.id}`}>{abstract.canonical_name}</Link>
                  {abstract.description && <p>{abstract.description}</p>}
                  <span className="status" data-s={abstract.resolved_at ? "verified" : "unknown"} style={{ marginTop: 8 }}>{goalResolutionLabel(abstract)}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* what we know */}
        <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>What we know</h2>
          <p className="small dim">Claims carried by the procedures recorded for this goal.</p>
        </div>
        {claims.length > 0 ? (
          <div className="cells">
            {claims.slice(0, 8).map((c, i) => (
              <div className="cell" key={String(c.id ?? i)}>
                <div className="n"><span>{String(i + 1).padStart(2, "0")}</span></div>
                <div>
                  <p>{String(c.statement ?? c.name ?? c.description ?? "Untitled claim")}</p>
                  {typeof c.t_invalid !== "undefined" && (
                    <span className="status" data-s={c.t_invalid ? "unknown" : "evidenced"} style={{ marginTop: 10 }}>
                      {c.t_invalid ? "retired" : "current"}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}><p>No claims are attached to this goal’s procedures yet.</p></div>
        )}

        {/* verification */}
        <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>How do we know this was solved?</h2>
          <p className="small dim">Success criteria and evaluation methods recorded as Benchmarks for this goal. A goal may use more than one kind.</p>
        </div>
        {p.benchmarks && p.benchmarks.length > 0 ? (
          <div style={{ gridColumn: "1 / span 12", display: "grid", gap: 20 }}>
            {p.benchmarks.map((b) => (
              <div className="log" key={b.id} style={{ padding: "16px 18px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <b style={{ fontWeight: 400, fontSize: 17 }}>{b.name}</b>
                  <span className="status" data-s={b.status === "frozen" ? "verified" : "unknown"}>{b.status ?? "draft"}</span>
                </div>
                {b.description && <p style={{ marginBottom: 8, fontSize: 14.5 }}>{b.description}</p>}
                {humanize(b.success_criteria).map(([k, v]) => (<div key={"sc" + k}><span>{k}</span><span>{v}</span></div>))}
                {humanize(b.environment_specification).map(([k, v]) => (<div key={"env" + k}><span>{k}</span><span>{v}</span></div>))}
                {humanize(b.comparison_policy).map(([k, v]) => (<div key={"cp" + k}><span>{k}</span><span>{v}</span></div>))}
              </div>
            ))}
          </div>
        ) : (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}><p>No benchmark or success criteria recorded for this goal yet.</p></div>
        )}

        {/* community demand (Credit commitments; escrow bounty) */}
        <GoalDemandPanel goalId={id} resolved={Boolean(p.resolved_at)} />

        {/* pending review (reviewer-only) */}
        {isReviewer && (
          (pendingWays.kind === "ok" && pendingWays.data.length > 0) ||
          (pendingBenchmarks.kind === "ok" && pendingBenchmarks.data.length > 0)
        ) && (
          <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
            <div className="marker caption"><b>PENDING REVIEW</b></div>
            {reviewError && <p className="small dim" style={{ marginBottom: 12 }}>{reviewError}</p>}
            <div style={{ display: "grid", gap: 12 }}>
              {pendingWays.kind === "ok" && pendingWays.data.map((s) => (
                <div className="log" key={s.id} style={{ padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <div>
                    <span className="status" data-s="candidate" style={{ marginRight: 10 }}>way</span>
                    <span>{(s.name as string) || "Untitled way"}</span>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="button" className="btn-ink" disabled={reviewBusy === s.id} onClick={() => review("way", s.id, "accepted")}>
                      <span>{reviewBusy === s.id ? "…" : "Accept"}</span>
                    </button>
                    <button type="button" className="btn-ink" style={{ background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}
                            disabled={reviewBusy === s.id} onClick={() => review("way", s.id, "rejected")}>
                      <span>Reject</span>
                    </button>
                  </div>
                </div>
              ))}
              {pendingBenchmarks.kind === "ok" && pendingBenchmarks.data.map((s) => (
                <div className="log" key={s.id} style={{ padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <div>
                    <span className="status" data-s="candidate" style={{ marginRight: 10 }}>benchmark</span>
                    <span>{(s.name as string) || "Untitled benchmark"}</span>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="button" className="btn-ink" disabled={reviewBusy === s.id} onClick={() => review("benchmark", s.id, "accepted")}>
                      <span>{reviewBusy === s.id ? "…" : "Accept"}</span>
                    </button>
                    <button type="button" className="btn-ink" style={{ background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}
                            disabled={reviewBusy === s.id} onClick={() => review("benchmark", s.id, "rejected")}>
                      <span>Reject</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ways to do this */}
        <div style={{ gridColumn: "1 / span 12", marginTop: 16, display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h2 className="h3" style={{ marginBottom: 4 }}>Ways to do this</h2>
            <p className="small dim">Ranked for this goal’s recorded evidence, not a universal “best.”</p>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <Link href={`/goals/${id}/contribute/way`} className="btn-ink"><span>Contribute a way</span><span className="sq" aria-hidden="true">→</span></Link>
            <Link href={`/goals/${id}/contribute/benchmark`} className="btn-ink" style={{ background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}>
              <span>Contribute a benchmark</span><span className="sq" aria-hidden="true">→</span>
            </Link>
          </div>
        </div>

        {ranked.kind === "ok" && ranked.data.length > 0 ? (
          groups.map(({ bucket, label }) => {
            const items = ranked.data.filter((r) => r.bucket === bucket);
            if (items.length === 0) return null;
            return (
              <div key={bucket} style={{ gridColumn: "1 / span 12" }}>
                <div className="caption dim" style={{ margin: "20px 0 4px" }}>{label} · {items.length}</div>
                <ul className="list" style={{ marginTop: 0 }}>
                  {items.map((pr) => (
                    <li key={pr.procedure_row_id}>
                      <Link href={`/goals/${id}/procedures/${pr.procedure_row_id}`}>
                        <span className="n">#{pr.rank}</span>
                        <div>
                          <h3>{pr.display_name || "Untitled procedure"}</h3>
                          {pr.display_description && <p className="desc">{pr.display_description}</p>}
                          <div className="meta">
                            {pr.applicability_summary && <span>{pr.applicability_summary}</span>}
                            {pr.context_matched && <span>matches your context</span>}
                            <span>{pr.success_count} verified · {pr.evidence_count} recorded</span>
                            {pr.created_by && <span>by {pr.created_by}</span>}
                          </div>
                        </div>
                        <span className="status" data-s={bucketToStatus[bucket]}>{pr.bucket_label}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        ) : (
          <StateNotice state={ranked} empty={ranked.kind === "ok" ? "No procedures are recorded for this goal yet." : undefined} />
        )}

        {/* contributors */}
        {contributors.kind === "ok" && contributors.data.length > 0 && (
          <>
            <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
              <h2 className="h3" style={{ marginBottom: 4 }}>Who’s contributed here</h2>
              <p className="small dim">Contribution to this goal specifically, not a site-wide ranking.</p>
            </div>
            <div className="run-ledger">
              {contributors.data.slice(0, 8).map((c) => {
                const parts = [
                  c.procedures ? `${c.procedures} way${c.procedures === 1 ? "" : "s"}` : null,
                  c.improvements ? `${c.improvements} improvement${c.improvements === 1 ? "" : "s"}` : null,
                  c.benchmarks ? `${c.benchmarks} benchmark${c.benchmarks === 1 ? "" : "s"}` : null,
                ].filter(Boolean);
                return <div key={c.contributor_id}><b>{c.contributor_id}</b><span>{parts.join(", ")} on this goal</span></div>;
              })}
            </div>
          </>
        )}
      </section>
    </>
  );
}
