"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import StateNotice from "@/components/NotConnected";
import TryThisWay from "@/components/TryThisWay";
import {
  getProblem, getProblemEvaluations, getProblemSolutions, getProcedure, getProcedureEvidence, getProcedureVersions,
  rankScore, verificationBucket, type Evaluation, type EvidenceRow, type Problem,
  type ProcedureDetail, type ProcedureVersionRow,
} from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";

const bucketToStatus: Record<string, string> = { Verified: "verified", Candidate: "candidate", "Needs evidence": "unknown" };
const outcomeToStatus = (o?: string) => (o === "success" || o === "pass" ? "successful" : o === "failure" || o === "fail" ? "failed" : "unknown");
const step = (s: unknown): string =>
  typeof s === "string" ? s : typeof s === "object" && s ? String((s as Record<string, unknown>).description ?? (s as Record<string, unknown>).action ?? (s as Record<string, unknown>).name ?? JSON.stringify(s)) : String(s);

export default function ProcedurePage() {
  const { id, procedureId } = useParams<{ id: string; procedureId: string }>();
  const [problem, setProblem] = useState<ApiState<Problem>>({ kind: "loading" });
  const [proc, setProc] = useState<ApiState<ProcedureDetail>>({ kind: "loading" });
  const [versions, setVersions] = useState<ApiState<ProcedureVersionRow[]>>({ kind: "loading" });
  const [evidence, setEvidence] = useState<ApiState<EvidenceRow[]>>({ kind: "loading" });
  const [siblingRank, setSiblingRank] = useState<{ rank: number; of: number } | null>(null);
  const [evals, setEvals] = useState<Evaluation[]>([]);

  useEffect(() => {
    const ac = new AbortController();
    getProblem(id, ac.signal).then(setProblem);
    getProcedure(procedureId, ac.signal).then(setProc);
    getProcedureVersions(procedureId, ac.signal).then((r) => setVersions(r.kind === "ok" ? { kind: "ok", data: Array.isArray(r.data) ? r.data : [] } : (r as ApiState<ProcedureVersionRow[]>)));
    getProcedureEvidence(procedureId, ac.signal).then((r) => setEvidence(r.kind === "ok" ? { kind: "ok", data: Array.isArray(r.data) ? r.data : [] } : (r as ApiState<EvidenceRow[]>)));

    (async () => {
      const sol = await getProblemSolutions(id, ac.signal);
      if (sol.kind !== "ok") return;
      const ids = sol.data.solutions.filter((s) => s.target_table === "procedures").map((s) => s.target_id);
      const details = await Promise.all(ids.map((pid) => getProcedure(pid, ac.signal)));
      const ok = details.filter((d): d is { kind: "ok"; data: ProcedureDetail } => d.kind === "ok").map((d) => d.data);
      if (ok.length) {
        const scored = ok.map((p) => ({ id: p.id, score: rankScore(p) })).sort((a, b) => b.score - a.score);
        const idx = scored.findIndex((s) => s.id === procedureId);
        if (idx >= 0) setSiblingRank({ rank: idx + 1, of: scored.length });
      }
      const ev = await getProblemEvaluations(id, ac.signal);
      if (ev.kind === "ok") setEvals(ev.data.evaluations.filter((e) => e.procedure_id === procedureId));
    })();

    return () => ac.abort();
  }, [id, procedureId]);

  const bucket = useMemo(() => (proc.kind === "ok" ? verificationBucket(proc.data) : null), [proc]);

  if (proc.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={proc} empty={undefined} />
      </section>
    );
  }
  const p = proc.data;
  const successN = p.evidence_summary?.success_count ?? 0;
  const failN = p.evidence_summary?.failure_count ?? 0;
  const unknownN = Math.max((p.evidence_summary?.total ?? 0) - successN - failN, 0);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}>
          <b>PROCEDURE</b>
          {problem.kind === "ok" && <span>/ for <Link href={`/problems/${id}`} style={{ textDecoration: "underline" }}>{problem.data.title}</Link></span>}
        </div>
        <h1 className="display" style={{ gridColumn: "1 / span 10" }}>{p.display_name || p.name || "Untitled procedure"}</h1>
        <div className="states" style={{ marginTop: 20 }}>
          {bucket && <span className="status" data-s={bucketToStatus[bucket]}>{bucket}</span>}
          {siblingRank && <span className="caption dim">Ranked #{siblingRank.rank} of {siblingRank.of} for this goal</span>}
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
          <div className="two-out">
            <div className="out ok"><h3>{successN}</h3><p>Verified runs that reached the goal.</p></div>
            <div className="out fail"><h3>{failN}</h3><p>Runs that did not reach the goal.</p></div>
          </div>
          {unknownN > 0 && <p className="small dim" style={{ marginTop: 12 }}>{unknownN} run{unknownN === 1 ? "" : "s"} with an unresolved outcome — kept as unknown, not counted as success.</p>}
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
