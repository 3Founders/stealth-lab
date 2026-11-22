"use client"

/**
 * A Way's run, as a conversation — and nothing else.
 *
 * This is not a general chat. Only five things can appear, and that restraint is the design:
 *
 *   kell   a question a step needs answered          -> options, one at a time
 *   kell   a step has finished, with its output      -> the output, readable
 *   kell   the run is waiting on a person            -> what to do
 *   you    an input, or an answer                    -> sent, and echoed above
 *   kell   a refusal or a failure                    -> said plainly, never a spinner that never ends
 *
 * Why a chat and not a form: a Way is a sequence, and a person wants to see it move — step 1 produced this, now
 * step 2 asks that. A form shows one state at a time and throws history away. Why *only* those five: a free-text
 * box on a page about evidence invites the person to type anything, the run has no use for it, and the old
 * comment-box failure (docs/KELL.md §4) comes straight back. If a message is not one of the five, it cannot be
 * sent.
 *
 * The question shape is AskUserQuestion's, copied deliberately — see app/submissions/way_inputs.py for why that
 * team's third attempt is the one worth taking.
 */
import { useEffect, useRef, useState } from "react"
import { downloadArtifact } from "@/lib/api"

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

/** One file a run produced. `url` is licence-gated; `download_all` is the whole deliverable as one archive. */
export type Artifact = { name: string; media_type?: string; size_bytes?: number; url: string; order?: number }

/** One turn. `kind` decides how it is drawn and, for `you`, that it is a message the person actually sent. */
export type Turn =
  | { kind: "kell"; text: string; tone?: "note" | "error" | "done" }
  | {
      kind: "step"
      order: number
      title: string
      output: string
      /** Files this step read from earlier steps (step `imports`). */
      reads?: string[]
      /** Files it handed back (step `produces`). */
      writes?: string[]
    }
  | { kind: "files"; files: Artifact[]; zip?: string }
  | { kind: "you"; text: string }
export function WayChat({
  inputs,
  turns,
  question,
  busy,
  onStart,
  onAnswer,
  started,
  estimate,
  layout = "chat",
}: {
  inputs: WayInputDef[]
  turns: Turn[]
  question: WayQuestion | null
  busy: boolean
  onStart: (values: Record<string, string>) => void
  onAnswer: (value: string | string[]) => void
  started: boolean
  /** The pre-run cost range (docs/pricingestimation.md). Absent = not measured yet and not estimable. */
  estimate?: CostEstimate | null
  /** "chat" is the conversation; "canvas" is the same content as a column of cards, for the desktop pane. */
  layout?: "chat" | "canvas"
}) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" })
  }, [turns.length, question?.key])

  return (
    <section className="wc" data-layout={layout} aria-label="Way run" aria-live="polite">
      {!started && estimate && <CostRange estimate={estimate} />}
      <ol className="wc-log">
        {turns.map((t, i) => (
          <li key={i} className="wc-turn" data-who={t.kind === "you" ? "you" : "kell"} data-kind={t.kind}>
            {t.kind === "step" ? (
              <article className="wc-step">
                <header>
                  <span className="wc-n">{t.order}</span>
                  <b>{t.title}</b>
                </header>
                {(t.reads?.length || t.writes?.length) ? (
                  <p className="wc-io">
                    {t.reads?.length ? <span>read {t.reads.join(", ")}</span> : null}
                    {t.reads?.length && t.writes?.length ? <span className="wc-io-arrow"> → </span> : null}
                    {t.writes?.length ? <span>wrote {t.writes.join(", ")}</span> : null}
                  </p>
                ) : null}
                <StepOutput text={t.output} />
              </article>
            ) : t.kind === "files" ? (
              <Deliverable files={t.files} zip={t.zip} />
            ) : (
              <p className={t.kind === "you" ? "wc-you" : "wc-kell"} data-tone={t.kind === "kell" ? t.tone : undefined}>
                {t.text}
              </p>
            )}
          </li>
        ))}

        {!started && (
          <li className="wc-turn" data-who="kell">
            <Inputs inputs={inputs} busy={busy} onStart={onStart} />
          </li>
        )}

        {question && (
          <li className="wc-turn" data-who="kell">
            <Question question={question} busy={busy} onAnswer={onAnswer} />
          </li>
        )}

        {busy && (
          <li className="wc-turn" data-who="kell">
            <p className="wc-kell wc-thinking" role="status">
              Working…
            </p>
          </li>
        )}
      </ol>
      <div ref={end} />
    </section>
  )
}

