"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  runnable, STATUS_LABEL, type RefineMode, type RefineResult, type Run, type RunRow, type StepIn, type Submission,
  type SubmissionRow, type SubmissionsApi, type Suggestion,
} from "@/lib/submissions-api";
import LocatorField from "./LocatorField";
import "./submit.css";

// The step-swap animation only exists in a browser; useLayoutEffect warns during the server render, so the server takes useEffect.
const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

/**
 * Submit a Way (docs/goal_submission.md): the goal, then its steps in order, each done by a person or automated. The mode it is filed
 * under (Fully Human, Human+AI, Fully AI) is worked out from the steps and shown, never asked. After submitting, each step shows its
 * review; a rejected one shows why and can be fixed on its own. An approved Way can be run from here, pausing at the steps a person does.
 * One component for the website's /submit page and the rooms' panel (the desktop app's Rooms pane is the same page).
 */
type View = { kind: "new" } | { kind: "list" } | { kind: "one"; id: string } | { kind: "runs" } | { kind: "run"; id: string };

/** A step in the form, with a key of its own so React can move the card itself when ↑/↓ swap two steps (and animate it). The key is
 *  local: cleanStep drops it before anything is sent. */
type DraftStep = StepIn & { id: string };

let seq = 0;
const newStepId = () => `step-${Date.now().toString(36)}-${++seq}`;
const blank = (kind: StepIn["kind"] = "automated"): DraftStep => ({ id: newStepId(), kind, title: "", description: "", prompt: "", locator: "" });
const DRAFT_KEY = "kell:submit-draft";

