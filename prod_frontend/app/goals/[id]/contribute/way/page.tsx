"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import {
  createProcedureSubmission, getGoal, getRankedProcedures,
  type Goal, type RankedProcedure, type Row, type SubmissionResult,
} from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import type { ApiState } from "@/lib/api";

type Precondition = { subject: string; predicate: string; value: string };

export default function ContributeWayPage() {
  const { id } = useParams<{ id: string }>();
  const [goal, setGoal] = useState<ApiState<Goal>>({ kind: "loading" });
  const [candidates, setCandidates] = useState<RankedProcedure[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { getSession().then(setSession); }, []);

  const [submissionType, setSubmissionType] = useState<"new" | "improvement">("new");
  const [parentId, setParentId] = useState("");
  const [name, setName] = useState("");
  const [rationale, setRationale] = useState("");
  const [steps, setSteps] = useState<string[]>([""]);
  const [preconditions, setPreconditions] = useState<Precondition[]>([{ subject: "", predicate: "", value: "" }]);
  const [expectedOutcome, setExpectedOutcome] = useState("");
  const [applicability, setApplicability] = useState("");
  const [constraints, setConstraints] = useState<string[]>([""]);
  const [implReqs, setImplReqs] = useState("");
  const [evidence, setEvidence] = useState("");

  const [result, setResult] = useState<ApiState<SubmissionResult>>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    getGoal(id, ac.signal).then(setGoal);
    getRankedProcedures(id, undefined, ac.signal).then((response) => { if (response.kind === "ok") setCandidates(response.data.ranked); });
    return () => ac.abort();
  }, [id]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!session) return;
    setSubmitting(true);
    setResult({ kind: "loading" });
    const structuredPreconditions: Row[] = preconditions
      .filter((item) => item.subject.trim() && item.predicate.trim() && item.value.trim())
      .map((item) => ({ subject: item.subject.trim(), predicate: item.predicate.trim(), value: item.value.trim() }));
    const r = await createProcedureSubmission({
      goal_id: id,
      submission_type: submissionType,
      name: name.trim(),
      steps: steps.map((step) => step.trim()).filter(Boolean),
      rationale: rationale.trim(),
      preconditions: structuredPreconditions,
      expected_outcome: { summary: expectedOutcome.trim() },
      applicability_context: applicability.trim() ? { summary: applicability.trim() } : undefined,
      constraints: constraints.map((constraint) => constraint.trim()).filter(Boolean),
      implementation_requirements: implReqs.trim() ? { notes: implReqs.trim() } : undefined,
      supporting_evidence: evidence.trim() ? evidence.split("\n").map((line) => line.trim()).filter(Boolean) : undefined,
      parent_procedure_row_id: submissionType === "improvement" ? parentId || undefined : undefined,
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
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A WAY</b><span>/ {goal.data.canonical_name}</span></div>
          <h1 className="display"><AnimatedHeading>Sign in to contribute</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="empty" style={{ gridColumn: "1 / span 8" }}>
            <b>Contributions are attributed to a real, signed-in account.</b>
            <p>keळ never lets a submission claim someone else&rsquo;s identity. The server derives who contributed from your session, not from anything a form could say. <Link href={`/sign-in?redirect=${encodeURIComponent(`/goals/${id}/contribute/way`)}`} style={{ textDecoration: "underline" }}>Sign in</Link> to continue.</p>
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
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A WAY</b><span>/ {goal.data.canonical_name}</span></div>
          <h1 className="display"><AnimatedHeading>Submitted</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="cform-result">
            <b>Status: {submission.status === "candidate" ? "Candidate" : submission.status === "needs_review" ? "Needs review" : submission.status}</b>
            {(submission.created_by || submission.submitted_by) && <p className="small dim">Attributed to {submission.created_by || submission.submitted_by}.</p>}
            <p>This is <em>not</em> a verified way yet. That only happens once it&rsquo;s reused and its outcomes are independently verified. {submission.status === "needs_review" ? "A person will look at this before it&rsquo;s listed on the goal page." : "It&rsquo;s recorded and will go through review before appearing as a way to do this."}</p>
            {submission.status_reason && <p className="small dim">Automated review noted: {submission.status_reason}</p>}
            <p style={{ marginTop: 16 }}><Link href={`/goals/${id}`} style={{ textDecoration: "underline" }}>Back to the goal</Link></p>
          </div>
        </section>
      </>
    );
  }

  const hasCompletePrecondition = preconditions.some((item) => item.subject.trim() && item.predicate.trim() && item.value.trim());

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A WAY</b><span>/ {goal.data.canonical_name}</span></div>
        <h1 className="display"><AnimatedHeading>Share a way to do this</AnimatedHeading></h1>
        <p className="lead">Submitted as candidate. It&rsquo;s reviewed, then listed, never marked verified just for showing up.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="stype">This is</label>
            <select id="stype" value={submissionType} onChange={(event) => setSubmissionType(event.target.value as "new" | "improvement")}>
              <option value="new">A new way</option>
              <option value="improvement">An improvement to an existing way</option>
            </select>
          </div>

          {submissionType === "improvement" && (
            <div className="cfield">
              <label htmlFor="parent">Improves</label>
              <select id="parent" value={parentId} onChange={(event) => setParentId(event.target.value)} required>
                <option value="" disabled>Choose the way this improves</option>
                {candidates.map((candidate) => <option key={candidate.procedure_row_id} value={candidate.procedure_row_id}>{candidate.display_name || candidate.name}</option>)}
              </select>
              <p className="hint">This creates a new, improved version, attributed to you, with the original still credited.</p>
            </div>
          )}

          <div className="cfield">
            <label htmlFor="name">Title</label>
            <input id="name" type="text" value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} />
          </div>

          <div className="cfield">
            <label htmlFor="rationale">Description &amp; rationale</label>
            <textarea id="rationale" value={rationale} onChange={(event) => setRationale(event.target.value)} required placeholder="What does this do, and why does it work?" />
          </div>

          <div className="cfield">
            <label>Steps</label>
            {steps.map((step, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={step} onChange={(event) => setSteps(steps.map((value, valueIndex) => valueIndex === index ? event.target.value : value))} placeholder={`Step ${index + 1}`} />
                {steps.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setSteps(steps.filter((_, valueIndex) => valueIndex !== index))} aria-label={`Remove step ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setSteps([...steps, ""])}>+ Add step</button>
          </div>

          <div className="cfield">
            <label>Preconditions</label>
            {preconditions.map((precondition, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={precondition.subject} onChange={(event) => setPreconditions(preconditions.map((value, valueIndex) => valueIndex === index ? { ...value, subject: event.target.value } : value))} placeholder="Subject" aria-label={`Precondition ${index + 1} subject`} required />
                <input type="text" value={precondition.predicate} onChange={(event) => setPreconditions(preconditions.map((value, valueIndex) => valueIndex === index ? { ...value, predicate: event.target.value } : value))} placeholder="Predicate" aria-label={`Precondition ${index + 1} predicate`} required />
                <input type="text" value={precondition.value} onChange={(event) => setPreconditions(preconditions.map((value, valueIndex) => valueIndex === index ? { ...value, value: event.target.value } : value))} placeholder="Value" aria-label={`Precondition ${index + 1} value`} required />
                {preconditions.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setPreconditions(preconditions.filter((_, valueIndex) => valueIndex !== index))} aria-label={`Remove precondition ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setPreconditions([...preconditions, { subject: "", predicate: "", value: "" }])}>+ Add precondition</button>
          </div>

          <div className="cfield">
            <label htmlFor="expected-outcome">Expected outcome</label>
            <textarea id="expected-outcome" value={expectedOutcome} onChange={(event) => setExpectedOutcome(event.target.value)} required placeholder="What should be observably true after this way succeeds?" />
          </div>

          <div className="cfield">
            <label htmlFor="applicability">Applicability / context</label>
            <textarea id="applicability" value={applicability} onChange={(event) => setApplicability(event.target.value)} placeholder="When does this apply (environment, tooling, situation)?" />
          </div>

          <div className="cfield">
            <label>Constraints</label>
            {constraints.map((constraint, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={constraint} onChange={(event) => setConstraints(constraints.map((value, valueIndex) => valueIndex === index ? event.target.value : value))} placeholder="What must not change, or what's unavailable" />
                {constraints.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setConstraints(constraints.filter((_, valueIndex) => valueIndex !== index))} aria-label={`Remove constraint ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setConstraints([...constraints, ""])}>+ Add constraint</button>
          </div>

          <div className="cfield">
            <label htmlFor="impl">Implementation requirements</label>
            <textarea id="impl" value={implReqs} onChange={(event) => setImplReqs(event.target.value)} placeholder="Tools, systems, agents, APIs this way needs" />
          </div>

          <div className="cfield">
            <label htmlFor="evidence">Supporting evidence</label>
            <textarea id="evidence" value={evidence} onChange={(event) => setEvidence(event.target.value)} placeholder="Links or notes on where this has worked, one per line (optional)" />
          </div>

          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={submitting || !name.trim() || !rationale.trim() || !hasCompletePrecondition || !expectedOutcome.trim() || steps.every((step) => !step.trim())}>
              <span>{submitting ? "Submitting…" : "Submit as candidate"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            {result.kind === "unauthenticated" && <span className="small dim">Your session expired. <Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in again</Link>.</span>}
            {result.kind === "error" && <span className="small dim">{result.message}</span>}
          </div>
        </form>
        <p className="cform-note">Nothing here is treated as verified success. A submission becomes a real Procedure right away (so it can be reviewed), but only reaches the goal page and earns anything once a person accepts it, and only counts as proven once independent reuse verifies it.</p>
      </section>
    </>
  );
}
