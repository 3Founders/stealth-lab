"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import StateNotice from "@/components/NotConnected";
import { createBenchmarkSubmission, getProblem, type Problem, type SubmissionResult } from "@/lib/kel-api";
import { getSession } from "@/lib/session";
import type { ApiState } from "@/lib/api";

export default function ContributeBenchmarkPage() {
  const { id } = useParams<{ id: string }>();
  const [problem, setProblem] = useState<ApiState<Problem>>({ kind: "loading" });
  const session = getSession();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [invariants, setInvariants] = useState<string[]>([""]);
  const [context, setContext] = useState("");
  const [method, setMethod] = useState("");
  const [implementation, setImplementation] = useState("");
  const [evidence, setEvidence] = useState("");

  const [result, setResult] = useState<ApiState<SubmissionResult>>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    getProblem(id, ac.signal).then(setProblem);
    return () => ac.abort();
  }, [id]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!session) return;
    setSubmitting(true);
    setResult({ kind: "loading" });
    const verification_method: Record<string, string> = {};
    if (context.trim()) verification_method.context = context.trim();
    if (method.trim()) verification_method.method = method.trim();
    if (implementation.trim()) verification_method.implementation = implementation.trim();
    if (evidence.trim()) verification_method.evidence = evidence.trim();
    const r = await createBenchmarkSubmission({
      goal_id: id,
      name: name.trim(),
      description: description.trim() || undefined,
      success_criteria: successCriteria.trim() ? { summary: successCriteria.trim() } : undefined,
      invariants: invariants.map((v) => v.trim()).filter(Boolean),
      verification_method: Object.keys(verification_method).length ? verification_method : undefined,
    });
    setResult(r);
    setSubmitting(false);
  }

  if (problem.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={problem} empty={undefined} />
      </section>
    );
  }

  if (!session) {
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A BENCHMARK</b><span>/ {problem.data.title}</span></div>
          <h1 className="display">Sign in to contribute.</h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="empty" style={{ gridColumn: "1 / span 8" }}>
            <b>Benchmarks are attributed to a real, signed-in account.</b>
            <p><Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in</Link> to continue.</p>
          </div>
        </section>
      </>
    );
  }

  if (result.kind === "ok") {
    const r = result.data;
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A BENCHMARK</b><span>/ {problem.data.title}</span></div>
          <h1 className="display">Submitted.</h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="cform-result">
            <b>Status: {r.status === "candidate" ? "Candidate" : r.status === "needs_review" ? "Needs review" : r.status}</b>
            <p>A benchmark describes how this Goal&rsquo;s success is checked — it isn&rsquo;t accepted automatically, and being accepted still isn&rsquo;t the same as being validated. It becomes validated once real evaluations show it can actually tell a success from a failure.</p>
            {r.status_reason && <p className="small dim">Automated review noted: {r.status_reason}</p>}
            <p style={{ marginTop: 16 }}><Link href={`/problems/${id}`} style={{ textDecoration: "underline" }}>Back to the goal</Link></p>
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A BENCHMARK</b><span>/ {problem.data.title}</span></div>
        <h1 className="display">Propose how to check this.</h1>
        <p className="lead">A Benchmark describes how the Goal is verified — not how one procedure proves itself. It's reviewed like any other contribution.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="bname">Title</label>
            <input id="bname" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
          </div>

          <div className="cfield">
            <label htmlFor="bdesc">What it verifies</label>
            <textarea id="bdesc" value={description} onChange={(e) => setDescription(e.target.value)} required placeholder="What does this check, and why does it correspond to the goal's success criteria?" />
          </div>

          <div className="cfield">
            <label htmlFor="bcriteria">Success criteria</label>
            <textarea id="bcriteria" value={successCriteria} onChange={(e) => setSuccessCriteria(e.target.value)} placeholder="What outcome counts as success?" />
          </div>

          <div className="cfield">
            <label>Invariants / constraints</label>
            {invariants.map((v, i) => (
              <div className="cstep-row" key={i}>
                <input type="text" value={v} onChange={(e) => setInvariants(invariants.map((x, j) => (j === i ? e.target.value : x)))} placeholder="What must hold true throughout" />
                {invariants.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setInvariants(invariants.filter((_, j) => j !== i))} aria-label={`Remove invariant ${i + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setInvariants([...invariants, ""])}>+ Add invariant</button>
          </div>

          <div className="cfield">
            <label htmlFor="bcontext">Context</label>
            <textarea id="bcontext" value={context} onChange={(e) => setContext(e.target.value)} placeholder="Which situations this benchmark applies to" />
          </div>

          <div className="cfield">
            <label htmlFor="bmethod">Method</label>
            <textarea id="bmethod" value={method} onChange={(e) => setMethod(e.target.value)} placeholder="Deterministic test, environment check, human verification, a combination..." />
          </div>

          <div className="cfield">
            <label htmlFor="bimpl">Implementation (if executable)</label>
            <textarea id="bimpl" value={implementation} onChange={(e) => setImplementation(e.target.value)} placeholder="Optional — how it's actually run" />
          </div>

          <div className="cfield">
            <label htmlFor="bevidence">Supporting evidence</label>
            <textarea id="bevidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="Why this is a fair way to check the goal (optional)" />
          </div>

          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={submitting || !name.trim() || !description.trim()}>
              <span>{submitting ? "Submitting…" : "Submit as candidate"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            {result.kind === "unauthenticated" && <span className="small dim">Your session expired — <Link href="/sign-in" style={{ textDecoration: "underline" }}>sign in again</Link>.</span>}
            {result.kind === "error" && <span className="small dim">{result.message}</span>}
          </div>
        </form>
        <p className="cform-note">A Procedure cannot redefine what success means for its own Goal — this benchmark belongs to the Goal, not to any one way of accomplishing it. If you&rsquo;ve also contributed a way here, that&rsquo;s flagged for review, not rejected.</p>
      </section>
    </>
  );
}
