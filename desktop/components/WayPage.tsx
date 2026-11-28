"use client"
// One Way, as its own screen in the desktop (/way?id=…): the same page the website serves, built on the same
// object (`GET /v1/goals/submissions/{id}`) and the same order, so a Way looks the same wherever it is read.
//
// The transport is the only difference: there it carries the website session, here `callWebRoute` reads the
// Rooms sign-in from the app's own loopback (lib/mcp.ts) — the coding token is refused by the website API on
// purpose, which is what made "My submissions" answer 401 (2026-11-19).
//
// Two rules, enforced here rather than left to a style guide (docs/KELL.md §4):
//   1. Never a bare percentage: <Rate/> draws a rate only with its n AND its Wilson interval, and "not
//      measured" otherwise — never 0% and never 100%.
//   2. The version never hides. A label alone can lie about what it points at, so it sits beside the name.
//
// HTML cards on one CSS rail, not a graph library: a Way has a handful of steps, so SVG/canvas physics would
// cost more than it draws — the same judgment ProcedureGraph already records.
import { useEffect, useState } from "react"
import SubmitWayPanel from "@/components/SubmitWayPanel"

import { submissionsApi } from "@/lib/submissions-client"
import "./submitway.css"

type Step = {
  order: number; kind: "human" | "automated"; title: string; description: string; prompt: string
  locator: { type: string; value: string } | null
  status: "reviewing" | "ai_approved" | "approved" | "rejected"; reason: string; revision: number
}

type Submission = {
  id: string; goal_name: string; description: string; mode: string; mode_label: string
  status: "reviewing" | "needs_changes" | "ai_approved" | "approved" | "withdrawn"
  parent_procedure_id: string | null; steps: Step[]; created_at?: string; message?: string
}

type Load =
  | { kind: "loading" }
  | { kind: "signed-out"; message: string }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ok"; way: Submission }

const STATUS_COPY: Record<string, { label: string; tone: string }> = {
  approved: { label: "Approved", tone: "good" },
  ai_approved: { label: "AI approved — not yet human-reviewed", tone: "warn" },
  reviewing: { label: "In review", tone: "quiet" },
  needs_changes: { label: "Needs changes", tone: "bad" },
  withdrawn: { label: "Withdrawn", tone: "quiet" },
}

const short = (id: string) => id.slice(0, 8)

/** A rate with its interval, or an honest "not measured". Never a bare percentage. */
function Rate({ successes, total }: { successes: number | null; total: number | null }) {
  if (successes == null || total == null || total === 0) return <span className="dkw-muted">not measured</span>
  const p = successes / total
  const z = 1.96 // Wilson 95%, the same estimator the backend ranks on (BEST_VERIFIED_FLOOR)
  const denom = 1 + (z * z) / total
  const centre = (p + (z * z) / (2 * total)) / denom
  const half = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / denom
  const lo = Math.max(0, centre - half), hi = Math.min(1, centre + half)
  return (
    <span className="dkw-rate">
      <b>{(p * 100).toFixed(1)}%</b>
      <span className="dkw-muted"> ({successes}/{total}, 95% CI {(lo * 100).toFixed(0)}–{(hi * 100).toFixed(0)}%)</span>
    </span>
  )
}

