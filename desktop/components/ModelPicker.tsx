"use client"
// One model picker for every prompt bar: the main composer and the procedure's ask bar. It promises nothing it cannot do.
// A model is a "provider/id" string (the catalog from app/api/settings: every model this machine may run); the Chat entry
// means "no opinion", so kell's router picks the rung for the task, which is the app's normal behaviour. A model this
// machine's lane cannot see stays listed with "not in this lane" instead of vanishing: a choice that silently went away is
// worse than one that says it cannot run.
import { useEffect, useRef, useState } from "react"
import type { DelegationModel } from "@/lib/plan"

const short = (m: string) => m.split("/").pop() || m

/** "kell/some-model" -> the reference app/api/prompt sends. A bare id keeps the kell provider (the lane's own). */
export function toModelRef(m: string): DelegationModel {
  const at = m.indexOf("/")
  return at > 0 ? { providerID: m.slice(0, at), id: m.slice(at + 1) } : { providerID: "kell", id: m }
}

/** What a chip says for a model value. Empty means the router chooses. */
export const modelLabel = (m: string) => (m ? short(m) : "Chat")

export default function ModelPicker({ value, catalog = [], onChange, disabled, label = "Model", align = "start" }: {
  value: string
  catalog?: string[]
  onChange: (m: string) => void
  disabled?: boolean
  /** The chip's accessible name, e.g. "Model for the main chat" or "Model for this Way". */
  label?: string
  align?: "start" | "end"
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const box = useRef<HTMLDivElement | null>(null)
  const known = catalog.includes(value)
  // A click anywhere else closes it, as every popover in the app does.
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", away)
    return () => document.removeEventListener("mousedown", away)
  }, [open])
  const needle = q.trim().toLowerCase()
  const shown = catalog.filter((m) => m.toLowerCase().includes(needle)).slice(0, 40)
  const choose = (m: string) => { onChange(m); setOpen(false); setQ("") }
  return (
    <div className="dk-mp" ref={box}>
      <button type="button" className="dk-mp-chip" data-on={value || undefined} disabled={disabled}
        aria-haspopup="listbox" aria-expanded={open} aria-label={`${label}: ${modelLabel(value)}`}
        title={value ? `${value}${known ? "" : " (not in this lane)"}` : "kell's router picks the model for each task"}
        onClick={() => { setOpen((o) => !o); setQ("") }}>
        {/* A small mark for what is chosen: the router (a spark), or a dot for a named model. */}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
          strokeLinejoin="round" aria-hidden="true">{value
            ? <circle cx="12" cy="12" r="7" />
            : <path d="M12 3.5l1.7 4.3 4.3 1.7-4.3 1.7L12 15.5l-1.7-4.3L6 9.5l4.3-1.7z" />}</svg>
        <span>{modelLabel(value)}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="dk-mp-pop" data-align={align}>
          <input autoFocus value={q} placeholder="Search models…" aria-label={`Search models for ${label}`} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.preventDefault(); setOpen(false) }
              if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); if (shown.length) choose(shown[0]) }
            }} />
          <ul role="listbox" aria-label={label}>
            <li>
              <button type="button" data-on={!value || undefined} onClick={() => choose("")}>
                <b>Chat</b><small>kell's router picks the rung for the task</small>
              </button>
            </li>
            {shown.map((m) => (
              <li key={m}>
                <button type="button" data-on={value === m || undefined} onClick={() => choose(m)}>
                  <b title={m}>{short(m)}</b><small>{m.includes("/") ? m.split("/")[0] : "kell"}</small>
                </button>
              </li>
            ))}
            {value && !known && (
              // A choice that no longer appears in this lane: still selectable, still visible, never silently dropped.
              <li>
                <button type="button" data-on onClick={() => choose("")}>
                  <b>{short(value)}</b><small className="dk-mp-warn">not in this lane · press to go back to Chat</small>
                </button>
              </li>
            )}
          </ul>
          {!catalog.length && <p className="dk-note">No model list on this machine yet: Chat runs on the engine&apos;s default.</p>}
          {catalog.length > 0 && !shown.length && <p className="dk-note">No model matches that.</p>}
        </div>
      )}
    </div>
  )
}
