"use client"
// The Ways the router found, as selectable cards. Nothing runs until one is picked: the turn waits, and the card the
// person chooses becomes the plan (Goal + Way + steps + the model that Way needs).
//
// The card follows the rooms' tool sign-in card (components/chat/RoomView.tsx, .dg-signin-* in the website's chat.css):
// a gradient icon tile, a title, one line of supporting text, a metadata row, a "Best fit" badge on the first, and one
// dark action with the yellow arrow. Several Ways scroll sideways, one snap per card, instead of growing the sidebar
// downwards (2026-11-16). The whole card is the target.
import type { Delegation, WayOption } from "@/lib/plan"

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

export default function ProcedurePicker({ goal, options, onPick, onSkip }: {
  goal: Delegation["goal"]
  options: WayOption[]
  onPick: (option: WayOption) => void
  onSkip: () => void
}) {
  return (
    <section className="dk-pick" aria-label="Choose a procedure">
      <div className="dk-pick-head">
        <h2>Choose a procedure</h2>
        <p className="dk-pick-sub">
          {goal?.name ? <>For <b>{goal.name}</b>. </> : null}
          {options.length} {options.length === 1 ? "Way" : "Ways"} found. kell is waiting: pick one and it continues.
        </p>
      </div>
      <div className="dk-pick-strip" role="group" aria-label="Ways to apply">
        {options.map((o, i) => {
          const hue = hueOf(o.name || o.id)
          return (
            <button key={`${o.id}-${i}`} type="button" className="dk-pick-card"
              style={{ ["--i" as any]: i }}
              onClick={() => onPick(o)} aria-label={`Use ${o.name}`}>
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
          )
        })}
      </div>
      <button type="button" className="dk-pick-skip" onClick={onSkip}>Run without a Way</button>
    </section>
  )
}
