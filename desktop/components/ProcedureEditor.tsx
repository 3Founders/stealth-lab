"use client"
// The Way's record as an editor: metadata, why it fits, and every step with its title and note, editable before the
// run. The same component is the right panel's Procedure section and the card's click-to-open sheet, so both always
// agree. Two actions: add a step, and use the Way (the per-step x removes one).
//
// A step may also name a model (the ... beside it opens a search over this machine's catalog). That is optional: with
// nothing chosen the step runs on the Way's own model, which is the default (2026-11-17).
import { useState } from "react"
import type { DelegationStep, WayOption } from "@/lib/plan"

export default function ProcedureEditor({ option, steps, catalog = [], onEdit, onAdd, onRemove, onUse, useTitle, onAsk }: {
  option: WayOption
  steps: DelegationStep[]
  /** Every model this machine may run; the per-step picker searches it. Empty means no list yet. */
  catalog?: string[]
  onEdit: (i: number, patch: Partial<DelegationStep>) => void
  onAdd: () => void
  onRemove: (i: number) => void
  /** Absent while nothing is waiting to run: the Use button stays visible, disabled, so the editor keeps its two actions. */
  onUse?: () => void
  useTitle?: string
  /** Ask kell to rewrite the steps from a sentence (the canvas's "Change it" pattern). */
  onAsk?: (text: string) => Promise<void>
}) {
  const [pick, setPick] = useState<number | null>(null)
  const [q, setQ] = useState("")
  const [ask, setAsk] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const choose = (i: number, model: string) => {
    onEdit(i, { model: model || undefined })
    setPick(null)
    setQ("")
  }
  const needle = q.trim().toLowerCase()
  const shown = catalog.filter((m) => m.toLowerCase().includes(needle)).slice(0, 12)

  return (
    <div className="dk-edit">
      <dl className="dk-proc-dl">
        <dt>Way</dt><dd>{option.id}</dd>
        {option.goal?.name && <><dt>Goal</dt><dd>{option.goal.name}{option.goal.id ? ` · ${option.goal.id}` : ""}</dd></>}
        <dt>Approval</dt><dd>{option.approval.replace(/_/g, " ")}</dd>
        {option.review && <><dt>Review</dt><dd>{option.review.replace(/_/g, " ")}</dd></>}
        {option.tool && <><dt>Tool</dt><dd>{option.tool.name}{option.tool.server ? ` · ${option.tool.server}` : ""}</dd></>}
        <dt>Steps</dt><dd>{steps.length}</dd>
      </dl>
      {option.why && <p className="dk-proc-why">{option.why}</p>}

      <h3>Steps <span>{steps.length}</span></h3>
      <ol className="dk-proc-steps">
        {steps.map((s, i) => (
          <li key={i} data-pick={pick === i || undefined}>
            <span className="n">{i + 1}</span>
            <span className="fields">
              <input value={s.title} aria-label={`Step ${i + 1} title`}
                onChange={(e) => onEdit(i, { title: e.target.value })} />
              <input value={s.note || ""} placeholder="A note for this step" aria-label={`Step ${i + 1} note`}
                onChange={(e) => onEdit(i, { note: e.target.value })} />
              <span className="k">
                {[s.kind, s.tool].filter(Boolean).join(" · ")}
                {s.model ? <> · <b className="dk-step-model">{s.model}</b></> : null}
              </span>
              {pick === i && (
                // The model catalog for this step, opened by the ... beside it. Nothing chosen means the Way's model.
                <div className="dk-model-pick">
                  <input autoFocus value={q} placeholder="Search models…" aria-label={`Search models for step ${i + 1}`}
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); setPick(null); setQ("") } }} />
                  <ul role="listbox" aria-label={`Models for step ${i + 1}`}>
                    <li>
                      <button type="button" data-on={!s.model || undefined} onClick={() => choose(i, "")}>
                        Default: the Way's model
                      </button>
                    </li>
                    {shown.map((m) => (
                      <li key={m}>
                        <button type="button" data-on={s.model === m || undefined} onClick={() => choose(i, m)}>{m}</button>
                      </li>
                    ))}
                  </ul>
                  {!catalog.length && <p className="dk-note">No model list on this machine yet.</p>}
                  {catalog.length > 0 && !shown.length && <p className="dk-note">No model matches that.</p>}
                </div>
              )}
            </span>
            <button type="button" className="dk-step-opts" aria-label={`Model for step ${i + 1}`} aria-expanded={pick === i}
              title={s.model ? `Model: ${s.model}` : "Choose a model for this step (default: the Way's)"}
              onClick={() => { setPick(pick === i ? null : i); setQ("") }}>⋯</button>
            <button type="button" className="dk-step-del" aria-label={`Remove step ${i + 1}`} title="Remove this step"
              onClick={() => onRemove(i)}>×</button>
          </li>
        ))}
      </ol>

      <div className="dk-edit-actions">
        <button type="button" className="btn-line dg-mini" onClick={onAdd}>+ Add step</button>
        <button type="button" className="dk-proc-use" disabled={!onUse}
          title={onUse ? (useTitle || "Run this Way with the steps as edited") : "Pick a task first: the Way runs with it"}
          onClick={onUse}>
          Use this Way<span className="sq" aria-hidden="true">→</span>
        </button>
      </div>

      {onAsk && (
        // Like the canvas's ask: a sentence goes to kell, which rewrites the steps (title and note); the graph follows.
        <form className="dk-proc-ask" onSubmit={async (e) => {
          e.preventDefault()
          const text = ask.trim()
          if (!text || busy) return
          setBusy(true); setErr("")
          try { await onAsk(text); setAsk("") }
          catch (ex) { setErr(String((ex as Error)?.message || ex)) }
          finally { setBusy(false) }
        }}>
          <input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask kell to change these steps…"
            aria-label="Ask kell to change these steps" disabled={busy} />
          <button type="submit" className="btn-ink dg-mini" disabled={!ask.trim() || busy}>{busy ? "Editing…" : "Change it"}</button>
        </form>
      )}
      {err && <p className="dk-err" role="alert">{err}</p>}
    </div>
  )
}
