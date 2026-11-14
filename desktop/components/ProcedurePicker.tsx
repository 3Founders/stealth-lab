"use client"
// The Ways the router found, as selectable cards. Nothing runs until one is picked: the turn waits, and the card the
// person chooses becomes the plan (Goal + Way + steps + the model that Way needs).
//
// Card anatomy follows the App Store / selectable-card patterns the research recommends: a media tile, a title, one
// line of supporting text, a metadata row, and a clear action; the whole card is the target, 2-6 options, consistent
// height, and "Best fit" is text, not only colour. The first card is the router's own first choice, not a claim that
// it is right.
import type { Delegation, WayOption } from "@/lib/plan"

const short = (id?: string) => (id ? id.slice(0, 8) : "")

function WayIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="6" cy="6" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="12" cy="18" r="2.2" />
      <path d="M8.2 6h7.6M7.2 8l3.6 7.8M16.8 8l-3.6 7.8" />
    </svg>
  )
}

export default function ProcedurePicker({ goal, options, glass, onToggleGlass, onPick, onSkip }: {
  goal: Delegation["goal"]
  options: WayOption[]
  glass: boolean
  onToggleGlass: () => void
  onPick: (option: WayOption) => void
  onSkip: () => void
}) {
  return (
    <section className="dk-proc dk-pick" data-glass={glass ? "on" : "off"} aria-label="Choose a procedure">
      <div className="dk-proc-head">
        <h2>Choose a procedure</h2>
        <button type="button" className="dk-icon dk-proc-toggle" aria-pressed={glass}
          aria-label={glass ? "Turn the glass effect off" : "Turn the glass effect on"}
          title={glass ? "Glass effect on" : "Glass effect off"} onClick={onToggleGlass}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 3.5l1.7 4.3 4.3 1.7-4.3 1.7L12 15.5l-1.7-4.3L6 9.5l4.3-1.7z" />
            <path d="M18.6 14.4l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
          </svg>
        </button>
      </div>
      <p className="dk-pick-sub">
        {goal?.name ? <>For <b>{goal.name}</b>. </> : null}
        {options.length} {options.length === 1 ? "Way" : "Ways"} found. Pick one; the turn runs on the model it needs.
      </p>
      <div className="dk-proc-graph dk-pick-graph">
        <span className="dk-proc-aurora" aria-hidden="true" />
        <div className="dk-pick-list" aria-label="Ways to apply">
          {options.map((o, i) => (
            <button key={`${o.id}-${i}`} type="button" className="dk-pick-card"
              onClick={() => onPick(o)} aria-label={`Use ${o.name}`}>
              <span className="dk-pick-icon" aria-hidden="true"><WayIcon /></span>
              <span className="dk-pick-body">
                <span className="dk-pick-title">
                  <b title={o.name}>{o.name}</b>
                  {i === 0 && <span className="dk-pick-best">Best fit</span>}
                </span>
                {o.why && <span className="dk-pick-why" title={o.why}>{o.why}</span>}
                <span className="dk-pick-meta">
                  <i data-approval={o.approval}>{o.approval}</i>
                  {o.review && o.review !== o.approval && <i data-approval={o.review}>{o.review.replace(/_/g, " ")}</i>}
                  <i>{o.steps.length} {o.steps.length === 1 ? "step" : "steps"}</i>
                  {o.tool && <i className="dk-pick-tool" title={o.tool.server || o.tool.name}>{o.tool.name}</i>}
                  {o.id && <i className="dk-pick-id">{short(o.id)}</i>}
                </span>
              </span>
              <span className="dk-pick-cta" aria-hidden="true">Use<span className="sq">→</span></span>
            </button>
          ))}
        </div>
        <button type="button" className="dk-pick-skip" onClick={onSkip}>Run without a Way</button>
      </div>
    </section>
  )
}