export default function WayPage({ id, onBack }: { id: string; onBack?: () => void }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" })
  const [improving, setImproving] = useState(false)
  const [runInput, setRunInput] = useState("")
  const [starting, setStarting] = useState(false)
  const [runErr, setRunErr] = useState<string | null>(null)

  /** Start an execution of this Way on this computer, then open it in the main window. */
  async function runHere() {
    if (load.kind !== "ok") return
    setStarting(true); setRunErr(null)
    try {
      const r = await fetch("/api/executions", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "start", submissionId: load.way.id, title: load.way.goal_name, input: runInput }) })
      const j = await r.json().catch(() => ({}))
      if (!r.ok || !j?.execution?.id) throw new Error(String(j?.error || `the app answered ${r.status}`))
      window.location.href = `/?exec=${encodeURIComponent(j.execution.id)}`
    } catch (e) { setRunErr(String((e as Error)?.message || e)); setStarting(false) }
  }

  useEffect(() => {
    let live = true
    setImproving(false)
    // lib/submissions-client is the browser seam: it posts to app/api/submissions, which does the signed-in call on
    // the server. Importing lib/submissions-api directly fails the build (lib/mcp.ts reaches for node:fs).
    submissionsApi.get(id).then((way) => {
      if (!live) return
      if (!way?.id) return setLoad({ kind: "missing" })
      setLoad({ kind: "ok", way: way as Submission })
    }).catch((e: Error & { status?: number }) => {
      if (!live) return
      const message = e?.message || "Could not reach the server."
      // The API's own message is written for a person ("Sign in to kell (Rooms)…"), so it is what gets shown.
      if (/sign in/i.test(message)) return setLoad({ kind: "signed-out", message })
      if (/no submission|not found|404/i.test(message)) return setLoad({ kind: "missing" })
      setLoad({ kind: "error", message })
    })
    return () => { live = false }
  }, [id])

  if (load.kind !== "ok") {
    return (
      <section className="dkw">
        <nav className="dkw-bar" aria-label="Way">
          {onBack && <button type="button" className="dkw-back" onClick={onBack}>← Back to the chat</button>}
          <span className="dkw-bar-label">Way</span>
        </nav>
        <p className="dkw-muted" role={load.kind === "loading" ? "status" : undefined}>
          {load.kind === "loading" && "Loading the Way…"}
          {load.kind === "signed-out" && load.message}
          {load.kind === "missing" && "No Way with that id — it may have been withdrawn, or the link may be from another account."}
          {load.kind === "error" && load.message}
        </p>
      </section>
    )
  }

  const { way } = load
  const status = STATUS_COPY[way.status] ?? { label: way.status, tone: "quiet" }
  const steps = [...(way.steps || [])].sort((a, b) => a.order - b.order)
  const automated = steps.filter((s) => s.kind === "automated").length
  const human = steps.length - automated
  const rejected = steps.filter((s) => s.status === "rejected").length
  const runnable = way.status === "approved" || way.status === "ai_approved"
  const version = way.parent_procedure_id ? `@${short(way.parent_procedure_id)}` : ""

  return (
    <section className="dkw" aria-label={`Way: ${way.goal_name}`}>
      <nav className="dkw-bar" aria-label="Way">
        {onBack && <button type="button" className="dkw-back" onClick={onBack}>← Back to the chat</button>}
        <span className="dkw-bar-label">Way</span>
        {runnable && (
          <button type="button" className="dkw-chip dkw-run" onClick={() => setImproving(true)}>
            Propose an improvement
          </button>
        )}
      </nav>

      {/* 1. Identity — name, version, Goal inline. */}
      <header className="dkw-id">
        <h1>
          {way.goal_name}
          {version && <span className="dkw-version">{version}</span>}
        </h1>
        <p className="dkw-goal"><span className="dkw-k">Goal</span>{way.goal_name}</p>
        <div className="dkw-meta">
          <span className="dkw-badge" data-tone={status.tone}>{status.label}</span>
          <span className="dkw-chip">{way.mode_label || way.mode}</span>
          <span className="dkw-chip">{steps.length} step{steps.length === 1 ? "" : "s"} · {automated} automated · {human} human</span>
          {way.created_at && <span className="dkw-chip">created {new Date(way.created_at).toLocaleDateString()}</span>}
          <span className="dkw-chip dkw-mono" title="Quote this id in a report">{short(way.id)}</span>
        </div>
      </header>

      {/* 2. Evidence — above the description, on purpose. */}
      <section className="dkw-card dkw-evidence">
        <h2>Evidence</h2>
        <div className="dkw-grid3">
          <div>
            <span className="dkw-k">Verified runs</span>
            <p><Rate successes={null} total={null} /></p>
            <p className="dkw-small dkw-muted">A run counts once it reaches a terminal outcome against this exact version. Failures count too — a Way whose failures vanish is a Way whose score rises while its quality does not.</p>
          </div>
          <div>
            <span className="dkw-k">Review</span>
            <p>
              {rejected === 0
                ? <>Every step cleared kell&apos;s reviewer{way.status === "approved" ? " and a person approved it." : "."}</>
                : <><b>{rejected} of {steps.length}</b> step{rejected === 1 ? " was" : "s were"} rejected and need changes.</>}
            </p>
            <p className="dkw-small dkw-muted">
              {way.status === "ai_approved"
                ? "AI approval is not human approval. A Way an autonomous runner may use needs both."
                : "Review checks each step on its own and again against the Goal as a whole."}
            </p>
          </div>
          <div>
            <span className="dkw-k">Independent replication</span>
            <p className="dkw-muted">None recorded.</p>
            <p className="dkw-small dkw-muted">A replication is a run by someone other than the author. Until one exists, this Way is a single source.</p>
          </div>
        </div>
      </section>

      {/* 3. What it does, and when it applies. */}
      <section className="dkw-card">
        <h2>What it does</h2>
        <p>{way.description || way.goal_name}</p>
        {steps.length > 0 && (
          <p className="dkw-small dkw-muted">Applies when the {steps.length}-step sequence below can start: every human step has someone to do it, and every automated step names something that exists.</p>
        )}
      </section>

      {/* 4. The decomposition — versions on the nodes, one atom per step. */}
      <section className="dkw-card">
        <h2>The decomposition</h2>
        <ol className="dkw-steps">
          {steps.map((s) => (
            <li key={s.order} className="dkw-step" data-kind={s.kind} data-status={s.status}>
              <span className="dkw-dot" aria-hidden="true" />
              <div className="dkw-step-body">
                <div className="dkw-step-head">
                  <span className="dkw-order">{s.order}</span>
                  <b>{s.title || `Step ${s.order}`}</b>
                  <i className="dkw-kind">{s.kind === "human" ? "person" : "automated"}</i>
                </div>
                {s.description && <p>{s.description}</p>}
                {s.prompt && <p className="dkw-prompt"><span className="dkw-k">does</span>{s.prompt}</p>}
                {s.locator?.value && <p className="dkw-loc dkw-mono" title={s.locator.value}><span className="dkw-k">{s.locator.type}</span>{s.locator.value}</p>}
                {s.status === "rejected" && s.reason && <p className="dkw-reject"><span className="dkw-k">rejected</span>{s.reason}</p>}
                {s.revision > 0 && <p className="dkw-small dkw-muted">revised {s.revision} time{s.revision === 1 ? "" : "s"}</p>}
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* 5. Version history. */}
      <section className="dkw-card">
        <h2>Version history</h2>
        <ul className="dkw-hist">
          <li><span className="dkw-chip">{version || "current"}</span><span>{status.label}</span></li>
        </ul>
        <p className="dkw-small dkw-muted">A change that breaks a consumer of this Way is marked <b>Breaking</b> and says what breaks.</p>
      </section>

      {/* 6a. Run it here: an execution, on this computer (lib/executions.ts). Its steps open in the main window under
          Executions, as pages, the same way the website shows a run; this app's engine does the AI steps. */}
      <section className="dkw-card">
        <h2>Run this Way</h2>
        {runnable ? (
          <form className="dkw-run" onSubmit={(e) => { e.preventDefault(); void runHere() }}>
            <label htmlFor="dkw-run-input" className="dkw-small dkw-muted">What it is for (the task, in your words)</label>
            <textarea id="dkw-run-input" value={runInput} onChange={(e) => setRunInput(e.target.value)} rows={3}
              placeholder="e.g. a 12-minute conference talk from my paper on sparse routing" />
            <button type="submit" className="dkw-cta" disabled={starting}>{starting ? "Starting…" : "Run on this computer"}</button>
            {runErr && <p className="dkw-small" role="alert">{runErr}</p>}
            <p className="dkw-small dkw-muted">The AI steps run here, in a folder of their own under Documents/kell/Runs; a step that needs you waits for your answer. The run and its files are kept with kell, as on the website.</p>
          </form>
        ) : <p className="dkw-muted">This Way is {status.label.toLowerCase()}, so it does not run yet.</p>}
      </section>

      {/* 6. Composition and dependents. */}
      <section className="dkw-card">
        <h2>Composition</h2>
        {way.parent_procedure_id
          ? <p>Backed by procedure <span className="dkw-mono">{short(way.parent_procedure_id)}</span>. This Way can be used as a step inside another Way; the reference is pinned to this exact version, so a later revision never changes what an existing composition runs.</p>
          : <p className="dkw-muted">Not yet bound to a procedure, so it cannot be used as a step inside another Way.</p>}
      </section>

      {/* 7. Provenance, and how to run it yourself. */}
      <section className="dkw-card">
        <h2>Provenance — run it yourself</h2>
        <p>A badge nobody can check is worth nothing. The record above is the same object this page read; re-read it, or run the Way against your own input.</p>
        <pre className="dkw-code"><code>{`curl -s <kell>/v1/goals/submissions/${short(way.id)} \\\n  -H "authorization: Bearer $KELL_TOKEN"`}</code></pre>
        {runnable
          ? <button type="button" className="dkw-cta" onClick={() => setImproving(true)}>Run or improve this Way</button>
          : <p className="dkw-muted">This Way is {status.label.toLowerCase()}, so it does not run yet.</p>}
      </section>

      {way.message && <p className="dkw-small dkw-muted">Note from kell: {way.message}</p>}

      {/* The improvement door: the panel, seeded on this Way — not a comment box. */}
      {improving && (
        <section className="dkw-card" aria-label="Improve this Way">
          <h2>Improve this Way</h2>
          <p className="dkw-small dkw-muted">Anchor the change to a step, say what would convince you it is better, and submit a new version. The old version keeps its evidence.</p>
          <SubmitWayPanel api={submissionsApi} compact initial={way.id} />
        </section>
      )}
    </section>
  )
}


