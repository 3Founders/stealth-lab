"use client"
// The Ways the router found, as selectable cards. Nothing runs until one is picked: the turn waits, and the card the
// person chooses becomes the plan (Goal + Way + steps + the model that Way needs).
//
// The card follows the rooms' tool sign-in card (components/chat/RoomView.tsx, .dg-signin-* in the website's chat.css):
// a gradient icon tile, a title, one line of supporting text, a metadata row, a "Best fit" badge on the first, and one
// dark action with the yellow arrow. Several Ways scroll sideways, one snap per card, instead of growing the sidebar
// downwards (2026-11-16). Each card can be expanded to read the Way's full record (id, Goal, approval and review, the
// tool, why it fits, and every step) without choosing it (2026-11-17). The whole top of the card is the target.
import { useState } from "react"
import type { Delegation, GoalOption, WayOption } from "@/lib/plan"

const short = (id?: string) => (id ? id.slice(0, 8) : "")

/** A stable hue per Way, the same way the rooms colour a tool's tile: the name decides, not the position. */
const hueOf = (name: string) => {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

function WayIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="6" cy="6" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="12" cy="18" r="2.2" />
      <path d="M8.2 6h7.6M7.2 8l3.6 7.8M16.8 8l-3.6 7.8" />
    </svg>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" data-open={open || undefined}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

/** One Way's full record: what it is, who approved it, and every step it would run. */
function WayDetails({ option }: { option: WayOption }) {
  return (
    <div className="dk-pick-detail">
      <dl className="dk-pick-dl">
        <dt>Way</dt><dd>{option.id}</dd>
        {option.goal?.name && <><dt>Goal</dt><dd>{option.goal.name}{option.goal.id ? ` · ${option.goal.id}` : ""}</dd></>}
        <dt>Approval</dt><dd>{option.approval.replace(/_/g, " ")}</dd>
        {option.review && <><dt>Review</dt><dd>{option.review.replace(/_/g, " ")}</dd></>}
        {option.tool && <><dt>Tool</dt><dd>{option.tool.name}{option.tool.server ? ` · ${option.tool.server}` : ""}</dd></>}
        <dt>Steps</dt><dd>{option.steps.length}</dd>
      </dl>
      {option.why && <p className="dk-pick-detail-why">{option.why}</p>}
      {option.steps.length > 0 && (
        <ol className="dk-pick-steps" aria-label={`${option.steps.length} steps`}>
          {option.steps.map((s, n) => (
            <li key={`${s.order}-${n}`}>
              <span className="n">{s.order}.</span>
              <span className="t">
                {s.title}
                {(s.kind || s.tool || s.note) && (
                  <span className="k">{[s.kind, s.tool, s.note].filter(Boolean).join(" · ")}</span>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
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
  // One card open at a time: the strip stays short, and the open record is not repeated beside another open one.
  const [open, setOpen] = useState<string | null>(null)
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
            const hue = hueOf(o.name || o.id)
            const key = `${o.id}-${i}`
            const isOpen = open === key
            return (
              <div key={key} className="dk-pick-card" data-open={isOpen || undefined} style={{ ["--i" as any]: i }}>
                <button type="button" className="dk-pick-main" onClick={() => onPick(o)} aria-label={`Use ${o.name}`}>
                  <span className="dk-pick-icon" aria-hidden="true"
                    style={{ background: `linear-gradient(145deg, hsl(${hue} 62% 42%), hsl(${hue + 38} 62% 30%))` }}>
                    <WayIcon />
                  </span>
                  <span className="dk-pick-body">
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
                  </span>
                  <span className="dk-pick-cta" aria-hidden="true">Use<span className="sq">→</span></span>
                </button>
                <div className="dk-pick-foot">
                  <button type="button" className="dk-pick-toggle" aria-expanded={isOpen}
                    onClick={() => setOpen(isOpen ? null : key)}>
                    {isOpen ? "Hide details" : "Details"}<Chevron open={isOpen} />
                  </button>
                </div>
                {isOpen && <WayDetails option={o} />}
              </div>
            )
          })}
        </div>
      )}
      <button type="button" className="dk-pick-skip" onClick={onSkip}>
        {empty ? "Let kell work on it without a procedure" : "Run without a Way"}
      </button>
    </section>
  )
}
