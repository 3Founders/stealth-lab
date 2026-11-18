"use client"
// The right panel's Procedure section: the Way's editable record, always visible. It replaced the read-only graph
// (2026-11-17): the same editor the card's sheet uses, with Add step and Use this Way, so the record lives in the panel
// and a card click only opens the sheet for the same editor.
import ProcedureEditor from "./ProcedureEditor"
import InfoTip from "./InfoTip"
import type { DelegationStep, WayOption } from "@/lib/plan"
import type { BarAttachment } from "@/lib/attach"

export default function ProcedurePanel({ option, steps, catalog = [], onEdit, onAdd, onRemove, onUse, onAsk, onSubmitWay, glass, onToggleGlass, previewLabel }: {
  option: WayOption | null
  steps: DelegationStep[]
  catalog?: string[]
  onEdit: (i: number, patch: Partial<DelegationStep>) => void
  onAdd: () => void
  onRemove: (i: number) => void
  onUse?: () => void
  /** The record's own prompt bar: a sentence and pictures go to the edit call, with that bar's model. */
  onAsk?: (text: string, files?: BarAttachment[], model?: string) => Promise<void>
  /** Open the rooms' "Submit a Way" flow: a person writes a new procedure there, step by step, for review. */
  onSubmitWay?: () => void
  glass: boolean
  onToggleGlass: () => void
  previewLabel?: string
}) {
  return (
    <section className="dk-proc" data-glass={glass ? "on" : "off"} aria-label="Procedure">
      <div className="dk-proc-head">
        <span className="dk-proc-title">
          <h2>Procedure</h2>
          {!option && <InfoTip align="start" text="Send a task: kell resolves a Goal and a Way, and its steps appear here, editable before they run." />}
        </span>
        {previewLabel && <span className="dk-proc-preview">{previewLabel}</span>}
        <span className="dk-proc-actions">
          {onSubmitWay && (
            <button type="button" className="btn-line dg-mini dk-proc-submit" onClick={onSubmitWay}
              title="Write a new Way, step by step, and submit it for review">Submit a Way</button>
          )}
          <button type="button" className="dk-icon dk-icon-sm dk-proc-toggle" aria-pressed={glass}
            aria-label={glass ? "Turn the glass effect off" : "Turn the glass effect on"}
            title={glass ? "Glass effect on" : "Glass effect off"} onClick={onToggleGlass}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
              strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 3.5l1.7 4.3 4.3 1.7-4.3 1.7L12 15.5l-1.7-4.3L6 9.5l4.3-1.7z" />
              <path d="M18.6 14.4l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
            </svg>
          </button>
        </span>
      </div>

      {!option ? null : (
        <div className="dk-proc-graph" key={option.id}>
          <span className="dk-proc-aurora" aria-hidden="true" />
          <ProcedureEditor option={option} steps={steps} catalog={catalog} onEdit={onEdit} onAdd={onAdd} onRemove={onRemove} onUse={onUse} onAsk={onAsk} />
        </div>
      )}
    </section>
  )
}
