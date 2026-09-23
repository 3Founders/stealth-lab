"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import { createProblem, type Problem } from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import type { ApiState } from "@/lib/api";

export default function AddProblemPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => { getSession().then(setSession); }, []);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [objective, setObjective] = useState("");
  const [constraints, setConstraints] = useState<string[]>([""]);

  const [result, setResult] = useState<ApiState<Problem>>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!session) return;
    setSubmitting(true);
    setResult({ kind: "loading" });
    const r = await createProblem({
      title: title.trim(),
      description: description.trim() || undefined,
      objective: objective.trim() || undefined,
      constraints: constraints.map((c) => c.trim()).filter(Boolean),
    });
    setResult(r);
    setSubmitting(false);
  }

  if (session === undefined) {
    return <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }} />;
  }

  if (!session) {
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A PROBLEM</b></div>
          <h1 className="display"><AnimatedHeading>Sign in to contribute</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="empty" style={{ gridColumn: "1 / span 8" }}>
            <b>Contributions are attributed to a real, signed-in account.</b>
            <p>keळ never lets a submission claim someone else&rsquo;s identity. The server derives who proposed a problem from your session, not from anything a form could say. <Link href={`/sign-in?redirect=${encodeURIComponent("/problems/add")}`} style={{ textDecoration: "underline" }}>Sign in</Link> to continue.</p>
          </div>
        </section>
      </>
    );
  }

  if (result.kind === "ok") {
    const p = result.data;
    return (
      <>
        <section className="page-hero frame grid">
          <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A PROBLEM</b></div>
          <h1 className="display"><AnimatedHeading>Added</AnimatedHeading></h1>
        </section>
        <section className="frame grid" style={{ paddingBottom: 120 }}>
          <div className="cform-result">
            <b>{p.title}</b>
            <p>This problem is now live and browsable. Anyone can contribute a way to accomplish it, or a benchmark to check whether it&rsquo;s been accomplished.</p>
            <p style={{ marginTop: 16 }}><Link href={`/problems/${p.id}`} style={{ textDecoration: "underline" }}>Go to the problem</Link></p>
          </div>
        </section>
      </>
    );
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ADD A PROBLEM</b></div>
        <h1 className="display"><AnimatedHeading>Add something worth accomplishing</AnimatedHeading></h1>
        <p className="lead">A Problem is a goal, described clearly enough that a way to accomplish it can be found, contributed, and checked.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 28 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="title">Title</label>
            <input id="title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={500} placeholder="e.g. Deploy a service to staging" />
          </div>

          <div className="cfield">
            <label htmlFor="description">Description</label>
            <textarea id="description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this, and why does it matter?" />
          </div>

          <div className="cfield">
            <label htmlFor="objective">Objective</label>
            <textarea id="objective" value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="What outcome counts as this being accomplished?" />
          </div>

          <div className="cfield">
            <label>Constraints</label>
            {constraints.map((c, i) => (
              <div className="cstep-row" key={i}>
                <input type="text" value={c} onChange={(e) => setConstraints(constraints.map((x, j) => (j === i ? e.target.value : x)))} placeholder="Something any way to accomplish this must respect" />
                {constraints.length > 1 && (
                  <button type="button" className="cstep-remove" onClick={() => setConstraints(constraints.filter((_, j) => j !== i))} aria-label={`Remove constraint ${i + 1}`}>×</button>
                )}
              </div>
            ))}
            <button type="button" className="cstep-add" onClick={() => setConstraints([...constraints, ""])}>+ Add constraint</button>
          </div>

          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={submitting || !title.trim()}>
              <span>{submitting ? "Adding…" : "Add problem"}</span><span className="sq" aria-hidden="true">→</span>
            </button>
            {result.kind === "unauthenticated" && <span className="small dim">Your session expired. <Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in again</Link>.</span>}
            {result.kind === "error" && <span className="small dim">{result.message}</span>}
          </div>
        </form>
        <p className="cform-note">Problems are public as soon as they&rsquo;re added. Make sure the title is specific enough that someone else searching for the same goal would recognize it, rather than accidentally creating a near-duplicate.</p>
      </section>
    </>
  );
}
