"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { createBenchmarkSubmission, getGoal, type Goal, type SubmissionResult } from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import type { ApiState } from "@/lib/api";

export default function ContributeBenchmarkPage() {
  const { id } = useParams<{ id: string }>();
  const [goal, setGoal] = useState<ApiState<Goal>>({ kind: "loading" });
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { getSession().then(setSession); }, []);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [successCriteria, setSuccessCriteria] = useState("");
  const [failureCriteria, setFailureCriteria] = useState<string[]>([""]);
  const [scopeConditions, setScopeConditions] = useState<string[]>([""]);
  const [invariants, setInvariants] = useState<string[]>([""]);
  const [context, setContext] = useState("");
  const [method, setMethod] = useState("");
  const [implementation, setImplementation] = useState("");
  const [evidence, setEvidence] = useState("");

  const [result, setResult] = useState<ApiState<SubmissionResult>>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    getGoal(id, ac.signal).then(setGoal);
    return () => ac.abort();
  }, [id]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!session) return;
    setSubmitting(true);
    setResult({ kind: "loading" });
    const verificationMethod: Record<string, string> = {};
    if (context.trim()) verificationMethod.context = context.trim();
    if (method.trim()) verificationMethod.method = method.trim();
    if (evidence.trim()) verificationMethod.evidence = evidence.trim();
    const r = await createBenchmarkSubmission({
      goal_id: id,
      name: name.trim(),
      description: description.trim(),
      success_criteria: { summary: successCriteria.trim() },
      failure_criteria: failureCriteria.map((value) => value.trim()).filter(Boolean),
      scope_conditions: scopeConditions.map((value) => value.trim()).filter(Boolean),
      invariants: invariants.map((value) => value.trim()).filter(Boolean),
      verification_method: Object.keys(verificationMethod).length ? verificationMethod : undefined,
      environment_specification: implementation.trim() ? { implementation: implementation.trim() } : undefined,
    });
    setResult(r);
    setSubmitting(false);
  }

  if (goal.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice state={goal} empty={undefined} />
      </section>
    );
  }

  if (!session) {
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A BENCHMARK</b><span>/ {goal.data.canonical_name}</span></div>
          <h1 className="display"><AnimatedHeading>Sign in to contribute</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="empty" style={{ gridColumn: "1 / span 8" }}>
            <b>Benchmarks are attributed to a real, signed-in account.</b>
            <p><Link href={`/sign-in?redirect=${encodeURIComponent(`/goals/${id}/contribute/benchmark`)}`} style={{ textDecoration: "underline" }}>Sign in</Link> to continue.</p>
          </div>
        </section>
      </>
    );
  }

  if (result.kind === "ok") {
    const submission = result.data;
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A BENCHMARK</b><span>/ {goal.data.canonical_name}</span></div>
          <h1 className="display"><AnimatedHeading>Submitted</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="cform-result">
            <b>Status: {submission.status === "candidate" ? "Candidate" : submission.status === "needs_review" ? "Needs review" : submission.status}</b>
            {(submission.created_by || submission.submitted_by) && <p className="small dim">Attributed to {submission.created_by || submission.submitted_by}.</p>}
            <p>A benchmark describes how this Goal&rsquo;s success is checked. It isn&rsquo;t accepted automatically, and being accepted still isn&rsquo;t the same as being validated. It becomes validated once real evaluations show it can actually tell a success from a failure.</p>
            {submission.status_reason && <p className="small dim">Automated review noted: {submission.status_reason}</p>}
            <p style={{ marginTop: 16 }}><Link href={`/goals/${id}`} style={{ textDecoration: "underline" }}>Back to the goal</Link></p>
          </div>
        </section>
      </>
    );
  }

  const hasFailureCriterion = failureCriteria.some((value) => value.trim());
  const hasScopeCondition = scopeConditions.some((value) => value.trim());

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A BENCHMARK</b><span>/ {goal.data.canonical_name}</span></div>
        <h1 className="display"><AnimatedHeading>Propose how to check this</AnimatedHeading></h1>
        <p className="lead">A Benchmark describes how the Goal is verified, not how one procedure proves itself. It&rsquo;s reviewed like any other contribution.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="bname">Title</label>
            <input id="bname" type="text" value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} />
          </div>

          <div className="cfield">
            <label htmlFor="bdesc">Rationale / what it verifies</label>
            <textarea id="bdesc" value={description} onChange={(event) => setDescription(event.target.value)} required placeholder="What does this check, and why does it correspond to the goal's success criteria?" />
          </div>

          <div className="cfield">
            <label htmlFor="bcriteria">Success criteria</label>
            <textarea id="bcriteria" value={successCriteria} onChange={(event) => setSuccessCriteria(event.target.value)} required placeholder="What outcome counts as success?" />
          </div>

          <div className="cfield">
            <label>Failure criteria</label>
            {failureCriteria.map((value, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={value} onChange={(event) => setFailureCriteria(failureCriteria.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder="What outcome counts as failure?" required />
                {failureCriteria.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setFailureCriteria(failureCriteria.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove failure criterion ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setFailureCriteria([...failureCriteria, ""])}>+ Add failure criterion</button>
          </div>

          <div className="cfield">
            <label>Scope conditions</label>
            {scopeConditions.map((value, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={value} onChange={(event) => setScopeConditions(scopeConditions.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder="Which situations this benchmark applies to" required />
                {scopeConditions.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setScopeConditions(scopeConditions.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove scope condition ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setScopeConditions([...scopeConditions, ""])}>+ Add scope condition</button>
          </div>

          <div className="cfield">
            <label>Invariants / constraints</label>
            {invariants.map((value, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={value} onChange={(event) => setInvariants(invariants.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder="What must hold true throughout" />
                {invariants.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setInvariants(invariants.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove invariant ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setInvariants([...invariants, ""])}>+ Add invariant</button>
          </div>

          <div className="cfield">
            <label htmlFor="bcontext">Context</label>
            <textarea id="bcontext" value={context} onChange={(event) => setContext(event.target.value)} placeholder="Which situations this benchmark applies to" />
          </div>

          <div className="cfield">
            <label htmlFor="bmethod">Method</label>
            <textarea id="bmethod" value={method} onChange={(event) => setMethod(event.target.value)} placeholder="Deterministic test, environment check, human verification, a combination..." />
          </div>

          <div className="cfield">
            <label htmlFor="bimpl">Implementation (if executable)</label>
            <textarea id="bimpl" value={implementation} onChange={(event) => setImplementation(event.target.value)} placeholder="Optional, how it’s actually run" />
          </div>

          <div className="cfield">
            <label htmlFor="bevidence">Supporting evidence</label>
            <textarea id="bevidence" value={evidence} onChange={(event) => setEvidence(event.target.value)} placeholder="Why this is a fair way to check the goal (optional)" />
          </div>

          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={submitting || !name.trim() || !description.trim() || !successCriteria.trim() || !hasFailureCriterion || !hasScopeCondition}>
              <span>{submitting ? "Submitting…" : "Submit as candidate"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            {result.kind === "unauthenticated" && <span className="small dim">Your session expired. <Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in again</Link>.</span>}
            {result.kind === "error" && <span className="small dim">{result.message}</span>}
          </div>
        </form>
        <p className="cform-note">A Procedure cannot redefine what success means for its own Goal. This benchmark belongs to the Goal, not to any one way of accomplishing it. If you&rsquo;ve also contributed a way here, that&rsquo;s flagged for review, not rejected.</p>
      </section>
    </>
  );
}
