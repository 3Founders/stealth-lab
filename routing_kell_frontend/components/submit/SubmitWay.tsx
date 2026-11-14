"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  runnable, STATUS_LABEL, type Run, type StepIn, type Submission, type SubmissionRow, type SubmissionsApi,
} from "@/lib/submissions-api";
import "./submit.css";

/**
 * Submit a Way (docs/goal_submission.md): the goal, then its steps in order, each done by a person or automated. The mode it is filed
 * under (Fully Human, Human+AI, Fully AI) is worked out from the steps and shown, never asked. After submitting, each step shows its
 * review; a rejected one shows why and can be fixed on its own. An approved Way can be run from here, pausing at the steps a person does.
 * One component for the website's /submit page and the rooms' panel (the desktop app's Rooms pane is the same page).
 */
type View = { kind: "new" } | { kind: "list" } | { kind: "one"; id: string };

const blank = (kind: StepIn["kind"] = "automated"): StepIn => ({ kind, title: "", description: "", prompt: "", locator: "" });
const DRAFT_KEY = "kell:submit-draft";

export default function SubmitWay({ api, compact = false, initial }: { api: SubmissionsApi; compact?: boolean; initial?: string }) {
  const [view, setView] = useState<View>(initial ? { kind: "one", id: initial } : { kind: "new" });
  return (
    <div className="sw" data-compact={compact || undefined}>
      <nav className="sw-tabs" aria-label="Submissions">
        <button type="button" aria-pressed={view.kind === "new"} onClick={() => setView({ kind: "new" })}>New Way</button>
        <button type="button" aria-pressed={view.kind !== "new"} onClick={() => setView({ kind: "list" })}>My submissions</button>
      </nav>
      {view.kind === "new" && <NewWay api={api} onDone={(id) => setView({ kind: "one", id })} />}
      {view.kind === "list" && <Mine api={api} onOpen={(id) => setView({ kind: "one", id })} />}
      {view.kind === "one" && <One api={api} id={view.id} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------ the form

function NewWay({ api, onDone }: { api: SubmissionsApi; onDone: (id: string) => void }) {
  const [goal, setGoal] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<StepIn[]>([blank("human"), blank()]);
  const [mode, setMode] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  // a draft survives a reload (this device only)
  useEffect(() => {
    try {
      const d = JSON.parse(window.localStorage.getItem(DRAFT_KEY) || "null");
      if (d && typeof d.goal === "string" && Array.isArray(d.steps) && d.steps.length) { setGoal(d.goal); setDescription(d.description || ""); setSteps(d.steps); }
    } catch { /* storage blocked */ }
  }, []);
  useEffect(() => {
    try { window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ goal, description, steps })); } catch { /* storage blocked */ }
  }, [goal, description, steps]);

  // The mode, from the server's own rule, a moment after the steps change. Nothing is stored.
  const usable = steps.filter((s) => (s.kind === "human" ? s.description?.trim() : s.prompt?.trim() || String(s.locator || "").trim()));
  useEffect(() => {
    if (!goal.trim() || !usable.length) { setMode(""); return; }
    const t = window.setTimeout(() => {
      void api.preview({ goal, description, steps: usable }).then((p) => setMode(p.message)).catch(() => setMode(""));
    }, 500);
    return () => window.clearTimeout(t);
  }, [api, goal, description, JSON.stringify(usable)]); // eslint-disable-line react-hooks/exhaustive-deps

  const put = (i: number, s: StepIn) => setSteps((all) => all.map((x, j) => (j === i ? s : x)));
  const move = (i: number, by: number) => setSteps((all) => {
    const j = i + by;
    if (j < 0 || j >= all.length) return all;
    const next = [...all]; [next[i], next[j]] = [next[j], next[i]]; return next;
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!goal.trim()) { setError("Say what the goal is."); return; }
    if (!usable.length) { setError("Add at least one step."); return; }
    setSending(true);
    try {
      const out = await api.submit({ goal, description, steps: usable });
      try { window.localStorage.removeItem(DRAFT_KEY); } catch { /* storage blocked */ }
      onDone(out.id);
    } catch (err) { setError((err as Error).message); }
    finally { setSending(false); }
  };

  return (
    <form className="sw-form" onSubmit={(e) => void submit(e)}>
      <label className="sw-field">
        <span>Goal</span>
        <input value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. Summarise recent ML papers on a topic" maxLength={200} />
      </label>
      <label className="sw-field">
        <span>What it is for <em>(optional)</em></span>
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="One or two sentences" />
      </label>

      <ol className="sw-steps">
        {steps.map((s, i) => (
          <li key={i} className="sw-step">
            <StepEditor step={s} n={i + 1} onChange={(x) => put(i, x)} />
            <div className="sw-step-tools">
              <button type="button" aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button type="button" aria-label={`Move step ${i + 1} down`} disabled={i === steps.length - 1} onClick={() => move(i, 1)}>↓</button>
              <button type="button" aria-label={`Remove step ${i + 1}`} disabled={steps.length === 1} onClick={() => setSteps((all) => all.filter((_, j) => j !== i))}>Remove</button>
            </div>
          </li>
        ))}
      </ol>
      <div className="sw-row">
        <button type="button" className="btn-line sw-btn" onClick={() => setSteps((all) => [...all, blank("human")])}>+ A step a person does</button>
        <button type="button" className="btn-line sw-btn" onClick={() => setSteps((all) => [...all, blank()])}>+ An automated step</button>
      </div>

      {mode && <p className="sw-mode" role="status">{mode}</p>}
      {error && <p className="sw-error" role="alert">{error}</p>}
      <div className="sw-row">
        <button type="submit" className="btn-ink sw-btn" disabled={sending}><span>{sending ? "Submitting… kell is reviewing each step" : "Submit for review"}</span></button>
      </div>
      <p className="sw-dim">Each step becomes its own reusable goal. kell's AI reviewer checks every step; approved steps go into search marked “AI approved”, and a rejected step comes back with the reason (by email too) so you fix just that one.</p>
    </form>
  );
}

