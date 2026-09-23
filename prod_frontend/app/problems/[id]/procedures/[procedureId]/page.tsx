"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import TryThisWay from "@/components/TryThisWay";
import {
  getProblem, getProblemEvaluations, getProcedure, getProcedureEvidence, getProcedureVersions, getRankedProcedures,
  type Evaluation, type EvidenceRow, type Problem, type ProcedureDetail, type ProcedureVersionRow, type RankedProcedure,
} from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";

const bucketToStatus: Record<string, string> = {
  verified: "verified", candidate: "candidate", needs_evidence: "unknown", verified_failure: "failed",
};
const outcomeToStatus = (o?: string) => (o === "success" || o === "pass" ? "successful" : o === "failure" || o === "fail" ? "failed" : "unknown");
const step = (s: unknown): string =>
  typeof s === "string" ? s : typeof s === "object" && s ? String((s as Record<string, unknown>).description ?? (s as Record<string, unknown>).action ?? (s as Record<string, unknown>).name ?? JSON.stringify(s)) : String(s);

export default function ProcedurePage() {
  const { id, procedureId } = useParams<{ id: string; procedureId: string }>();
  const [problem, setProblem] = useState<ApiState<Problem>>({ kind: "loading" });
  const [proc, setProc] = useState<ApiState<ProcedureDetail>>({ kind: "loading" });
  const [versions, setVersions] = useState<ApiState<ProcedureVersionRow[]>>({ kind: "loading" });
  const [evidence, setEvidence] = useState<ApiState<EvidenceRow[]>>({ kind: "loading" });
  const [ranked, setRanked] = useState<ApiState<RankedProcedure[]>>({ kind: "loading" });
  const [evals, setEvals] = useState<Evaluation[]>([]);

  useEffect(() => {
    const ac = new AbortController();
    getProblem(id, ac.signal).then(setProblem);
    getProcedure(procedureId, ac.signal).then(setProc);
    getProcedureVersions(procedureId, ac.signal).then((r) => setVersions(r.kind === "ok" ? { kind: "ok", data: Array.isArray(r.data) ? r.data : [] } : (r as ApiState<ProcedureVersionRow[]>)));
    getProcedureEvidence(procedureId, ac.signal).then((r) => setEvidence(r.kind === "ok" ? { kind: "ok", data: Array.isArray(r.data) ? r.data : [] } : (r as ApiState<EvidenceRow[]>)));
    // Rank/bucket for this procedure within its goal come from the backend
    // ONLY (app/economy/ranking.py) — never recomputed client-side.
    getRankedProcedures(id, undefined, ac.signal).then((r) => setRanked(r.kind === "ok" ? { kind: "ok", data: r.data.ranked } : (r as ApiState<RankedProcedure[]>)));

    (async () => {
      const ev = await getProblemEvaluations(id, ac.signal);
      if (ev.kind === "ok") setEvals(ev.data.evaluations.filter((e) => e.procedure_id === procedureId));
    })();

    return () => ac.abort();
  }, [id, procedureId]);

  const rankEntry = useMemo(
    () => (ranked.kind === "ok" ? ranked.data.find((r) => r.procedure_row_id === procedureId) ?? null : null),
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
  // Canonical, backend-computed trust states (app.services.evidence_trust)
  // — never inferred from a generic evidence row here. verified_success/
  // verified_failure/claimed_success/unknown are always shown separately;
  // a claim is never displayed as if it were verified.
  const es = p.evidence_summary;
  const verifiedSuccessN = es?.verified_success ?? es?.success_count ?? 0;
  const verifiedFailureN = es?.verified_failure ?? es?.failure_count ?? 0;
  const claimedSuccessN = es?.claimed_success ?? 0;
  const unknownN = es?.unknown ?? 0;

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}>
          <b>PROCEDURE</b>
          {problem.kind === "ok" && <span>/ for <Link href={`/problems/${id}`} style={{ textDecoration: "underline" }}>{problem.data.title}</Link></span>}
        </div>
        <h1 className="display" style={{ gridColumn: "1 / span 10" }}><AnimatedHeading>{p.display_name || p.name || "Untitled procedure"}</AnimatedHeading></h1>
        <div className="states" style={{ marginTop: 20 }}>
          {rankEntry && <span className="status" data-s={bucketToStatus[rankEntry.bucket]}>{rankEntry.bucket_label}</span>}
          {rankEntry && <span className="caption dim">Ranked #{rankEntry.rank} of {rankEntry.of} for this goal{rankEntry.context_matched ? " (matches your context)" : ""}</span>}
        </div>
      </section>

      <section className="frame grid" style={{ paddingBottom: 100, rowGap: 44 }}>
        {/* works when */}
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Works when</h2>
          {p.applicability_summary && <p className="lead" style={{ marginBottom: 14 }}>{p.applicability_summary}</p>}
          {(Array.isArray(p.preconditions) && p.preconditions.length > 0) || (Array.isArray(p.invariants) && p.invariants.length > 0) ? (
            <div className="log" style={{ maxWidth: "60em" }}>
              {(p.preconditions ?? []).map((c, i) => (<div key={"pre" + i}><span>needs</span><span>{step(c)}</span></div>))}
              {(p.invariants ?? []).map((c, i) => (<div key={"inv" + i}><span>keeps true</span><span>{step(c)}</span></div>))}
            </div>
          ) : !p.applicability_summary ? (
            <p className="small dim">No applicability, preconditions or invariants are recorded yet.</p>
          ) : null}
        </div>

        {/* why this way */}
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Why this way</h2>
          {p.provenance || p.domain ? (
            <p className="small dim">{[p.domain, p.provenance].filter(Boolean).join(" · ")}</p>
          ) : (
            <p className="small dim">No contributor rationale recorded yet.</p>
          )}
        </div>

        {/* steps */}
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Steps</h2>
          {Array.isArray(p.steps) && p.steps.length > 0 ? (
            <ol className="steps" style={{ gridColumn: "auto" }}>
              {p.steps.map((s, i) => (
                <li key={i}><span className="n">{String(i + 1).padStart(2, "0")}</span><div><p>{step(s)}</p></div></li>
              ))}
            </ol>
          ) : (
            <p className="small dim">No steps recorded yet.</p>
          )}
        </div>

        {/* uses */}
        {Array.isArray(p.executor_kinds) && p.executor_kinds.length > 0 && (
          <div style={{ gridColumn: "1 / span 12" }}>
            <h2 className="h3" style={{ marginBottom: 12 }}>Uses</h2>
            <div className="scopes">{p.executor_kinds.map((k) => <span key={k}>{k}</span>)}</div>
          </div>
        )}

        {/* evidence */}
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

        {/* what happened */}
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>What happened</h2>
          {evidence.kind === "ok" && evidence.data.length > 0 ? (
            <ul className="list" style={{ marginTop: 0 }}>
              {evidence.data.slice(0, 12).map((e, i) => (
                <li key={String(e.id ?? i)}>
                  <span className="n">{String(i + 1).padStart(2, "0")}</span>
                  <div>
                    <p className="desc" style={{ marginTop: 0 }}>{e.summary || e.description || "Execution recorded."}</p>
                    {e.created_at && <span className="meta"><span>{new Date(e.created_at).toLocaleString()}</span></span>}
                  </div>
                  <span className="status" data-s={outcomeToStatus(e.outcome)}>{e.outcome ?? "unknown"}</span>
                </li>
              ))}
            </ul>
          ) : (
            <StateNotice state={evidence} empty={evidence.kind === "ok" ? "No individual runs are recorded yet." : undefined} />
          )}
        </div>

        {/* contribution */}
        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 12 }}>Contribution</h2>
          <p className="small dim">{p.created_by ? `Contributed by ${p.created_by}` : "Contributor not recorded"}{p.version ? ` · version ${p.version}` : ""}</p>
        </div>

        {/* lineage */}
        {versions.kind === "ok" && versions.data.length > 0 && (
          <div className="lineage" style={{ marginTop: 0 }}>
            <h2 className="h3">Lineage</h2>
            <ol>
              {[...versions.data].sort((a, b) => (a.version ?? 0) - (b.version ?? 0)).map((v, i, arr) => (
                <li key={String(v.id ?? i)} className={i === arr.length - 1 ? "key" : undefined}>
                  {i === 0 ? "Original" : i === arr.length - 1 ? "Current" : "Improved"}
                  <small>v{v.version ?? i + 1}{v.t_created ? ` · ${new Date(v.t_created).toLocaleDateString()}` : ""}{v.created_by ? ` · ${v.created_by}` : ""}</small>
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* try this way */}
        <div style={{ gridColumn: "1 / span 7" }}>
          <h2 className="h3" style={{ marginBottom: 16 }}>Try this way</h2>
          <TryThisWay procedureId={p.id} />
        </div>
      </section>
    </>
  );
}
