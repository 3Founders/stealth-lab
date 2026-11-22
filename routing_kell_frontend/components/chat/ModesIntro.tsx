"use client"
import { useEffect, useRef } from "react"
import "./modes-intro.css"

/**
 * The first time the rooms open: what the three modes above the message box do.
 *
 * The switch reads "Direct | Auto | Ask" with no verb, and the difference between them is the difference between kell
 * doing nothing, doing the work on its own, and asking first. Shown once per browser (kell:modes-seen). Same words as
 * the desktop app's components/ModesIntro.tsx on purpose: a person meets the same three choices in both places.
 */
const MODES = [
  {
    id: "direct",
    label: "Direct",
    line: "Your message goes straight to the model.",
    detail: "kell looks nothing up. Best when you already know exactly what you want.",
  },
  {
    id: "auto",
    label: "Auto",
    line: "kell finds a Way and runs the best fit on its own.",
    detail: "One stored procedure runs step by step without stopping to ask. Best for work you have done before.",
  },
  {
    id: "ask",
    label: "Ask",
    line: "kell finds Ways and shows them for you to pick.",
    detail: "Nothing runs until you choose. This is where kell starts.",
  },
]

export default function ModesIntro({ onClose }: { onClose: () => void }) {
  const done = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    done.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); onClose() } }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div className="mi-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="mi-card" role="dialog" aria-modal="true" aria-labelledby="mi-title">
        <p className="mi-kicker">Before you type</p>
        <h2 id="mi-title">Three ways to send a message</h2>
        <p className="mi-lead">
          A Way is a stored procedure kell has learned or you wrote: steps in order, with the check that proves each one.
          The switch above the message box decides what kell does with your message before the model answers.
        </p>
        <ul className="mi-modes">
          {MODES.map((m) => (
            <li key={m.id} className="mi-mode" data-mode={m.id}>
              <span className="mi-dot" aria-hidden="true" />
              <span className="mi-text">
                <b>{m.label}</b>
                <span className="mi-line">{m.line}</span>
                <span className="mi-detail">{m.detail}</span>
              </span>
            </li>
          ))}
        </ul>
        <p className="mi-foot">You can change it at any time; kell remembers your choice.</p>
        <div className="mi-actions">
          <button ref={done} type="button" className="btn-ink" onClick={onClose}>Got it</button>
        </div>
      </div>
    </div>
  )
}
