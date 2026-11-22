"use client"
// The Ways the router found, as selectable cards. Nothing runs until one is picked: the turn waits, and the card the
// person chooses becomes the plan (Goal + Way + steps + the model that Way needs).
//
// A card is a title and its evidence, not an icon: most Ways have no icon, so the generated tile is gone. When a Way
// does carry one it sits above the title, with the Use button beside it; the title then has the card's whole width
// (2026-11-17). Clicking the card opens its full record on the right, like the canvas panel: the Way id, Goal, approval
// and review, the tool, why it fits, and every step, with each step's title and note editable before it runs. The same
// editor is the right panel's Procedure section; the steps are shared with it (2026-11-17).
import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import type { Delegation, DelegationStep, GoalOption, Models, WayOption } from "@/lib/plan"
import type { BarAttachment } from "@/lib/attach"
import ProcedureEditor from "@/components/ProcedureEditor"

const short = (id?: string) => (id ? id.slice(0, 8) : "")

export default function ProcedurePicker({ goal, options, goals = [], onPick, onSkip, onInspect, stepsFor, onEdit, onAdd, onRemove, catalog = [], models, onSuggest, onAsk, initialAsk = "", initialFiles = [] }: {
  goal: Delegation["goal"]
  options: WayOption[]
  /** The Goals the search matched, shown when none has a written Way: the result is visible before the model runs. */
  goals?: GoalOption[]
  onPick: (option: WayOption) => void
  onSkip: () => void
  /** The card whose record is open (or null): the right panel follows it, so the record is always visible there too. */
  onInspect?: (option: WayOption | null) => void
  /** The steps of a Way as edited so far: the panel and the sheet share them. */
  stepsFor: (o: WayOption) => DelegationStep[]
  onEdit: (o: WayOption, i: number, patch: Partial<DelegationStep>) => void
  onAdd: (o: WayOption) => void
  onRemove: (o: WayOption, i: number) => void
  /** This machine's models, searched by a step's model picker. */
  catalog?: string[]
  /** The cheap and strong rungs: what a step with no model is told kell would pick. */
  models?: Models
  /** Fill every step that has no model with kell's recommendation. */
  onSuggest?: (o: WayOption) => void
  /** Ask kell to rewrite this Way's steps from a sentence and any pictures (the canvas's "Change it"). */
  onAsk?: (o: WayOption, text: string, files?: BarAttachment[], model?: string) => Promise<string | void>
  /** A message and pictures the person typed or dropped into the chat: they belong to this Way's record, so the inline
   *  bar starts with them rather than the person retyping them. The chat's composer keeps its own copy until sent. */
  initialAsk?: string
  initialFiles?: BarAttachment[]
}) {
  const empty = options.length === 0
  // Which card's record is open on the right.
  const [openKey, setOpenKey] = useState<string | null>(null)
  const keyOf = (o: WayOption, i: number) => `${o.id}-${i}`
  const selected = options.find((o, i) => keyOf(o, i) === openKey) ?? null
  /** Open or close a card's record, and tell the right panel which Way is being read. */
  const openRecord = (key: string | null) => {
    setOpenKey(key)
    onInspect?.(key ? options.find((o, i) => keyOf(o, i) === key) ?? null : null)
  }
  useEffect(() => () => onInspect?.(null), [])
  // Escape closes the record, as it closes every overlay in the app.
  useEffect(() => {
    if (!selected) return
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") openRecord(null) }
    document.addEventListener("keydown", key)
    return () => document.removeEventListener("keydown", key)
  }, [selected])
  return (
    <section className="dk-pick" aria-label={empty ? "Procedure search result" : "Choose a procedure"}>
      <div className="dk-pick-head">
        <h2>{empty ? "Procedure search" : "Choose a procedure"}</h2>
        <p className="dk-pick-sub">
          {empty ? (
            <>No stored Way matches this task. kell can still work on it: the model runs only after you say so.</>
          ) : (
            <>
              {options.length === 1 && goal?.name ? <>For <b>{goal.name}</b>. </> : null}
              {options.length} {options.length === 1 ? "Way" : "Ways"} found for this task. kell is waiting: pick one and it continues.
            </>
          )}
        </p>
      </div>
      {empty ? (
        <div className="dk-pick-empty">
          {goal?.name && <p className="dk-pick-empty-goal">Closest Goal: <b>{goal.name}</b></p>}
          {goals.length > 0 && (
            <div className="dk-pick-goals" aria-label="Goals the search matched">
              {goals.slice(0, 6).map((g) => (
                <span key={g.id} className="dk-pick-goal-chip" data-has-way={g.hasWay || undefined} title={g.name}>
                  {g.name}{g.hasWay ? "" : " · no Way"}
                </span>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="dk-pick-strip" role="group" aria-label="Ways to apply">
          {options.map((o, i) => {
            const key = keyOf(o, i)
            return (
              <div key={key} className="dk-pick-card" data-open={openKey === key || undefined} style={{ ["--i" as any]: i }}>
                <div className="dk-pick-top">
                  {o.icon && <img className="dk-pick-icon" src={o.icon} alt="" loading="lazy" />}
                  <button type="button" className="dk-pick-use" aria-label={`Use ${o.name}`}
                    onClick={() => onPick({ ...o, steps: stepsFor(o) })}>
                    Use<span className="sq" aria-hidden="true">→</span>
                  </button>
                </div>
                <button type="button" className="dk-pick-open" onClick={() => openRecord(key)} aria-label={`Details for ${o.name}`}>
                  <span className="dk-pick-title">
                    <b title={o.name}>{o.name}</b>
                    {i === 0 && <span className="dk-pick-best">Best fit</span>}
                  </span>
                  {o.why && <span className="dk-pick-why" title={o.why}>{o.why}</span>}
                  <span className="dk-pick-meta">
                    {o.goal?.name && <i className="dk-pick-goal" title={o.goal.name}>{o.goal.name}</i>}
                    <i data-approval={o.approval}>{o.approval}</i>
                    {o.review && o.review !== o.approval && <i data-approval={o.review}>{o.review.replace(/_/g, " ")}</i>}
                    <i>{o.steps.length} {o.steps.length === 1 ? "step" : "steps"}</i>
                    {o.tool && <i className="dk-pick-tool" title={o.tool.server || o.tool.name}>{o.tool.name}</i>}
                    {o.id && <i className="dk-pick-id">{short(o.id)}</i>}
                  </span>
                </button>
              </div>
            )
          })}
        </div>
      )}
      <button type="button" className="dk-pick-skip" onClick={onSkip}>
        {empty ? "Let kell work on it without a procedure" : "Run without a Way"}
      </button>

      {selected && openKey && createPortal(
        // The Way's record, in a right-side sheet like the canvas: the same editor the panel shows, with Add step and
        // Use this Way; each step's x removes it (2026-11-17).
        <div className="dk-proc-layer">
          <div className="dk-proc-scrim" onClick={() => openRecord(null)} aria-hidden="true" />
          <aside className="dk-proc-panel" role="dialog" aria-modal="true" aria-label={`${selected.name} details`}>
            <header className="dk-proc-head">
              <div className="dk-proc-heading">
                {selected.icon && <img className="dk-proc-icon" src={selected.icon} alt="" />}
                <div>
                  <h2>{selected.name}</h2>
                  <p>{selected.goal?.name || "Procedure"}</p>
                </div>
              </div>
              <button type="button" className="dk-proc-x" aria-label="Close" title="Close" onClick={() => openRecord(null)}>×</button>
            </header>
            <div className="dk-proc-body">
              <ProcedureEditor option={selected} steps={stepsFor(selected)} catalog={catalog} models={models}
                initialAsk={initialAsk} initialFiles={initialFiles}
                onSuggest={onSuggest ? () => onSuggest(selected) : undefined}
                onEdit={(i, patch) => onEdit(selected, i, patch)}
                onAdd={() => onAdd(selected)}
                onRemove={(i) => onRemove(selected, i)}
                onAsk={onAsk ? (t, files, model) => onAsk(selected, t, files, model) : undefined}
                onUse={() => { const chosen = { ...selected, steps: stepsFor(selected) }; openRecord(null); onPick(chosen) }} />
            </div>
          </aside>
        </div>,
        document.body,
      )}
    </section>
  )
}
