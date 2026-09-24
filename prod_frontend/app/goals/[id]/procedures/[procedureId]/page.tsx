"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import TryThisWay from "@/components/TryThisWay";
import {
  getGoal, getGoalEvaluations, getProcedure, getProcedureEvidence, getProcedureVersions, getRankedProcedures,
  humanize, rankedProcedureRowId,
  type Evaluation, type EvidenceRow, type Goal, type ProcedureDetail, type ProcedureVersionRow, type RankedProcedure,
} from "@/lib/kel-api";
import {
  evaluationsForProcedure, evidenceDescription, evidenceOutcome, evidenceOutcomeLabel, evidenceTimestamp,
  procedureConfidenceLabel, procedureLane, procedureRankingExplanation,
} from "@/lib/goal-display";
import type { ApiState } from "@/lib/api";

const bucketToStatus: Record<string, string> = {
  verified: "verified", candidate: "candidate", needs_evidence: "unknown", verified_failure: "failed",
};
const outcomeToStatus = (outcome: string) => outcome === "success" || outcome === "pass" ? "successful" : outcome === "failure" || outcome === "fail" ? "failed" : "unknown";
const step = (value: unknown): string =>
  typeof value === "string" ? value : typeof value === "object" && value ? String((value as Record<string, unknown>).description ?? (value as Record<string, unknown>).action ?? (value as Record<string, unknown>).name ?? JSON.stringify(value)) : String(value);

