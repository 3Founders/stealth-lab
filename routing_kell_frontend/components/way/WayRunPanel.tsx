"use client"

/**
 * The panel that stands between a Way and its run: the inputs it takes, and the questions a step must ask.
 *
 * Two things live here because they are the same moment for the person: "what does this need from me?".
 *
 * Design taken from Claude Code's `AskUserQuestion`, deliberately. That team documented two failed designs before
 * this one — questions attached to a plan (confused the model), and a markdown format to parse ("it would append
 * extra sentences, drop options, or abandon the structure altogether"). The lesson: **the shape must be structured
 * data, not prose we parse.** So an option is a labelled choice with a description, and free text is the last
 * resort rather than the only box.
 *
 * Two rules that make it pleasant rather than an interrogation:
 *  1. **Optional inputs are not shown closed.** An input the author declared optional renders with its default or
 *     empty, and the run button works regardless.
 *  2. **Questions are asked one at a time.** Four at once spends attention; a fifth discovered later spends it
 *     again.
 */
import { useState } from "react"

export type WayInputDef = {
  name: string
  description?: string
  required?: boolean
  type?: "text" | "number" | "url" | "file" | "choice"
  default?: string | null
  choices?: string[]
}

export type WayOption = { label: string; description?: string }

export type WayQuestion = {
  key: string
  question: string
  header?: string
  options: WayOption[]
  multiSelect?: boolean
  allowFreeText?: boolean
}

/** Start-of-run: fill in what the Way declares, then hand back a `name -> value` record. */
export function InputsForm({ inputs, onStart, busy, error }: {
  inputs: WayInputDef[]
  onStart: (values: Record<string, string>) => void
  busy?: boolean
  error?: string | null
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {}
    for (const i of inputs) if (i.default != null) seed[i.name] = i.default
    return seed
  })

  const missing = inputs.filter((i) => i.required && !(values[i.name] || "").trim() && i.default == null)

  return (
    <form
      className="wi"
      onSubmit={(e) => {
        e.preventDefault()
        if (!missing.length) onStart(values)
      }}
    >
      {inputs.length === 0 && (
        <p className="wi-note">This Way needs nothing from you — press Run and it starts.</p>
      )}

      {inputs.map((input) => (
        <label key={input.name} className="wi-field">
          <span className="wi-label">
            {input.name}
            {input.required ? <b className="wi-req" aria-label="required"> required</b> : <i className="wi-opt"> optional</i>}
          </span>
          {input.description && <span className="wi-desc">{input.description}</span>}
          {input.type === "choice" && input.choices?.length ? (
            <select
              value={values[input.name] ?? input.default ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [input.name]: e.target.value }))}
            >
              {!input.required && <option value="">— none —</option>}
              {input.choices.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input
              type={input.type === "number" ? "number" : input.type === "url" ? "url" : "text"}
              value={values[input.name] ?? ""}
              placeholder={input.default ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [input.name]: e.target.value }))}
            />
          )}
        </label>
      ))}

      <div className="wi-actions">
        <button type="submit" className="btn-ink" disabled={busy || missing.length > 0}>
          {busy ? "Starting…" : "Run this Way"}
        </button>
        {missing.length > 0 && (
          <span className="wi-note">Fill in {missing.map((m) => m.name).join(", ")} to start.</span>
        )}
      </div>
      {error && <p className="wi-error">{error}</p>}
    </form>
  )
}

/**
 * One question, mid-run. Blocks until answered, exactly like the tool it mirrors: the run is paused on the server,
 * so there is nothing to time out except the person.
 */
export function QuestionCard({ question, onAnswer, busy, error }: {
  question: WayQuestion
  onAnswer: (answer: string | string[]) => void
  busy?: boolean
  error?: string | null
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [free, setFree] = useState("")

  const toggle = (label: string) => {
    if (!question.multiSelect) return onAnswer(label)
    setPicked((p) => (p.includes(label) ? p.filter((x) => x !== label) : [...p, label]))
  }

  return (
    <section className="wq" aria-labelledby="wq-h" role="group">
      <div className="wq-head">
        {question.header && <span className="wq-tag">{question.header}</span>}
        <h3 id="wq-h">{question.question}</h3>
      </div>

      <ul className="wq-options">
        {question.options.map((o) => (
          <li key={o.label}>
            <button
              type="button"
              className="wq-option"
              data-picked={picked.includes(o.label) || undefined}
              aria-pressed={question.multiSelect ? picked.includes(o.label) : undefined}
              disabled={busy}
              onClick={() => toggle(o.label)}
            >
              <b>{o.label}</b>
              {o.description && <span>{o.description}</span>}
            </button>
          </li>
        ))}
      </ul>

      {question.multiSelect && (
        <div className="wq-actions">
          <button type="button" className="btn-ink" disabled={busy || picked.length === 0} onClick={() => onAnswer(picked)}>
            {busy ? "Sending…" : `Continue with ${picked.length || "none"} selected`}
          </button>
        </div>
      )}

      {question.allowFreeText && (
        <form
          className="wq-free"
          onSubmit={(e) => {
            e.preventDefault()
            if (free.trim()) onAnswer(free.trim())
          }}
        >
          <input value={free} onChange={(e) => setFree(e.target.value)} placeholder="Or type an answer…" aria-label="Your answer" />
          <button type="submit" className="btn-line" disabled={busy || !free.trim()}>Send</button>
        </form>
      )}

      {error && <p className="wq-error">{error}</p>}
    </section>
  )
}
