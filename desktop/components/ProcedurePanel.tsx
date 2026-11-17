"use client"
// The right panel's Procedure section: the Way's editable record, always visible. It replaced the read-only graph
// (2026-11-17): the same editor the card's sheet uses, with Add step and Use this Way, so the record lives in the panel
// and a card click only opens the sheet for the same editor.
import ProcedureEditor from "./ProcedureEditor"
import type { DelegationStep, WayOption } from "@/lib/plan"

export default function ProcedurePanel({ option, steps, catalog = [], onEdit, onAdd, onRemove, onUse, onAsk, glass, onToggleGlass, previewLabel }: {
  option: WayOption | null
  steps: DelegationStep[]
  catalog?: string[]
  onEdit: (i: number, patch: Partial<DelegationStep>) => void
  onAdd: () => void
  onRemove: (i: number) => void
  onUse?: () => void
  onAsk?: (text: string) => Promise<void>
  glass: boolean
  onToggleGlass: () => void
  previewLabel?: string
}) {
  return (
    <section className="dk-proc" data-glass={glass ? "on" : "off"} aria-label="Procedure">
      <div className="dk-proc-head">
        <h2>Procedure</h2>
        {previewLabel && <span className="dk-proc-preview">{previewLabel}</span>}
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

      {!option ? (
        <p className="dk-note">Send a task: kell resolves a Goal and a Way, and its steps appear here, editable before they run.</p>
      ) : (
        <div className="dk-proc-graph" key={option.id}>
          <span className="dk-proc-aurora" aria-hidden="true" />
          <ProcedureEditor option={option} steps={steps} catalog={catalog} onEdit={onEdit} onAdd={onAdd} onRemove={onRemove} onUse={onUse} onAsk={onAsk} />
        </div>
      )}
    </section>
  )
}