export default function ProcedurePage() {
  const { id, procedureId } = useParams<{ id: string; procedureId: string }>();
  const [goal, setGoal] = useState<ApiState<Goal>>({ kind: "loading" });
  const [proc, setProc] = useState<ApiState<ProcedureDetail>>({ kind: "loading" });
  const [versions, setVersions] = useState<ApiState<ProcedureVersionRow[]>>({ kind: "loading" });
  const [evidence, setEvidence] = useState<ApiState<EvidenceRow[]>>({ kind: "loading" });
  const [ranked, setRanked] = useState<ApiState<RankedProcedure[]>>({ kind: "loading" });
  const [evals, setEvals] = useState<Evaluation[]>([]);

  useEffect(() => {
    const ac = new AbortController();
    getGoal(id, ac.signal).then(setGoal);

    (async () => {
      const rankingResponse = await getRankedProcedures(id, undefined, ac.signal);
      setRanked(rankingResponse.kind === "ok" ? { kind: "ok", data: rankingResponse.data.ranked } : (rankingResponse as ApiState<RankedProcedure[]>));
      const rankEntry = rankingResponse.kind === "ok"
        ? rankingResponse.data.ranked.find((entry) => entry.procedure_row_id === procedureId || entry.procedure_id === procedureId) ?? null
        : null;
      const rowId = rankedProcedureRowId(rankingResponse.kind === "ok" ? rankingResponse.data.ranked : [], procedureId) ?? rankEntry?.procedure_row_id ?? procedureId;
      const [procedureResponse, versionsResponse, evidenceResponse] = await Promise.all([
        getProcedure(rowId, ac.signal),
        getProcedureVersions(rowId, ac.signal),
        getProcedureEvidence(rowId, ac.signal),
      ]);
      setProc(procedureResponse);
      setVersions(versionsResponse.kind === "ok" ? { kind: "ok", data: Array.isArray(versionsResponse.data) ? versionsResponse.data : [] } : (versionsResponse as ApiState<ProcedureVersionRow[]>));
      setEvidence(evidenceResponse.kind === "ok" ? { kind: "ok", data: Array.isArray(evidenceResponse.data) ? evidenceResponse.data : [] } : (evidenceResponse as ApiState<EvidenceRow[]>));
      const evaluationResponse = await getGoalEvaluations(id, ac.signal);
      if (evaluationResponse.kind === "ok") {
        const stableId = procedureResponse.kind === "ok" ? procedureResponse.data.procedure_id : rankEntry?.procedure_id;
        setEvals(evaluationsForProcedure(evaluationResponse.data.evaluations, stableId));
      }
    })();

    return () => ac.abort();
  }, [id, procedureId]);

  const rankEntry = useMemo(
    () => (ranked.kind === "ok" ? ranked.data.find((entry) => entry.procedure_row_id === procedureId || entry.procedure_id === procedureId) ?? null : null),
    [ranked, procedureId],
  );

  if (proc.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={proc} empty={undefined} />
      </section>
    );
  }

  const p = proc.data;
  const evidenceSummary = p.evidence_summary;
  const verifiedSuccessN = evidenceSummary?.verified_success ?? evidenceSummary?.success_count ?? 0;
  const verifiedFailureN = evidenceSummary?.verified_failure ?? evidenceSummary?.failure_count ?? 0;
  const claimedSuccessN = evidenceSummary?.claimed_success ?? 0;
  const unknownN = evidenceSummary?.unknown ?? 0;
  const confidenceLabel = rankEntry ? procedureConfidenceLabel(rankEntry) : null;
  const lane = rankEntry ? procedureLane(rankEntry) : null;
  const rankingExplanation = rankEntry ? procedureRankingExplanation(rankEntry) : null;

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}>
          <b>PROCEDURE</b>
          {goal.kind === "ok" && <span>/ for <Link href={`/goals/${id}`} style={{ textDecoration: "underline" }}>{goal.data.canonical_name}</Link></span>}
        </div>
        <h1 className="display" style={{ gridColumn: "1 / span 10" }}><AnimatedHeading>{p.display_name || p.name || "Untitled procedure"}</AnimatedHeading></h1>
        <div className="states" style={{ marginTop: 20 }}>
          {rankEntry && <span className="status" data-s={bucketToStatus[rankEntry.bucket]}>{rankEntry.bucket_label}</span>}
          {rankEntry && <span className="caption dim">Ranked #{rankEntry.rank} of {rankEntry.of} for this goal{rankEntry.context_matched ? " (matches your context)" : ""}</span>}
          {lane && <span className="caption dim">{lane}</span>}
        </div>
      </section>

      <section className="frame grid" style={{ paddingBottom: 100, rowGap: 44 }}>
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Works when</h2>
          {p.applicability_summary && <p className="lead" style={{ marginBottom: 14 }}>{p.applicability_summary}</p>}
          {(Array.isArray(p.preconditions) && p.preconditions.length > 0) || (Array.isArray(p.invariants) && p.invariants.length > 0) ? (
            <div className="log" style={{ maxWidth: "60em" }}>
              {(p.preconditions ?? []).map((condition, index) => (<div key={`pre-${index}`}><span>needs</span><span>{step(condition)}</span></div>))}
              {(p.invariants ?? []).map((condition, index) => (<div key={`inv-${index}`}><span>keeps true</span><span>{step(condition)}</span></div>))}
            </div>
          ) : !p.applicability_summary ? (
            <p className="small dim">No applicability, preconditions or invariants are recorded yet.</p>
          ) : null}
        </div>

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Why this way</h2>
          {p.rationale || p.domain || p.provenance ? (
            <p className="small dim">{[p.rationale, p.domain, p.provenance].filter(Boolean).join(" · ")}</p>
          ) : (
            <p className="small dim">No contributor rationale recorded yet.</p>
          )}
          {p.expected_outcome && humanize(p.expected_outcome).map(([key, value]) => (
            <div className="log" key={`outcome-${key}`} style={{ marginTop: 12, maxWidth: "60em" }}><div><span>Expected outcome</span><span>{value}</span></div></div>
          ))}
          {confidenceLabel && <p className="small dim" style={{ marginTop: 12 }}>{confidenceLabel}</p>}
          {rankingExplanation && <p className="small dim" style={{ marginTop: 8 }}>{rankingExplanation}</p>}
        </div>

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Steps</h2>
          {Array.isArray(p.steps) && p.steps.length > 0 ? (
            <ol className="steps" style={{ gridColumn: "auto" }}>
              {p.steps.map((stepValue, index) => (
                <li key={index}><span className="n">{String(index + 1).padStart(2, "0")}</span><div><p>{step(stepValue)}</p></div></li>
              ))}
            </ol>
          ) : (
            <p className="small dim">No steps recorded yet.</p>
          )}
        </div>

        {Array.isArray(p.executor_kinds) && p.executor_kinds.length > 0 && (
          <div style={{ gridColumn: "1 / span 12" }}>
            <h2 className="h3" style={{ marginBottom: 12 }}>Uses</h2>
            <div className="scopes">{p.executor_kinds.map((kind) => <span key={kind}>{kind}</span>)}</div>
          </div>
        )}

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Evidence</h2>
          <p className="small dim" style={{ marginBottom: 16 }}>
            {p.t_created ? `Recorded since ${new Date(p.t_created).toLocaleDateString()}.` : ""} {evals.length > 0 ? `Used by ${evals.length} benchmark evaluation${evals.length === 1 ? "" : "s"} for this goal.` : "Not yet used by a benchmark evaluation for this goal."}
          </p>
          <div className="run-ledger" style={{ marginTop: 0 }}>
            <div><b>{verifiedSuccessN}</b><span>Verified successful, a real execution, independently confirmed.</span></div>
            <div><b>{verifiedFailureN}</b><span>Verified failed, a real execution that didn&rsquo;t reach the goal.</span></div>
            <div><b>{claimedSuccessN}</b><span>Claimed, not verified, reported success without execution-backed proof.</span></div>
            <div><b>{unknownN}</b><span>Unknown outcome, recorded, but not resolved either way.</span></div>
          </div>
        </div>

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>What happened</h2>
          {evidence.kind === "ok" && evidence.data.length > 0 ? (
            <ul className="list" style={{ marginTop: 0 }}>
              {evidence.data.slice(0, 12).map((row, index) => {
                const outcome = evidenceOutcome(row);
                const timestamp = evidenceTimestamp(row);
                return (
                  <li key={String(row.id ?? index)}>
                    <span className="n">{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <p className="desc" style={{ marginTop: 0 }}>{evidenceDescription(row)}</p>
                      {row.success_criteria && humanize(row.success_criteria).map(([key, value]) => <span className="meta" key={`criteria-${key}`}><span>{key}: {value}</span></span>)}
                      {row.created_by && <span className="meta"><span>Recorded by {row.created_by}</span></span>}
                      {timestamp && <span className="meta"><span>{new Date(timestamp).toLocaleString()}</span></span>}
                    </div>
                    <span className="status" data-s={outcomeToStatus(outcome)}>{evidenceOutcomeLabel(row)}</span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <StateNotice state={evidence} empty={evidence.kind === "ok" ? "No individual runs are recorded yet." : undefined} />
          )}
        </div>

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Contribution</h2>
          <p className="small dim">{p.created_by ? `Contributed by ${p.created_by}` : "Contributor not recorded"}{p.version ? ` · version ${p.version}` : ""}</p>
        </div>

        {versions.kind === "ok" && versions.data.length > 0 && (
          <div className="lineage" style={{ marginTop: 0 }}>
            <h2 className="h3">Lineage</h2>
            <ol>
              {[...versions.data].sort((a, b) => (a.version ?? 0) - (b.version ?? 0)).map((version, index, all) => (
                <li key={String(version.id ?? index)} className={index === all.length - 1 ? "key" : undefined}>
                  {index === 0 ? "Original" : index === all.length - 1 ? "Current" : "Improved"}
                  <small>v{version.version ?? index + 1}{version.t_created ? ` · ${new Date(version.t_created).toLocaleDateString()}` : ""}{version.created_by ? ` · ${version.created_by}` : ""}</small>
                </li>
              ))}
            </ol>
          </div>
        )}

        <div style={{ gridColumn: "1 / span 7" }}>
          <h2 className="h3" style={{ marginBottom: 16 }}>Try this way</h2>
          <TryThisWay procedureId={p.id} />
        </div>
      </section>
    </>
  );
}