function StepEditor({ step, n, onChange }: { step: StepIn; n: number; onChange: (s: StepIn) => void }) {
  const set = (k: keyof StepIn, v: string) => onChange({ ...step, [k]: v });
  const locator = typeof step.locator === "string" ? step.locator : step.locator?.value || "";
  return (
    <div className="sw-step-body">
      <div className="sw-step-head">
        <b>Step {n}</b>
        <span className="sw-seg" role="radiogroup" aria-label={`Who does step ${n}`}>
          <button type="button" role="radio" aria-checked={step.kind === "human"} onClick={() => onChange({ ...step, kind: "human" })}>A person</button>
          <button type="button" role="radio" aria-checked={step.kind === "automated"} onClick={() => onChange({ ...step, kind: "automated" })}>Automated</button>
        </span>
      </div>
      {step.kind === "human" ? (
        <textarea rows={2} aria-label={`What the person does in step ${n}`} value={step.description || ""} onChange={(e) => set("description", e.target.value)}
          placeholder="Exactly what the person does, e.g. Search 'diffusion transformers' on arxiv.org and copy the top 5 links" />
      ) : (
        <>
          <textarea rows={2} aria-label={`Prompt for step ${n}`} value={step.prompt || ""} onChange={(e) => set("prompt", e.target.value)}
            placeholder="A prompt for kell, e.g. For each link from step 1, write its method and main result in two lines" />
          <input aria-label={`Link or tool for step ${n}`} value={locator} onChange={(e) => onChange({ ...step, locator: e.target.value })}
            placeholder="Optional: a website, a file or script link (GitHub works), or a tool name" />
          {!step.prompt?.trim() && locator.trim() && (
            <input aria-label={`Title for step ${n}`} value={step.title || ""} onChange={(e) => set("title", e.target.value)}
              placeholder="What this step achieves (needed when there is no prompt)" />
          )}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------- my submissions

function Mine({ api, onOpen }: { api: SubmissionsApi; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<SubmissionRow[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void api.list().then(setRows).catch((e) => setError((e as Error).message)); }, [api]);
  if (error) return <p className="sw-error" role="alert">{error}</p>;
  if (!rows) return <p className="sw-dim">Loading…</p>;
  if (!rows.length) return <p className="sw-dim">Nothing submitted yet.</p>;
  return (
    <ul className="sw-list">
      {rows.map((r) => (
        <li key={r.id}>
          <button type="button" onClick={() => onOpen(r.id)}>
            <span className="sw-name">{r.goal_name}</span>
            <span className="sw-pill" data-status={r.status}>{STATUS_LABEL[r.status]}</span>
            <span className="sw-dim">{r.steps} step{r.steps === 1 ? "" : "s"}{r.rejected ? ` · ${r.rejected} to fix` : ""} · {new Date(r.created_at).toLocaleDateString()}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------------------------------------- one submission

function One({ api, id }: { api: SubmissionsApi; id: string }) {
  const [sub, setSub] = useState<Submission | null>(null);
  const [error, setError] = useState("");
  const [fixing, setFixing] = useState<{ order: number; step: StepIn } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setSub(await api.get(id)); setError(""); } catch (e) { setError((e as Error).message); }
  }, [api, id]);
  useEffect(() => { void load(); }, [load]);
  // still being reviewed: look again every few seconds
  useEffect(() => {
    if (sub?.status !== "reviewing") return;
    const t = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(t);
  }, [sub?.status, load]);

  const fix = async () => {
    if (!fixing) return;
    setBusy(true); setError("");
    try { setSub(await api.fix(id, fixing.order, fixing.step)); setFixing(null); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  if (error && !sub) return <p className="sw-error" role="alert">{error}</p>;
  if (!sub) return <p className="sw-dim">Loading…</p>;
  return (
    <div className="sw-one">
      <div className="sw-one-head">
        <h3>{sub.goal_name}</h3>
        <span className="sw-pill" data-status={sub.status}>{STATUS_LABEL[sub.status]}</span>
        <span className="sw-dim">{sub.mode_label}</span>
      </div>
      {sub.description && <p className="sw-dim">{sub.description}</p>}
      <ol className="sw-steps">
        {sub.steps.map((s) => (
          <li key={s.order} className="sw-step" data-status={s.status}>
            <div className="sw-step-body">
              <div className="sw-step-head">
                <b>Step {s.order}: {s.title}</b>
                <span className="sw-pill" data-status={s.status}>{STATUS_LABEL[s.status]}</span>
              </div>
              <p className="sw-dim">{s.kind === "human" ? `A person: ${s.description}` : [s.prompt, s.locator?.value].filter(Boolean).join(" · ")}</p>
              {matchNote(s) && <p className="sw-dim">{matchNote(s)}</p>}
              {s.status === "rejected" && s.reason && <p className="sw-reason"><b>Why:</b> {s.reason}</p>}
              {s.status === "rejected" && (fixing?.order === s.order ? (
                <div className="sw-fix">
                  <StepEditor step={fixing.step} n={s.order} onChange={(x) => setFixing({ order: s.order, step: x })} />
                  <div className="sw-row">
                    <button type="button" className="btn-ink sw-btn" disabled={busy} onClick={() => void fix()}><span>{busy ? "Reviewing…" : "Send the fix"}</span></button>
                    <button type="button" className="btn-line sw-btn" onClick={() => setFixing(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button type="button" className="btn-line sw-btn" onClick={() => setFixing({ order: s.order, step: {
                  kind: s.kind, title: s.title, description: s.description, prompt: s.prompt, locator: s.locator?.value || "" } })}>Fix this step</button>
              ))}
            </div>
          </li>
        ))}
      </ol>
      {error && <p className="sw-error" role="alert">{error}</p>}
      {runnable(sub) && <RunBox api={api} id={sub.id} />}
    </div>
  );
}

function matchNote(s: Submission["steps"][number]): string {
  const d = s.goal_match?.decision || "";
  const name = s.goal_match?.goal;
  if ((d === "exact" || d.startsWith("same")) && name) return `Joins kell's existing goal “${name}”.`;
  if (d.startsWith("possible_duplicate")) return "Kept as a new goal (it may be close to an existing one).";
  return "";
}

// --------------------------------------------------------------------------------------------------------------- a run

function RunBox({ api, id }: { api: SubmissionsApi; id: string }) {
  const [input, setInput] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const go = async (f: () => Promise<Run>) => {
    setBusy(true); setError("");
    try { const r = await f(); if (alive.current) setRun(r); }
    catch (e) { if (alive.current) setError((e as Error).message); }
    finally { if (alive.current) setBusy(false); }
  };

  return (
    <section className="sw-run" aria-label="Run this Way">
      <h4>Run this Way</h4>
      {!run || run.status === "done" || run.status === "failed" ? (
        <div className="sw-row">
          <input aria-label="What to run it on" value={input} onChange={(e) => setInput(e.target.value)} placeholder="What to run it on (optional), e.g. diffusion transformers" />
          <button type="button" className="btn-ink sw-btn" disabled={busy} onClick={() => void go(() => api.run(id, input))}><span>{busy ? "Running…" : run ? "Run again" : "Run"}</span></button>
        </div>
      ) : null}
      {run && (
        <div className="sw-run-out">
          {run.outputs.map((o) => (
            <div key={o.order} className="sw-out">
              <b>Step {o.order}: {o.title}</b> <span className="sw-dim">{o.actor === "human" ? "done by you" : "done by kell"}</span>
              <pre>{o.output}</pre>
            </div>
          ))}
          {run.status === "waiting_human" && run.waiting_on && (
            <div className="sw-wait">
              <p><b>Your turn, step {run.waiting_on.order}: {run.waiting_on.title}</b></p>
              {run.waiting_on.kind === "signin" && run.waiting_on.link
                ? <p>Sign in to the tool first: <a href={run.waiting_on.link} target="_blank" rel="noopener noreferrer">open the sign-in</a>, then mark it done.</p>
                : <p>{run.waiting_on.description}</p>}
              <textarea rows={2} aria-label="What you did or found" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What you did or found (passed to the next steps)" />
              <button type="button" className="btn-ink sw-btn" disabled={busy} onClick={() => void go(async () => { const r = await api.done(run.id, note); setNote(""); return r; })}><span>{busy ? "Continuing…" : "Done, continue"}</span></button>
            </div>
          )}
          {run.status === "done" && <p className="sw-mode" role="status">Done.</p>}
          {run.status === "failed" && <p className="sw-error" role="alert">The run stopped: {run.error}</p>}
        </div>
      )}
      {error && <p className="sw-error" role="alert">{error}</p>}
    </section>
  );
}
