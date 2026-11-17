"use client"
// The Ways the router found, as selectable cards. Nothing runs until one is picked: the turn waits, and the card the
// person chooses becomes the plan (Goal + Way + steps + the model that Way needs).
//
// A card is a title and its evidence, not an icon: most Ways have no icon, so the generated tile is gone. When a Way
// does carry one it sits above the title, with the Use button beside it; the title then has the card's whole width
// (2026-11-17). Clicking the card opens its full record on the right, like the canvas panel: the Way id, Goal, approval
// and review, the tool, why it fits, and every step, with each step's title and note editable before it runs.
import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import type { Delegation, GoalOption, WayOption } from "@/lib/plan"

type Step = WayOption["steps"][number]

const short = (id?: string) => (id ? id.slice(0, 8) : "")

/** The record's metadata rows, the same ones the card's chips summarise. */
function Meta({ option }: { option: WayOption }) {
  return (
    <dl className="dk-proc-dl">
      <dt>Way</dt><dd>{option.id}</dd>
      {option.goal?.name && <><dt>Goal</dt><dd>{option.goal.name}{option.goal.id ? ` · ${option.goal.id}` : ""}</dd></>}
      <dt>Approval</dt><dd>{option.approval.replace(/_/g, " ")}</dd>
      {option.review && <><dt>Review</dt><dd>{option.review.replace(/_/g, " ")}</dd></>}
      {option.tool && <><dt>Tool</dt><dd>{option.tool.name}{option.tool.server ? ` · ${option.tool.server}` : ""}</dd></>}
      <dt>Steps</dt><dd>{option.steps.length}</dd>
    </dl>
  )
}

export default function ProcedurePicker({ goal, options, goals = [], onPick, onSkip }: {
  goal: Delegation["goal"]
  options: WayOption[]
  /** The Goals the search matched, shown when none has a written Way: the result is visible before the model runs. */
  goals?: GoalOption[]
  onPick: (option: WayOption) => void
  onSkip: () => void
}) {
  const empty = options.length === 0
  // Which card's record is open on the right, and the steps the person has edited on each card.
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Step[]>>({})
  const keyOf = (o: WayOption, i: number) => `${o.id}-${i}`
  const selected = options.find((o, i) => keyOf(o, i) === openKey) ?? null
  const stepsOf = (o: WayOption, key: string) => drafts[key] ?? o.steps
  const editStep = (o: WayOption, key: string, i: number, patch: Partial<Step>) => setDrafts((d) => ({
    ...d, [key]: stepsOf(o, key).map((s, n) => (n === i ? { ...s, ...patch } : s)),
  }))
  // Escape closes the record, as it closes every overlay in the app.
  useEffect(() => {
    if (!selected) return
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpenKey(null) }
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
                    onClick={() => onPick({ ...o, steps: stepsOf(o, key) })}>
                    Use<span className="sq" aria-hidden="true">→</span>
                  </button>
                </div>
                <button type="button" className="dk-pick-open" onClick={() => setOpenKey(key)} aria-label={`Details for ${o.name}`}>
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
        // The Way's record, in a right-side sheet like the canvas: read everything, edit a step's wording, then use it.
        <div className="dk-proc-layer">
          <div className="dk-proc-scrim" onClick={() => setOpenKey(null)} aria-hidden="true" />
          <aside className="dk-proc-panel" role="dialog" aria-modal="true" aria-label={`${selected.name} details`}>
            <header className="dk-proc-head">
              <div className="dk-proc-heading">
                {selected.icon && <img className="dk-proc-icon" src={selected.icon} alt="" />}
                <div>
                  <h2>{selected.name}</h2>
                  <p>{selected.goal?.name || "Procedure"}</p>
                </div>
              </div>
              <button type="button" className="dk-proc-x" aria-label="Close" title="Close" onClick={() => setOpenKey(null)}>×</button>
            </header>
            <div className="dk-proc-body">
              <Meta option={selected} />
              {selected.why && <p className="dk-proc-why">{selected.why}</p>}
              <h3>Steps <span>{stepsOf(selected, openKey).length}</span></h3>
              <ol className="dk-proc-steps">
                {stepsOf(selected, openKey).map((s, i) => (
                  <li key={`${s.order}-${i}`}>
                    <span className="n">{s.order || i + 1}</span>
                    <span className="fields">
                      <input value={s.title} aria-label={`Step ${i + 1} title`}
                        onChange={(e) => editStep(selected, openKey, i, { title: e.target.value })} />
                      <input value={s.note || ""} placeholder="A note for this step" aria-label={`Step ${i + 1} note`}
                        onChange={(e) => editStep(selected, openKey, i, { note: e.target.value })} />
                      <span className="k">{[s.kind, s.tool].filter(Boolean).join(" · ")}</span>
                    </span>
                  </li>
                ))}
              </ol>
              {drafts[openKey] && (
                <button type="button" className="dk-proc-reset"
                  onClick={() => setDrafts((d) => { const n = { ...d }; delete n[openKey]; return n })}>
                  Reset the steps
                </button>
              )}
            </div>
            <footer className="dk-proc-foot">
              <button type="button" className="dk-proc-use"
                onClick={() => { const chosen = { ...selected, steps: stepsOf(selected, openKey) }; setOpenKey(null); onPick(chosen) }}>
                Use this Way<span className="sq" aria-hidden="true">→</span>
              </button>
            </footer>
          </aside>
        </div>,
        document.body,
      )}
    </section>
  )
}
