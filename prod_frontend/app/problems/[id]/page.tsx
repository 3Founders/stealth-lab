"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import StateNotice from "@/components/NotConnected";
import {
  getProblem, getProblemBenchmarks, getProblemSolutions, getProcedure, humanize,
  rankScore, verificationBucket, type Benchmark, type Problem, type ProcedureDetail, type Solution,
} from "@/lib/kel-api";
import { categoryOf } from "@/lib/mock-adapter";
import type { ApiState } from "@/lib/api";

const bucketToStatus: Record<string, string> = { Verified: "verified", Candidate: "candidate", "Needs evidence": "unknown" };

export default function GoalPage() {
  const { id } = useParams<{ id: string }>();
  const [problem, setProblem] = useState<ApiState<Problem>>({ kind: "loading" });
  const [benchmarks, setBenchmarks] = useState<ApiState<Benchmark[]>>({ kind: "loading" });
  const [procedures, setProcedures] = useState<ApiState<ProcedureDetail[]>>({ kind: "loading" });

  useEffect(() => {
    const ac = new AbortController();
    getProblem(id, ac.signal).then(setProblem);
    getProblemBenchmarks(id, ac.signal).then((r) => setBenchmarks(r.kind === "ok" ? { kind: "ok", data: r.data.benchmarks ?? [] } : (r as ApiState<Benchmark[]>)));

    // Procedures for this goal come through the Solution association (problem -> solution ->
    // procedure); each one is then fetched for its real verification/evidence/claims detail.
    (async () => {
      const sol = await getProblemSolutions(id, ac.signal);
      if (sol.kind !== "ok") return setProcedures(sol as ApiState<ProcedureDetail[]>);
      const targets = sol.data.solutions.filter((s) => s.target_table === "procedures");
      const details = await Promise.all(targets.map((s) => getProcedure(s.target_id, ac.signal)));
      const ok = details.filter((d): d is { kind: "ok"; data: ProcedureDetail } => d.kind === "ok").map((d) => d.data);
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

  const ranked = useMemo(() => {
    if (procedures.kind !== "ok") return [];
    return procedures.data
      .map((p) => ({ p, bucket: verificationBucket(p), score: rankScore(p) }))
      .sort((a, b) => b.score - a.score);
  }, [procedures]);

  const contributors = useMemo(() => {
    if (procedures.kind !== "ok") return [];
    const counts = new Map<string, number>();
    for (const p of procedures.data) {
      const who = (p.created_by as string) || null;
      if (!who) continue;
      counts.set(who, (counts.get(who) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [procedures]);

  if (problem.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={problem} empty={undefined} />
      </section>
    );
  }
  const p = problem.data;
  const groups: Array<{ label: "Verified" | "Candidate" | "Needs evidence" }> = [
    { label: "Verified" }, { label: "Candidate" }, { label: "Needs evidence" },
  ];

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>GOAL</b><span>/ {categoryOf(p)}</span></div>
        <h1 className="display">{p.title}</h1>
        {p.description && <p className="lead">{p.description}</p>}
      </section>

      <section className="frame grid" style={{ paddingBottom: 100, rowGap: 40 }}>
        {/* structured info */}
        <div className="cells">
          {p.objective && (
            <div className="cell"><div className="n"><span>01</span></div><div><span className="q">Objective</span><p>{p.objective}</p></div></div>
          )}
          {Array.isArray(p.constraints) && p.constraints.length > 0 && (
            <div className="cell">
              <div className="n"><span>02</span></div>
              <div><span className="q">Constraints</span><p>{p.constraints.map((c) => (typeof c === "string" ? c : JSON.stringify(c))).join(" · ")}</p></div>
            </div>
          )}
          <div className="cell"><div className="n"><span>03</span></div><div><span className="q">Status</span><p style={{ textTransform: "capitalize" }}>{p.status ?? "unknown"}</p></div></div>
          {(p.scope_type || p.proposer) && (
            <div className="cell">
              <div className="n"><span>04</span></div>
              <div><span className="q">Scope</span><p>{[p.scope_type, p.proposer ? `proposed by ${p.proposer}` : null].filter(Boolean).join(" · ") || "Not recorded"}</p></div>
            </div>
          )}
        </div>

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
        {benchmarks.kind === "ok" && benchmarks.data.length > 0 ? (
          <div style={{ gridColumn: "1 / span 12", display: "grid", gap: 20 }}>
            {benchmarks.data.map((b) => (
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
          <StateNotice state={benchmarks} empty={benchmarks.kind === "ok" ? "No benchmark or success criteria recorded for this goal yet." : undefined} />
        )}

        {/* ways to do this */}
        <div style={{ gridColumn: "1 / span 12", marginTop: 16, display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h2 className="h3" style={{ marginBottom: 4 }}>Ways to do this</h2>
            <p className="small dim">Ranked for this goal’s recorded evidence — not a universal “best.”</p>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <Link href="/sign-in" className="btn-ink"><span>Contribute a way</span><span className="sq" aria-hidden="true">→</span></Link>
            <Link href="/sign-in" className="btn-ink" style={{ background: "transparent", color: "var(--ink)", border: "1px solid var(--rule-strong)" }}>
              <span>Contribute a benchmark</span><span className="sq" aria-hidden="true">→</span>
            </Link>
          </div>
        </div>

        {procedures.kind === "ok" && ranked.length > 0 ? (
          groups.map(({ label }) => {
            const items = ranked.filter((r) => r.bucket === label);
            if (items.length === 0) return null;
            return (
              <div key={label} style={{ gridColumn: "1 / span 12" }}>
                <div className="caption dim" style={{ margin: "20px 0 4px" }}>{label} · {items.length}</div>
                <ul className="list" style={{ marginTop: 0 }}>
                  {items.map(({ p: pr, bucket }, i) => (
                    <li key={pr.id}>
                      <Link href={`/problems/${id}/procedures/${pr.id}`}>
                        <span className="n">#{i + 1}</span>
                        <div>
                          <h3>{pr.display_name || pr.name || "Untitled procedure"}</h3>
                          {pr.display_description && <p className="desc">{pr.display_description}</p>}
                          <div className="meta">
                            {pr.applicability_summary && <span>{pr.applicability_summary}</span>}
                            <span>{(pr.evidence_summary?.success_count ?? 0)} successful · {(pr.evidence_summary?.failure_count ?? 0)} failed</span>
                            {pr.created_by && <span>by {pr.created_by}</span>}
                          </div>
                        </div>
                        <span className="status" data-s={bucketToStatus[bucket]}>{bucket}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        ) : (
          <StateNotice state={procedures} empty={procedures.kind === "ok" ? "No procedures are recorded for this goal yet." : undefined} />
        )}

        {/* contributors */}
        {contributors.length > 0 && (
          <>
            <div style={{ gridColumn: "1 / span 12", marginTop: 16 }}>
              <h2 className="h3" style={{ marginBottom: 4 }}>Who’s contributed here</h2>
              <p className="small dim">Contribution to this goal specifically, not a site-wide ranking.</p>
            </div>
            <div className="run-ledger">
              {contributors.slice(0, 8).map(([who, n]) => (
                <div key={who}><b>{who}</b><span>{n} procedure{n === 1 ? "" : "s"} on this goal</span></div>
              ))}
            </div>
          </>
        )}
      </section>
    </>
  );
}