/** What a run is expected to cost, before it has run. A RANGE, with the assumptions that produced it. */
export type CostEstimate = {
  low_paise?: number
  high_paise?: number
  low_usd?: number
  high_usd?: number
  /** What the buyer pays: the cost with retries included, plus margin (docs/pricingestimation.md §4). */
  price_low_paise?: number
  price_high_paise?: number
  margin?: number
  retry_probability?: number
  assumptions?: string[]
}

function rupees(paise?: number): string {
  if (paise == null || !Number.isFinite(paise)) return "—"
  const r = paise / 100
  return `₹${Number.isInteger(r) ? r.toLocaleString("en-IN") : r.toFixed(2)}`
}

/**
 * The estimate, shown as a range with its reasoning available.
 *
 * A single figure here would be the lie TokenCast measures: the same task varies up to 30× across runs. So the
 * range is the headline, "estimate, not a quote" is stated, and the assumptions open on demand — a number a reader
 * cannot argue with is a number they should not trust.
 */
function CostRange({ estimate }: { estimate: CostEstimate }) {
  const [open, setOpen] = useState(false)
  if (estimate.low_paise == null || estimate.high_paise == null) return null
  const margin = estimate.margin != null ? Math.round(estimate.margin * 100) : null
  const retry = estimate.retry_probability != null ? Math.round(estimate.retry_probability * 100) : null
  return (
    <div className="wc-cost">
      <p className="wc-kell">
        A run should cost <b>{rupees(estimate.low_paise)}–{rupees(estimate.high_paise)}</b> in models and tools.
      </p>
      {estimate.price_low_paise != null && (
        <p className="wc-kell">
          You pay <b>{rupees(estimate.price_low_paise)}–{rupees(estimate.price_high_paise)}</b> for a run
          {margin != null ? `, which covers ${margin}% margin and` : ","}
          {retry != null ? ` ${retry}% of steps running twice.` : " retries."}
        </p>
      )}
      <p className="wc-desc">
        An estimate, not a quote. The first run measures the real figure, and every run after that is priced from it.
      </p>
      {estimate.assumptions?.length ? (
        <>
          <button type="button" className="wc-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide the assumptions" : "How this is worked out"}
          </button>
          {open && (
            <ul className="wc-assume">
              {estimate.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  )
}

/** The Way's declared inputs. Nothing here is required unless the author said so; most Ways start with one press. */
function Inputs({ inputs, busy, onStart }: {
  inputs: WayInputDef[]
  busy: boolean
  onStart: (values: Record<string, string>) => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {}
    for (const i of inputs) if (i.default != null) seed[i.name] = i.default
    return seed
  })
  const missing = inputs.filter((i) => i.required && !(values[i.name] || "").trim() && i.default == null)

  return (
    <form
      className="wc-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (!missing.length) onStart(values)
      }}
    >
      {inputs.length === 0 ? (
        <p className="wc-kell">
          This Way needs nothing from you. Press run and it starts — its steps will ask if they need anything.
        </p>
      ) : (
        inputs.map((input) => (
          <label key={input.name} className="wc-field">
            <span className="wc-label">
              {input.name}
              {input.required ? <b> required</b> : <i> optional</i>}
            </span>
            {input.description && <span className="wc-desc">{input.description}</span>}
            {input.type === "choice" && input.choices?.length ? (
              <select
                value={values[input.name] ?? input.default ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [input.name]: e.target.value }))}
              >
                {!input.required && <option value="">— none —</option>}
                {input.choices.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
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
        ))
      )}

      <div className="wc-actions">
        <button type="submit" className="btn-ink" disabled={busy || missing.length > 0}>
          {busy ? "Starting…" : "Run this Way"}
        </button>
        {missing.length > 0 && <span className="wc-desc">Fill in {missing.map((m) => m.name).join(", ")}.</span>}
      </div>
    </form>
  )
}

/** One question. Blocks the run until answered, exactly like the tool it mirrors. */
function Question({ question, busy, onAnswer }: {
  question: WayQuestion
  busy: boolean
  onAnswer: (value: string | string[]) => void
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [free, setFree] = useState("")

  return (
    <div className="wc-q">
      {question.header && <span className="wc-tag">{question.header}</span>}
      <p className="wc-kell">{question.question}</p>
      <ul className="wc-options">
        {question.options.map((o) => (
          <li key={o.label}>
            <button
              type="button"
              className="wc-option"
              data-picked={(question.multiSelect ? picked.includes(o.label) : false) || undefined}
              disabled={busy}
              onClick={() => {
                if (!question.multiSelect) return onAnswer(o.label)
                setPicked((p) => (p.includes(o.label) ? p.filter((x) => x !== o.label) : [...p, o.label]))
              }}
            >
              <b>{o.label}</b>
              {o.description && <span>{o.description}</span>}
            </button>
          </li>
        ))}
      </ul>

      {question.multiSelect && (
        <button type="button" className="btn-ink" disabled={busy || picked.length === 0} onClick={() => onAnswer(picked)}>
          Continue with {picked.length || "none"} selected
        </button>
      )}

      {question.allowFreeText && (
        <form
          className="wc-free"
          onSubmit={(e) => {
            e.preventDefault()
            if (free.trim()) onAnswer(free.trim())
          }}
        >
          <input value={free} onChange={(e) => setFree(e.target.value)} placeholder="Or type an answer…" aria-label="Your answer" />
          <button type="submit" className="btn-line" disabled={busy || !free.trim()}>Send</button>
        </form>
      )}
    </div>
  )
}

/**
 * A step's output. Markdown-ish is NOT rendered — deliberately. Rendering arbitrary model output as HTML on a page
 * whose claim is "this is the record" would let a step inject markup into the evidence. It is shown as preformatted
 * text, selectable and copyable, which is what a person actually needs.
 */
/**
 * What the person actually receives: the files, each downloadable, plus the whole deliverable as one archive.
 *
 * A download is fetched with the session's bearer token and saved as a blob. A plain `<a href download>` cannot do
 * that — the server reads the token from `Authorization` only, never a query string — so an ordinary link would be a
 * 401 dressed as a broken download. Sizes are shown because "2 KB of markdown" and "14 MB of deck" differ.
 */
function Deliverable({ files, zip }: { files: Artifact[]; zip?: string }) {
  const [failed, setFailed] = useState<string | null>(null)
  const size = (n?: number) =>
    n == null ? "" : n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`

  async function save(url: string, name: string) {
    setFailed(null)
    try {
      await downloadArtifact(url, name)
    } catch (e) {
      setFailed((e as Error)?.message || "the download failed")
    }
  }

  return (
    <div className="wc-files">
      <p className="wc-kell">
        {files.length === 1 ? "Your file is ready." : `${files.length} files are ready.`}
      </p>
      <ul className="wc-filelist">
        {files.map((f) => (
          <li key={f.name}>
            <button type="button" className="wc-file" onClick={() => void save(f.url, f.name)}>
              <span className="wc-fname">{f.name}</span>
              <span className="wc-fsize">{size(f.size_bytes)}</span>
            </button>
          </li>
        ))}
      </ul>
      {zip && files.length > 1 && (
        <button type="button" className="wc-cta" onClick={() => void save(zip, "way-output.zip")}>
          Download all as a zip
        </button>
      )}
      {failed && <p className="wc-desc" data-tone="error">{failed}</p>}
    </div>
  )
}

function StepOutput({ text }: { text: string }) {
  const [open, setOpen] = useState(text.length <= 900)
  if (text.length <= 900) return <pre className="wc-out">{text}</pre>
  return (
    <>
      <pre className="wc-out" data-clamped={!open || undefined}>{open ? text : `${text.slice(0, 900)}…`}</pre>
      <button type="button" className="wc-more" onClick={() => setOpen((o) => !o)}>
        {open ? "Show less" : `Show all ${text.length.toLocaleString()} characters`}
      </button>
    </>
  )
}
