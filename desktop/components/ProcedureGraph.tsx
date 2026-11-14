"use client"
// The procedure graph in the right sidebar: Goal -> Way -> steps, one glance instead of a single Goal row.
//
// Rendering: HTML cards on one CSS rail, not a graph library. A Way has a handful of steps, so SVG/canvas physics
// would cost more than it draws (the research: SVG is retained-mode and gets expensive with thousands of nodes;
// here there are <20). The glass is one blurred layer behind the cards, not a backdrop-filter per card: browsers
// re-sample the whole backdrop per blurred element, and the practical budget is two or three per viewport.
// The effect is optional, off for prefers-reduced-transparency / prefers-contrast, and persisted per machine.
import { useEffect, useMemo, useState } from "react"
import type { Delegation } from "@/lib/plan"

const KIND: Record<string, string> = {
  prompt: "prompt", tool: "tool", human: "person", person: "person",
  fetch: "fetch", script: "script", step: "sub-way", subgoal: "sub-way", instruction: "step",
}

const short = (id?: string) => (id ? id.slice(0, 8) : "")

export default function ProcedureGraph({ plan, glass, onToggleGlass }: {
  plan: Delegation | null
  glass: boolean
  onToggleGlass: () => void
}) {
  const steps = plan?.steps ?? []
  const [titles, setTitles] = useState<Record<number, string>>({})

  // A submitted Way's steps are sub-Ways; find_ways gives their source but not their title until the backend's
  // title field is deployed. Read the submission once, then map the titles onto the steps by order.
  const submission = useMemo(() => {
    for (const s of steps) if (s.sourceId?.startsWith("goal_submission:")) return s.sourceId.slice("goal_submission:".length)
    return null
  }, [steps])

  useEffect(() => {
    setTitles({})
    if (!submission) return
    let live = true
    fetch("/api/procedure", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ submissionId: submission }),
    }).then((r) => r.json()).then((j) => {
      if (!live || !Array.isArray(j?.steps)) return
      const map: Record<number, string> = {}
      for (const s of j.steps) if (s?.order) map[Number(s.order)] = String(s.title || "")
      setTitles(map)
    }).catch(() => undefined)
    return () => { live = false }
  }, [submission])

  return (
    <section className="dk-proc" data-glass={glass ? "on" : "off"} aria-label="Procedure graph">
      <div className="dk-proc-head">
        <h2>Procedure</h2>
        <button type="button" className="dk-icon dk-proc-toggle" aria-pressed={glass}
          aria-label={glass ? "Turn the glass effect off" : "Turn the glass effect on"}
          title={glass ? "Glass effect on" : "Glass effect off"} onClick={onToggleGlass}>
          {/* Same 18 px, 1.8-stroke language as the account and search icons in the bottom-left rail. */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 3.5l1.7 4.3 4.3 1.7-4.3 1.7L12 15.5l-1.7-4.3L6 9.5l4.3-1.7z" />
            <path d="M18.6 14.4l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
          </svg>
        </button>
      </div>

      {!plan?.way ? (
        <p className="dk-note">Send a task: kell resolves a Goal and a Way, and the graph shows the steps it will run.</p>
      ) : (
        <div className="dk-proc-graph">
          <span className="dk-proc-aurora" aria-hidden="true" />
          <div className="dk-proc-node dk-proc-goal" role="group" aria-label="Goal">
            <span className="dk-proc-label">Goal</span>
            <b title={plan.goal?.name}>{plan.goal?.name || "no Goal matched"}</b>
            {plan.goal?.id && <small>{short(plan.goal.id)}</small>}
          </div>
          <span className="dk-proc-link" aria-hidden="true" />
          <div className="dk-proc-node dk-proc-way" role="group" aria-label="Way">
            <span className="dk-proc-label">Way</span>
            <b title={plan.way.name}>{plan.way.name}</b>
            <span className="dk-proc-tags">
              <i data-approval={plan.way.approval}>{plan.way.approval}</i>
              <i>{steps.length} {steps.length === 1 ? "step" : "steps"}</i>
            </span>
          </div>
          {steps.length > 0 && (
            <ol className="dk-proc-steps" aria-label={`${steps.length} steps`}>
              {steps.map((s) => (
                <li key={`${s.order}-${s.title}`} className="dk-proc-step">
                  <span className="dk-proc-dot" data-kind={s.kind} aria-hidden="true" />
                  <div className="dk-proc-node">
                    <span className="dk-proc-order">{s.order}</span>
                    <b title={titles[s.order] || s.title}>{titles[s.order] || s.title}</b>
                    <span className="dk-proc-tags">
                      <i data-kind={s.kind}>{KIND[s.kind] || s.kind}</i>
                      {s.tool && <i className="dk-proc-tool" title={s.locator?.url || s.server || s.tool}>{s.tool}</i>}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          )}
          <div className="dk-proc-foot">
            {plan.model?.id && <span className="dk-proc-chip">{plan.model.id}</span>}
            {plan.tool?.name && <span className="dk-proc-chip">{plan.tool.name}</span>}
          </div>
        </div>
      )}
    </section>
  )
}
