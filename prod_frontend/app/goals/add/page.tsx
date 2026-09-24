"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import { createGoal, type CreateGoalResult, type Goal } from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import type { ApiState } from "@/lib/api";

export default function AddGoalPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => { getSession().then(setSession); }, []);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [rationale, setRationale] = useState("");
  const [success, setSuccess] = useState("");
  const [objective, setObjective] = useState("");
  const [constraints, setConstraints] = useState<string[]>([""]);

  const [candidates, setCandidates] = useState<Goal[] | null>(null);
  const [created, setCreated] = useState<Goal | null>(null);
  const [result, setResult] = useState<ApiState<CreateGoalResult>>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);

  async function submit(e: React.FormEvent, allowCreateAnyway = false) {
    e.preventDefault();
    if (!session) return;
    setSubmitting(true);
    setResult({ kind: "loading" });
    const r = await createGoal({
      canonical_name: title.trim(),
      description: description.trim() || undefined,
      rationale: rationale.trim(),
      objective: objective.trim() || undefined,
      expected_outcome: { summary: success.trim() },
      constraints: constraints.map((constraint) => constraint.trim()).filter(Boolean),
      allow_create_anyway: allowCreateAnyway || undefined,
    });
    setResult(r);
    setSubmitting(false);
    if (r.kind === "ok") {
      if (r.data.outcome === "near_matches") {
        setCandidates(r.data.candidates ?? []);
      } else {
        setCandidates(null);
        setCreated(r.data.goal ?? null);
      }
    }
  }

  if (session === undefined) {
    return <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }} />;
  }

  if (!session) {
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A GOAL</b></div>
          <h1 className="display"><AnimatedHeading>Sign in to contribute</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="empty" style={{ gridColumn: "1 / span 8" }}>
            <b>Contributions are attributed to a real, signed-in account.</b>
            <p>keळ never lets a submission claim someone else&rsquo;s identity. The server derives who proposed a goal from your session, not from anything a form could say. <Link href={`/sign-in?redirect=${encodeURIComponent("/goals/add")}`} style={{ textDecoration: "underline" }}>Sign in</Link> to continue.</p>
          </div>
        </section>
      </>
    );
  }

  if (result.kind === "ok" && created) {
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A GOAL</b></div>
          <h1 className="display"><AnimatedHeading>{result.data.outcome === "matched" ? "Already here" : "Added"}</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="cform-result">
            <b>{created.canonical_name}</b>
            {created.created_by && <p className="small dim">Attributed to {created.created_by}.</p>}
            {result.data.outcome === "matched" ? (
              <p>This goal already exists. Anyone can contribute a way to accomplish it, or a benchmark to check whether it&rsquo;s been accomplished.</p>
            ) : (
              <p>This goal is now live and browsable. Anyone can contribute a way to accomplish it, or a benchmark to check whether it&rsquo;s been accomplished.</p>
            )}
            <p style={{ marginTop: 16 }}><Link href={`/goals/${created.id}`} style={{ textDecoration: "underline" }}>Go to the goal</Link></p>
          </div>
        </section>
      </>
    );
  }

  if (candidates) {
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A GOAL</b></div>
          <h1 className="display"><AnimatedHeading>Close matches found</AnimatedHeading></h1>
          <p className="lead">These already look like what you&rsquo;re describing. Open one if it&rsquo;s the same goal, or confirm below to create a new one anyway.</p>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
          {candidates.length > 0 && (
            <ul className="list" style={{ gridColumn: "1 / span 12" }} aria-label="Near matches">
              {candidates.map((candidate, index) => (
                <li key={candidate.id}>
                  <Link href={`/goals/${candidate.id}`}>
                    <span className="n">{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <h3>{candidate.canonical_name}</h3>
                      {candidate.description && <p className="desc">{candidate.description}</p>}
                    </div>
                    <span className="caption dim" aria-hidden="true">→</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <div className="cform-submit" style={{ gridColumn: "1 / span 12" }}>
            <button className="btn-ink" type="button" disabled={submitting} onClick={(event) => submit(event, true)}>
              <span>{submitting ? "Adding…" : "None of these — create anyway"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            <button type="button" className="small dim" style={{ background: "none", border: "none", textDecoration: "underline", cursor: "pointer" }} onClick={() => setCandidates(null)}>
              Change what I typed
            </button>
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A GOAL</b></div>
        <h1 className="display"><AnimatedHeading>Add something worth accomplishing</AnimatedHeading></h1>
        <p className="lead">A Goal is described clearly enough that a way to accomplish it can be found, contributed, and checked.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="title">Title</label>
            <input id="title" type="text" value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={500} placeholder="e.g. Deploy a service to staging" />
          </div>

          <div className="cfield">
            <label htmlFor="description">Description</label>
            <textarea id="description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What is this, and why does it matter?" />
          </div>

          <div className="cfield">
            <label htmlFor="goal-rationale">Rationale</label>
            <textarea id="goal-rationale" value={rationale} onChange={(event) => setRationale(event.target.value)} required placeholder="Why is this worth accomplishing?" />
          </div>

          <div className="cfield">
            <label htmlFor="goal-success">Success</label>
            <textarea id="goal-success" value={success} onChange={(event) => setSuccess(event.target.value)} required placeholder="What observable outcome means this goal is accomplished?" />
          </div>

          <div className="cfield">
            <label htmlFor="objective">Objective</label>
            <textarea id="objective" value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="What outcome counts as this being accomplished?" />
          </div>

          <div className="cfield">
            <label>Constraints</label>
            {constraints.map((constraint, index) => (
              <div className="cstep-row" key={index}>
                <input type="text" value={constraint} onChange={(event) => setConstraints(constraints.map((value, valueIndex) => valueIndex === index ? event.target.value : value))} placeholder="Something any way to accomplish this must respect" />
                {constraints.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setConstraints(constraints.filter((_, valueIndex) => valueIndex !== index))} aria-label={`Remove constraint ${index + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setConstraints([...constraints, ""])}>+ Add constraint</button>
          </div>

          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={submitting || !title.trim() || !rationale.trim() || !success.trim()}>
              <span>{submitting ? "Checking…" : "Add goal"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            {result.kind === "unauthenticated" && <span className="small dim">Your session expired. <Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in again</Link>.</span>}
            {result.kind === "error" && <span className="small dim">{result.message}</span>}
          </div>
        </form>
        <p className="cform-note">Goals are public as soon as they&rsquo;re added. Make sure the title is specific enough that someone else searching for the same goal would recognize it, rather than accidentally creating a near-duplicate.</p>
      </section>
    </>
  );
}
