"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import StateNotice from "@/components/NotConnected";
import {
  createProcedureSubmission, getProblem, getRankedProcedures,
  type Problem, type RankedProcedure, type SubmissionResult,
} from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import type { ApiState } from "@/lib/api";

export default function ContributeWayPage() {
  const { id } = useParams<{ id: string }>();
  const [problem, setProblem] = useState<ApiState<Problem>>({ kind: "loading" });
  const [candidates, setCandidates] = useState<RankedProcedure[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { getSession().then(setSession); }, []);

  const [submissionType, setSubmissionType] = useState<"new" | "improvement">("new");
  const [parentId, setParentId] = useState("");
  const [name, setName] = useState("");
  const [rationale, setRationale] = useState("");
  const [steps, setSteps] = useState<string[]>([""]);
  const [applicability, setApplicability] = useState("");
  const [constraints, setConstraints] = useState<string[]>([""]);
  const [implReqs, setImplReqs] = useState("");
  const [evidence, setEvidence] = useState("");

  const [result, setResult] = useState<ApiState<SubmissionResult>>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    getProblem(id, ac.signal).then(setProblem);
    getRankedProcedures(id, undefined, ac.signal).then((r) => { if (r.kind === "ok") setCandidates(r.data.ranked); });
    return () => ac.abort();
  }, [id]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!session) return;
    setSubmitting(true);
    setResult({ kind: "loading" });
    const r = await createProcedureSubmission({
      goal_id: id,
      submission_type: submissionType,
      name: name.trim(),
      steps: steps.map((s) => s.trim()).filter(Boolean),
      rationale: rationale.trim() || undefined,
      applicability_context: applicability.trim() ? { summary: applicability.trim() } : undefined,
      constraints: constraints.map((c) => c.trim()).filter(Boolean),
      implementation_requirements: implReqs.trim() ? { notes: implReqs.trim() } : undefined,
      supporting_evidence: evidence.trim() ? evidence.split("\n").map((l) => l.trim()).filter(Boolean) : undefined,
      parent_procedure_row_id: submissionType === "improvement" ? parentId || undefined : undefined,
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
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A WAY</b><span>/ {problem.data.title}</span></div>
          <h1 className="display">Sign in to contribute.</h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="empty" style={{ gridColumn: "1 / span 8" }}>
            <b>Contributions are attributed to a real, signed-in account.</b>
            <p>keळ never lets a submission claim someone else&rsquo;s identity — the server derives who contributed from your session, not from anything a form could say. <Link href={`/sign-in?redirect=${encodeURIComponent(`/problems/${id}/contribute/way`)}`} style={{ textDecoration: "underline" }}>Sign in</Link> to continue.</p>
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
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A WAY</b><span>/ {problem.data.title}</span></div>
          <h1 className="display">Submitted.</h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="cform-result">
            <b>Status: {r.status === "candidate" ? "Candidate" : r.status === "needs_review" ? "Needs review" : r.status}</b>
            <p>This is <em>not</em> a verified way yet — that only happens once it&rsquo;s reused and its outcomes are independently verified. {r.status === "needs_review" ? "A person will look at this before it&rsquo;s listed on the goal page." : "It's recorded and will go through review before appearing as a way to do this."}</p>
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
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONTRIBUTE A WAY</b><span>/ {problem.data.title}</span></div>
        <h1 className="display">Share a way to do this.</h1>
        <p className="lead">Submitted as candidate. It's reviewed, then listed — never marked verified just for showing up.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="stype">This is</label>
            <select id="stype" value={submissionType} onChange={(e) => setSubmissionType(e.target.value as "new" | "improvement")}>
              <option value="new">A new way</option>
              <option value="improvement">An improvement to an existing way</option>
            </select>
          </div>

          {submissionType === "improvement" && (
            <div className="cfield">
              <label htmlFor="parent">Improves</label>
              <select id="parent" value={parentId} onChange={(e) => setParentId(e.target.value)} required>
                <option value="" disabled>Choose the way this improves</option>
                {candidates.map((c) => <option key={c.procedure_row_id} value={c.procedure_row_id}>{c.display_name}</option>)}
              </select>
              <p className="hint">This creates a new, improved version — attributed to you, with the original still credited.</p>
            </div>
          )}

          <div className="cfield">
            <label htmlFor="name">Title</label>
            <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
          </div>

          <div className="cfield">
            <label htmlFor="rationale">Description &amp; rationale</label>
            <textarea id="rationale" value={rationale} onChange={(e) => setRationale(e.target.value)} required placeholder="What does this do, and why does it work?" />
          </div>

          <div className="cfield">
            <label>Steps</label>
            {steps.map((s, i) => (
              <div className="cstep-row" key={i}>
                <input type="text" value={s} onChange={(e) => setSteps(steps.map((x, j) => (j === i ? e.target.value : x)))} placeholder={`Step ${i + 1}`} />
                {steps.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setSteps(steps.filter((_, j) => j !== i))} aria-label={`Remove step ${i + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setSteps([...steps, ""])}>+ Add step</button>
          </div>

          <div className="cfield">
            <label htmlFor="applicability">Applicability / context</label>
            <textarea id="applicability" value={applicability} onChange={(e) => setApplicability(e.target.value)} placeholder="When does this apply — environment, tooling, situation?" />
          </div>

          <div className="cfield">
            <label>Constraints</label>
            {constraints.map((c, i) => (
              <div className="cstep-row" key={i}>
                <input type="text" value={c} onChange={(e) => setConstraints(constraints.map((x, j) => (j === i ? e.target.value : x)))} placeholder="What must not change, or what's unavailable" />
                {constraints.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setConstraints(constraints.filter((_, j) => j !== i))} aria-label={`Remove constraint ${i + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setConstraints([...constraints, ""])}>+ Add constraint</button>
          </div>

          <div className="cfield">
            <label htmlFor="impl">Implementation requirements</label>
            <textarea id="impl" value={implReqs} onChange={(e) => setImplReqs(e.target.value)} placeholder="Tools, systems, agents, APIs this way needs" />
          </div>

          <div className="cfield">
            <label htmlFor="evidence">Supporting evidence</label>
            <textarea id="evidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="Links or notes on where this has worked, one per line (optional)" />
          </div>

          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={submitting || !name.trim() || !rationale.trim() || steps.every((s) => !s.trim())}>
              <span>{submitting ? "Submitting…" : "Submit as candidate"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            {result.kind === "unauthenticated" && <span className="small dim">Your session expired — <Link href="/sign-in" style={{ textDecoration: "underline" }}>sign in again</Link>.</span>}
            {result.kind === "error" && <span className="small dim">{result.message}</span>}
          </div>
        </form>
        <p className="cform-note">Nothing here is treated as verified success — a submission becomes a real Procedure right away (so it can be reviewed), but only reaches the goal page and earns anything once a person accepts it, and only counts as proven once independent reuse verifies it.</p>
      </section>
    </>
  );
}