export default function SubmitWay({ api, compact = false, initial }: { api: SubmissionsApi; compact?: boolean; initial?: string }) {
  const [view, setView] = useState<View>(initial ? { kind: "one", id: initial } : { kind: "new" });
  return (
    <div className="sw" data-compact={compact || undefined}>
      <nav className="sw-tabs" aria-label="Submissions">
        <button type="button" aria-pressed={view.kind === "new"} onClick={() => setView({ kind: "new" })}>New Way</button>
        <button type="button" aria-pressed={view.kind === "list" || view.kind === "one"} onClick={() => setView({ kind: "list" })}>My submissions</button>
        <button type="button" aria-pressed={view.kind === "runs" || view.kind === "run"} onClick={() => setView({ kind: "runs" })}>My runs</button>
      </nav>
      {view.kind === "new" && <NewWay api={api} onDone={(id) => setView({ kind: "one", id })} />}
      {view.kind === "list" && <Mine api={api} onOpen={(id) => setView({ kind: "one", id })} />}
      {view.kind === "one" && <One api={api} id={view.id} onOpen={(id) => setView({ kind: "one", id })} />}
      {view.kind === "runs" && <Runs api={api} onOpen={(id) => setView({ kind: "run", id })} />}
      {view.kind === "run" && <OneRun api={api} id={view.id} onBack={() => setView({ kind: "runs" })} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------ the form

/** The rewrite as a person reads it: the goal, the description and every step, with the actions the caller supplies.
 *  Shared by the new-Way form and by a submitted Way being rewritten, so the two always look the same. */
function ProposalCard({ p, children }: { p: RefineResult; children: ReactNode }) {
  return (
    <div className="sw-proposal" role="region" aria-label={`Proposed ${p.mode_label} Way`}>
      <p className="sw-proposal-kicker">{p.mode_label}: {p.mode_note}</p>
      <p className="sw-proposal-goal">{p.goal}</p>
      {p.description && <p className="sw-dim">{p.description}</p>}
      <ol className="sw-proposal-steps">
        {p.steps.map((s, i) => (
          <li key={i}>
            <b>{s.title || `Step ${i + 1}`}</b>
            <span className="sw-proposal-who">{s.kind === "human" ? "a person" : "automated"}</span>
            <span>{s.kind === "human" ? s.description : s.prompt || (typeof s.locator === "string" ? s.locator : s.locator?.value)}</span>
          </li>
        ))}
      </ol>
      <div className="sw-row">{children}</div>
      <p className="sw-dim">Submitting sends the goal and every step together, exactly as reviewed here.</p>
    </div>
  );
}

/** The rewrite as its own page: the Way as it stands, what should change, the mode, and the proposal. Opened from the
 *  three buttons at the end of the form and from a submission's view, so both take the same screen. */
function RefinePage({ api, draft, startMode, onBack, children }: {
  api: SubmissionsApi;
  draft: { goal: string; description?: string; steps: StepIn[] };
  startMode: RefineMode;
  onBack: () => void;
  children: (p: RefineResult) => ReactNode;
}) {
  const [mode, setMode] = useState<RefineMode>(startMode);
  const [note, setNote] = useState("");
  const [proposal, setProposal] = useState<RefineResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ready = !!draft.goal.trim() && draft.steps.length > 0;
  const run = async () => {
    setError(""); setBusy(true);
    try { setProposal(await api.refine(draft, mode, note)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const MODES: { id: RefineMode; label: string; hint: string }[] = [
    { id: "improve", label: "Improve", hint: "The same job, said precisely: a goal that names the outcome, steps that each do one thing." },
    { id: "specialize", label: "Specialize", hint: "Narrower: one concrete case (a repository, language, format or audience), named in the goal." },
    { id: "abstract", label: "Abstract", hint: "Broader: the family of jobs this Way belongs to, with the case details taken out." },
  ];
  return (
    <div className="sw-refine-page">
      <div className="sw-refine-top">
        <button type="button" className="btn-line sw-btn" onClick={onBack}>← Back</button>
        <h3 className="sw-refine-title">Rewrite this Way</h3>
      </div>
      <div className="sw-draft">
        <b>{draft.goal || "(no goal yet)"}</b>
        {draft.description && <p className="sw-dim">{draft.description}</p>}
        <ol>
          {draft.steps.map((s, i) => (
            <li key={i}><b>{s.title || `Step ${i + 1}`}</b>{" "}
              <span className="sw-dim">{s.kind === "human" ? "a person" : "automated"}</span></li>
          ))}
        </ol>
      </div>
      <label className="sw-field">
        <span>What should change? <em>(optional)</em></span>
        <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={300}
          placeholder="e.g. make it about Python repos, or split the first step" />
      </label>
      <span className="sw-seg" role="radiogroup" aria-label="How to rewrite it">
        {MODES.map((m) => (
          <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} title={m.hint}
            onClick={() => setMode(m.id)}>{m.label}</button>
        ))}
      </span>
      <div className="sw-row">
        <button type="button" className="btn-ink sw-btn" disabled={busy || !ready} data-busy={busy || undefined}
          onClick={() => void run()}>
          <span>{busy ? "Rewriting…" : proposal ? "Rewrite again" : "Rewrite"}</span>
        </button>
      </div>
      {!ready && <p className="sw-dim">Write the goal and at least one step first.</p>}
      {error && <p className="sw-error" role="alert">{error}</p>}
      {proposal && <ProposalCard p={proposal}>{children(proposal)}</ProposalCard>}
    </div>
  );
}

function NewWay({ api, onDone }: { api: SubmissionsApi; onDone: (id: string) => void }) {
  const [goal, setGoal] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<DraftStep[]>(() => [blank("human"), blank()]);
  const [mode, setMode] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  // Improve / specialize / abstract open their own page (RefinePage): the person adds what should change there, runs the
  // rewrite, and brings the result back here (or submits it straight from that page).
  const [refineMode, setRefineMode] = useState<RefineMode | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  // The tops of the cards just before a ↑/↓ swap, by step id. After the render moves the cards, the layout effect plays each one from its
  // old place to its new one (a FLIP), so the two steps visibly trade places instead of the text changing in place.
  const pendingFlip = useRef<Map<string, number> | null>(null);

  // a draft survives a reload (this device only)
  useEffect(() => {
    try {
      const d = JSON.parse(window.localStorage.getItem(DRAFT_KEY) || "null");
      if (d && typeof d.goal === "string" && Array.isArray(d.steps) && d.steps.length) {
        setGoal(d.goal); setDescription(d.description || "");
        setSteps((d.steps as StepIn[]).map((s) => ({ ...s, id: newStepId() })));
      }
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

  const put = (i: number, s: StepIn) => setSteps((all) => all.map((x, j) => (j === i ? { ...s, id: x.id } : x)));
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= steps.length) return;
    const list = listRef.current;
    if (list) {
      const tops = new Map<string, number>();
      steps.forEach((s, k) => {
        const el = list.children[k] as HTMLElement | undefined;
        if (el) tops.set(s.id, el.offsetTop);
      });
      pendingFlip.current = tops;
    }
    setSteps((all) => { const next = [...all]; [next[i], next[j]] = [next[j], next[i]]; return next; });
  };
  // FLIP: after the swap has moved the two cards, start each at its old top and let the card's own transition carry it to the new one.
  useIsoLayoutEffect(() => {
    const tops = pendingFlip.current;
    if (!tops) return;
    pendingFlip.current = null;
    const list = listRef.current;
    if (!list) return;
    steps.forEach((s, k) => {
      const el = list.children[k] as HTMLElement | undefined;
      const before = tops.get(s.id);
      if (!el || before === undefined) return;
      const dy = before - el.offsetTop;
      if (!dy) return;
      el.style.transition = "none";
      el.style.transform = `translateY(${dy}px)`;
      void el.offsetHeight;
      el.style.transition = "";
      el.style.transform = "";
    });
  }, [steps]);

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

  const useProposal = (p: RefineResult) => {
    setGoal(p.goal); setDescription(p.description);
    setSteps(p.steps.map((s) => ({ ...s, id: newStepId() })));
  };

  // "Use this and submit": the proposal is already a valid Way (the server checked it), so the goal and its steps go
  // to review together in one action. The form is not changed on the way out; the submission view is what follows.
  const submitProposal = async (p: RefineResult) => {
    setError("");
    setSending(true);
    try {
      const out = await api.submit({ goal: p.goal, description: p.description, steps: p.steps });
      try { window.localStorage.removeItem(DRAFT_KEY); } catch { /* storage blocked */ }
      onDone(out.id);
    } catch (err) { setError((err as Error).message); }
    finally { setSending(false); }
  };

  // The rewrite has a page of its own: it shows the Way as it stands, takes what should change, runs the rewrite and
  // offers the result back to this form (or straight to review). Going Back leaves the draft exactly as it was.
  if (refineMode) {
    return (
      <RefinePage api={api} draft={{ goal, description, steps: usable }} startMode={refineMode}
        onBack={() => setRefineMode(null)}>
        {(p) => (
          <>
            <button type="button" className="btn-ink sw-btn" disabled={sending}
              onClick={() => { useProposal(p); setRefineMode(null); }}>Use this</button>
            <button type="button" className="btn-line sw-btn" disabled={sending}
              onClick={() => void submitProposal(p)}>Use this and submit</button>
          </>
        )}
      </RefinePage>
    );
  }

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

      <ol className="sw-steps" ref={listRef}>
        {steps.map((s, i) => (
          <li key={s.id} className="sw-step">
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

      {mode && <p className="sw-mode" role="status" key={mode}>{mode}</p>}
      {error && <p className="sw-error" role="alert" key={error}>{error}</p>}
      <div className="sw-row">
        <button type="submit" className="btn-ink sw-btn" disabled={sending} data-busy={sending || undefined}><span>{sending ? "Submitting… kell is reviewing each step" : "Submit for review"}</span></button>
      </div>
      {/* Three buttons at the end: each opens the rewrite page, where the person says what should change before kell
          runs. Nothing is submitted from here; the page brings the result back or files it. */}
      <div className="sw-row" aria-label="Rewrite this Way before submitting">
        <span className="sw-dim">Or rewrite the goal and steps:</span>
        <button type="button" className="btn-line sw-btn" disabled={sending} onClick={() => setRefineMode("improve")}
          title="Open a page that shows this Way and takes what should change; kell says the same job more precisely.">Improve</button>
        <button type="button" className="btn-line sw-btn" disabled={sending} onClick={() => setRefineMode("specialize")}
          title="Open a page that narrows this Way to one concrete case, named in the goal.">Specialize</button>
        <button type="button" className="btn-line sw-btn" disabled={sending} onClick={() => setRefineMode("abstract")}
          title="Open a page that widens this Way to the family of jobs it belongs to.">Abstract</button>
      </div>
      <p className="sw-dim">Each step becomes its own reusable goal. kell's AI reviewer checks every step; approved steps go into search marked “AI approved”, and a rejected step comes back with the reason so you fix just that one.</p>
    </form>
  );
}

function StepEditor({ step, n, onChange }: { step: StepIn; n: number; onChange: (s: StepIn) => void }) {
  const set = (k: keyof StepIn, v: string) => onChange({ ...step, [k]: v });
  const locator = typeof step.locator === "string" ? step.locator : step.locator?.value || "";
  // Switching who does the step carries the words across instead of leaving them behind: the two kinds write to different
  // fields (a person has a description, kell has a prompt), so the text used to look deleted when the field changed
  // (2026-11-18). The text MOVES rather than being kept in both, because the server refuses a human step that still
  // carries a prompt (submissions/intake.py:145).
  const setKind = (kind: StepIn["kind"]) => {
    if (kind === step.kind) return;
    const words = kind === "human" ? (step.prompt || "").trim() : (step.description || "").trim();
    const next: StepIn = { ...step, kind };
    if (kind === "human") {
      next.description = words || step.description || "";
      delete next.prompt;
      delete next.locator;
    } else {
      next.prompt = words || step.prompt || "";
      delete next.description;
    }
    onChange(next);
  };
  return (
    <div className="sw-step-body">
      <div className="sw-step-head">
        <b>Step {n}</b>
        <span className="sw-seg" role="radiogroup" aria-label={`Who does step ${n}`}>
          <button type="button" role="radio" aria-checked={step.kind === "human"} onClick={() => setKind("human")}>A person</button>
          <button type="button" role="radio" aria-checked={step.kind === "automated"} onClick={() => setKind("automated")}>Automated</button>
        </span>
      </div>
      {/* The step's heading, first: it is what the person is naming, and everything below is what happens under that
          name. Always editable (2026-11-18); kell fills it in from the words when it is left out, so it stays optional. */}
      <input aria-label={`Heading for step ${n}`} value={step.title || ""} onChange={(e) => set("title", e.target.value)}
        placeholder="Heading for this step (optional: kell writes one from the words below)" />
      {step.kind === "human" ? (
        <textarea rows={2} aria-label={`What the person does in step ${n}`} value={step.description || ""} onChange={(e) => set("description", e.target.value)}
          placeholder="Exactly what the person does, e.g. Search 'diffusion transformers' on arxiv.org and copy the top 5 links" />
      ) : (
        <>
          <textarea rows={2} aria-label={`Prompt for step ${n}`} value={step.prompt || ""} onChange={(e) => set("prompt", e.target.value)}
            placeholder="A prompt for kell, e.g. For each link from step 1, write its method and main result in two lines" />
          <LocatorField n={n} value={locator} onChange={(v) => onChange({ ...step, locator: v })} />
        </>
      )}
    </div>
  );
}

const LOCATOR_LABEL: Record<string, string> = { url: "Link", file: "File", script: "Script", tool: "Tool" };

/** A step's link, file, script or tool on its own line; anything web-reachable opens in a new tab. */
function LocatorLine({ locator }: { locator: { type: string; value: string } }) {
  const web = /^https?:\/\//i.test(locator.value);
  return (
    <p className="sw-loc">
      <b>{LOCATOR_LABEL[locator.type] || "Link"}:</b>{" "}
      {web ? <a href={locator.value} target="_blank" rel="noopener noreferrer">{locator.value}</a> : <code>{locator.value}</code>}
    </p>
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
            <span className="sw-pill" data-status={r.status} key={r.status}>{STATUS_LABEL[r.status]}</span>
            <span className="sw-dim">{r.steps} step{r.steps === 1 ? "" : "s"}{r.rejected ? ` · ${r.rejected} to fix` : ""} · {new Date(r.created_at).toLocaleDateString()}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------------------------------------- one submission

function One({ api, id, onOpen }: { api: SubmissionsApi; id: string; onOpen: (id: string) => void }) {
  const [sub, setSub] = useState<Submission | null>(null);
  const [error, setError] = useState("");
  const [fixing, setFixing] = useState<{ order: number; step: StepIn } | null>(null);
  const [busy, setBusy] = useState(false);
  // Which rewrite page is open, if any: the three buttons at the end set it.
  const [rewriteMode, setRewriteMode] = useState<RefineMode | null>(null);
  // Better Ways for the rejected steps, searched when this opens (never stored: a deleted Way must not linger here).
  const [better, setBetter] = useState<Record<string, Suggestion[]>>({});
  const [searched, setSearched] = useState(false);

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

  const anyRejected = !!sub?.steps.some((s) => s.status === "rejected");
  useEffect(() => {
    if (!anyRejected || searched) return;
    setSearched(true);
    void api.suggestions(id).then(setBetter).catch(() => setBetter({}));
  }, [anyRejected, searched, api, id]);

  const fix = async () => {
    if (!fixing) return;
    setBusy(true); setError("");
    try { setSub(await api.fix(id, fixing.order, fixing.step)); setFixing(null); setSearched(false); setBetter({}); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const remove = async (order: number) => {
    setBusy(true); setError("");
    try { setSub(await api.removeStep(id, order)); setSearched(false); setBetter({}); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  // A submission is a reviewed record, so the rewrite is filed as a new Way rather than written over this one.
  const submitRewrite = async (p: RefineResult) => {
    setBusy(true); setError("");
    try { const out = await api.submit({ goal: p.goal, description: p.description, steps: p.steps }); onOpen(out.id); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  if (error && !sub) return <p className="sw-error" role="alert">{error}</p>;
  if (!sub) return <p className="sw-dim">Loading…</p>;
  if (rewriteMode) {
    return (
      <RefinePage api={api} startMode={rewriteMode} onBack={() => setRewriteMode(null)}
        draft={{ goal: sub.goal_name, description: sub.description,
          steps: sub.steps.map((s) => ({ kind: s.kind, title: s.title, description: s.description, prompt: s.prompt,
            locator: s.locator ?? undefined })) }}>
        {(p) => (
          <button type="button" className="btn-ink sw-btn" disabled={busy} onClick={() => void submitRewrite(p)}>Submit as a new Way</button>
        )}
      </RefinePage>
    );
  }
  return (
    <div className="sw-one">
      <div className="sw-one-head">
        <h3>{sub.goal_name}</h3>
        <span className="sw-pill" data-status={sub.status} key={sub.status}>{STATUS_LABEL[sub.status]}</span>
        <span className="sw-dim">{sub.mode_label}</span>
      </div>
      {sub.description && <p className="sw-dim">{sub.description}</p>}
      <ol className="sw-steps">
        {sub.steps.map((s) => (
          <li key={s.order} className="sw-step" data-status={s.status}>
            <div className="sw-step-body">
              <div className="sw-step-head">
                <b>Step {s.order}: {s.title}</b>
                <span className="sw-pill" data-status={s.status} key={s.status}>{STATUS_LABEL[s.status]}</span>
              </div>
              <p className="sw-dim">{s.kind === "human" ? `A person: ${s.description}` : s.prompt}</p>
              {s.kind !== "human" && s.locator?.value && <LocatorLine locator={s.locator} />}
              {matchNote(s) && <p className="sw-dim">{matchNote(s)}</p>}
              {s.status === "rejected" && s.reason && <p className="sw-reason"><b>Why:</b> {s.reason}</p>}
              {(better[String(s.order)] ?? []).length > 0 && (
                // Searched for this look, not remembered: a Way renamed or deleted since cannot show up here.
                <div className="sw-better" aria-label={`Ways that could do step ${s.order} instead`}>
                  <p className="sw-dim">kell found {better[String(s.order)].length === 1 ? "a Way" : "Ways"} that could do this step instead:</p>
                  <ul>
                    {better[String(s.order)].map((b) => (
                      <li key={`${b.way_id || b.name}-${s.order}`}>
                        <b>{b.name}</b>
                        {b.why && <span className="sw-dim"> — {b.why}</span>}
                        {b.steps?.length ? <span className="sw-dim"> ({b.steps.slice(0, 4).join(" · ")})</span> : null}
                        <button type="button" className="btn-line sw-btn" disabled={busy}
                          title="Start this step from that Way's words; kell reviews it again"
                          onClick={() => setFixing({ order: s.order, step: {
                            kind: "automated", title: b.name, prompt: b.steps?.length ? b.steps.join("\n") : b.name } })}>
                          Change this step to it
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {s.status !== "reviewing" && (fixing?.order === s.order ? (
                <div className="sw-fix">
                  <StepEditor step={fixing.step} n={s.order} onChange={(x) => setFixing({ order: s.order, step: x })} />
                  {s.status !== "rejected" && <p className="sw-dim">kell reviews the change; once it passes, the Way gets its next version and runs use it.</p>}
                  <div className="sw-row">
                    <button type="button" className="btn-ink sw-btn" disabled={busy} data-busy={busy || undefined} onClick={() => void fix()}><span>{busy ? "Reviewing…" : "Send the fix"}</span></button>
                    <button type="button" className="btn-line sw-btn" onClick={() => setFixing(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="sw-row">
                  <button type="button" className="btn-line sw-btn" onClick={() => setFixing({ order: s.order, step: {
                    kind: s.kind, title: s.title, description: s.description, prompt: s.prompt, locator: s.locator?.value || "" } })}>
                    {s.status === "rejected" ? "Fix this step" : "Change this step"}</button>
                  {/* A step can always be taken out, except the last one: a Way with no steps is not a Way (the server
                      refuses it too, so this is a courtesy, not the rule). Removing one re-reviews the rest. */}
                  {sub.steps.length > 1 && (
                    <button type="button" className="btn-line sw-btn" disabled={busy}
                      title="Remove this step; the others are re-reviewed, because a Way that lost a step is not the Way that passed"
                      onClick={() => void remove(s.order)}>Remove this step</button>
                  )}
                </div>
              ))}
            </div>
          </li>
        ))}
      </ol>
      {error && <p className="sw-error" role="alert" key={error}>{error}</p>}
      {runnable(sub) && <RunBox api={api} id={sub.id} />}
      {/* Three buttons at the end: each opens the rewrite page, seeded with this submission, where the person says what
          should change. Submitting there files a new Way; this one stays as it is. */}
      <div className="sw-row" aria-label="Rewrite this Way">
        <span className="sw-dim">Rewrite it:</span>
        <button type="button" className="btn-line sw-btn" disabled={busy} onClick={() => setRewriteMode("improve")}
          title="Open a page that shows this Way and takes what should change; kell says the same job more precisely.">Improve</button>
        <button type="button" className="btn-line sw-btn" disabled={busy} onClick={() => setRewriteMode("specialize")}
          title="Open a page that narrows this Way to one concrete case, named in the goal.">Specialize</button>
        <button type="button" className="btn-line sw-btn" disabled={busy} onClick={() => setRewriteMode("abstract")}
          title="Open a page that widens this Way to the family of jobs it belongs to.">Abstract</button>
      </div>
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
          <button type="button" className="btn-ink sw-btn" disabled={busy} data-busy={busy || undefined} onClick={() => void go(() => api.run(id, input))}><span>{busy ? "Running…" : run ? "Run again" : "Run"}</span></button>
        </div>
      ) : null}
      {run && <RunOut run={run} note={note} setNote={setNote} busy={busy}
        onDone={() => void go(async () => { const r = await api.done(run.id, note); setNote(""); return r; })} />}
      {error && <p className="sw-error" role="alert" key={error}>{error}</p>}
    </section>
  );
}

/** A run's steps so far and, when it waits on the person, their turn. Shared by "Run this Way" and "My runs". */
function RunOut({ run, note, setNote, busy, onDone }: {
  run: Run; note: string; setNote: (v: string) => void; busy: boolean; onDone: () => void;
}) {
  return (
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
          <button type="button" className="btn-ink sw-btn" disabled={busy} data-busy={busy || undefined} onClick={onDone}><span>{busy ? "Continuing…" : "Done, continue"}</span></button>
        </div>
      )}
      {run.status === "waiting_session" && (
        <p className="sw-dim">The next step is done by the AI in the chat that started this run (the desktop app or your assistant): say “continue” there.</p>
      )}
      {run.status === "done" && <p className="sw-mode" role="status">Done.</p>}
      {run.status === "failed" && <p className="sw-error" role="alert">The run stopped: {run.error}</p>}
      {run.status === "cancelled" && <p className="sw-dim" role="status">Cancelled.</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------------------ my runs

const RUN_LABEL: Record<Run["status"], string> = {
  running: "Running", waiting_human: "Waiting for you", waiting_session: "Waiting for the AI", done: "Done", failed: "Failed",
  cancelled: "Cancelled",
};

/** Every open run of the person's, wherever it was started, so a paused one can be continued or cancelled from here. */
function Runs({ api, onOpen }: { api: SubmissionsApi; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    try { setRows(await api.runs()); setError(""); } catch (e) { setError((e as Error).message); }
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  const cancel = async (id: string) => {
    setBusy(id); setError("");
    try { await api.cancel(id); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  if (error && !rows) return <p className="sw-error" role="alert">{error}</p>;
  if (!rows) return <p className="sw-dim">Loading…</p>;
  if (!rows.length) return <p className="sw-dim">No runs in progress.</p>;
  return (
    <>
      <ul className="sw-list">
        {rows.map((r) => (
          <li key={r.run_id} className="sw-runrow">
            <button type="button" onClick={() => onOpen(r.run_id)}>
              <span className="sw-name">{r.way_name}</span>
              <span className="sw-pill" data-status={r.status}>{RUN_LABEL[r.status] || r.status}</span>
              <span className="sw-dim">Step {r.step} of {r.of}{r.step_title ? `: ${r.step_title}` : ""}{r.input ? ` · “${r.input.slice(0, 80)}”` : ""}</span>
            </button>
            <button type="button" className="btn-line sw-btn" aria-label={`Cancel the run of ${r.way_name}`} disabled={busy === r.run_id}
              onClick={() => void cancel(r.run_id)}>{busy === r.run_id ? "Cancelling…" : "Cancel"}</button>
          </li>
        ))}
      </ul>
      {error && <p className="sw-error" role="alert">{error}</p>}
    </>
  );
}

function OneRun({ api, id, onBack }: { api: SubmissionsApi; id: string; onBack: () => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (f: () => Promise<Run>) => {
    setBusy(true); setError("");
    try { setRun(await f()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => { void go(() => api.getRun(id)); }, [api, id]); // eslint-disable-line react-hooks/exhaustive-deps
  const open = run && !["done", "failed", "cancelled"].includes(run.status);
  return (
    <section className="sw-run" aria-label="This run">
      <div className="sw-row">
        <button type="button" className="btn-line sw-btn" onClick={onBack}>← My runs</button>
        {open && <button type="button" className="btn-line sw-btn" disabled={busy} onClick={() => void go(() => api.cancel(id))}>Cancel this run</button>}
      </div>
      {!run && !error && <p className="sw-dim">Loading…</p>}
      {run && <RunOut run={run} note={note} setNote={setNote} busy={busy}
        onDone={() => void go(async () => { const r = await api.done(run.id, note); setNote(""); return r; })} />}
      {error && <p className="sw-error" role="alert" key={error}>{error}</p>}
    </section>
  );
}
