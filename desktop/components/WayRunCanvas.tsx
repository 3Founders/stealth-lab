"use client"
// A Way's run, as a canvas in the desktop: the same five things the website allows, laid out as a column of cards
// for a pane rather than as chat bubbles (app/submissions/way_inputs.py is the shared shape).
//
// It reads and writes the same routes the website does, through this app's own Rooms sign-in (lib/mcp.ts's
// callWebRoute -> lib/submissions-client). Nothing here is a second implementation of a run: it starts one with
// POST /v1/goals/submissions/{id}/runs and answers a paused step with POST /v1/goals/runs/{id}/done.
import { useEffect, useRef, useState } from "react"
import { submissionsApi } from "@/lib/submissions-client"

type Option = { label: string; description?: string }
type Question = { key: string; question: string; header?: string; options: Option[]; multiSelect?: boolean; allowFreeText?: boolean }
type RunReply = {
  id?: string
  status?: string
  outputs?: { order: number; title: string; output: string }[]
  /** The plan, with what each step reads and writes (docs/steps_sandboxes_filesystems.md). */
  steps?: { n: number; title: string; produces?: string[] | string; imports?: string[] }[]
  /** The files this run produced; `url` is licence-gated (docs/procedure_outputs.md). */
  artifacts?: { name: string; size_bytes?: number; url: string }[]
  download_all?: string
  artifacts_refused?: { name: string; why: string }[]
  question?: Question | null
}
type Turn =
  | { kind: "you"; text: string }
  | { kind: "step"; order: number; title: string; output: string; reads?: string[]; writes?: string[] }
  | { kind: "files"; files: { name: string; size_bytes?: number; url: string }[]; zip?: string }
  | { kind: "note"; text: string; tone?: "error" | "done" }

export default function WayRunCanvas({ submissionId }: { submissionId: string }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState<Question | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState("")
  const endOfLog = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endOfLog.current?.scrollIntoView({ block: "end", behavior: "smooth" })
  }, [turns.length, question?.key])

  /** Fold a reply in without repeating turns already shown. */
  function absorb(reply: RunReply) {
    if (reply.id) setRunId(reply.id)
    setQuestion(reply.question ?? null)
    setTurns((prev) => {
      const next = [...prev]
      const shown = new Set(next.filter((t) => t.kind === "step").map((t) => (t as { order: number }).order))
      for (const o of reply.outputs ?? []) {
        if (!shown.has(o.order)) {
          const plan = reply.steps?.find((s) => s.n === o.order)
          next.push({
            kind: "step", order: o.order, title: o.title, output: o.output,
            reads: plan?.imports ?? undefined,
            writes: plan?.produces ? (Array.isArray(plan.produces) ? plan.produces : [plan.produces]) : undefined,
          })
        }
      }
      if (reply.status === "done") {
        // The files come before "Done.": they are the deliverable, and a bare "Done." on an empty pane is the bug.
        if (reply.artifacts?.length) {
          next.push({ kind: "files", files: reply.artifacts, zip: reply.download_all })
        }
        const last = next[next.length - 1]
        if (!(last?.kind === "note" && last.tone === "done")) next.push({ kind: "note", text: "Done.", tone: "done" })
      }
      for (const r of reply.artifacts_refused ?? []) {
        next.push({ kind: "note", text: `${r.name} was not kept: ${r.why}` })
      }
      return next
    })
  }

  function fail(e: unknown) {
    setTurns((t) => [...t, { kind: "note", tone: "error", text: String((e as Error)?.message || e) }])
  }

  async function start() {
    setBusy(true)
    if (input.trim()) setTurns((t) => [...t, { kind: "you", text: input.trim() }])
    try {
      absorb((await submissionsApi.run(submissionId, input)) as unknown as RunReply)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  async function answer(value: string) {
    if (!runId) return
    setTurns((t) => [...t, { kind: "you", text: value }])
    setBusy(true)
    setQuestion(null)
    try {
      absorb((await submissionsApi.done(runId, value)) as unknown as RunReply)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    // Resume a run this pane started, if the app was reopened: the run lives on the server, not here.
    void (async () => {
      try {
        const rows = (await submissionsApi.runs()) as unknown as { run_id: string; way_id: string }[]
        const latest = rows.find((x) => x.way_id === submissionId)
        if (latest?.run_id) {
          const full = (await submissionsApi.getRun(latest.run_id)) as unknown as RunReply
          if (full) absorb(full)
        }
      } catch { /* nothing to resume */ }
    })()
  }, [submissionId])

  return (
    <section className="dkw-canvas" aria-label="Run this Way">
      <ol className="dkw-log">
        {turns.map((t, i) => (
          <li key={i} className="dkw-turn" data-who={t.kind === "you" ? "you" : "kell"}>
            {t.kind === "step" ? (
              <article className="dkw-stepcard">
                <header><span className="dkw-stepn">{t.order}</span><b>{t.title}</b></header>
                {(t.reads?.length || t.writes?.length) ? (
                  <p className="dkw-io">
                    {t.reads?.length ? `read ${t.reads.join(", ")}` : ""}
                    {t.reads?.length && t.writes?.length ? " → " : ""}
                    {t.writes?.length ? `wrote ${t.writes.join(", ")}` : ""}
                  </p>
                ) : null}
                <pre className="dkw-out">{t.output}</pre>
              </article>
            ) : t.kind === "files" ? (
              <div className="dkw-files">
                <p className="dkw-note">{t.files.length === 1 ? "Your file is ready." : `${t.files.length} files are ready.`}</p>
                <ul className="dkw-filelist">
                  {t.files.map((f) => (
                    <li key={f.name}>
                      <a
                        className="dkw-file"
                        href={`/api/way-artifact?run=${encodeURIComponent(runId || "")}&name=${encodeURIComponent(f.name)}`}
                        download={f.name}
                      >
                        <span className="dkw-fname">{f.name}</span>
                        <span className="dkw-fsize">{f.size_bytes ? `${Math.round(f.size_bytes / 1024)} KB` : ""}</span>
                      </a>
                    </li>
                  ))}
                </ul>
                {t.zip && t.files.length > 1 && (
                  <a className="dkw-cta" href={`/api/way-artifact?run=${encodeURIComponent(runId || "")}`} download>
                    Download all as a zip
                  </a>
                )}
              </div>
            ) : (
              <p className={t.kind === "you" ? "dkw-you" : "dkw-note"} data-tone={t.kind === "note" ? t.tone : undefined}>
                {t.text}
              </p>
            )}
          </li>
        ))}

        {question && (
          <li className="dkw-turn" data-who="kell">
            {question.header && <span className="dkw-tag">{question.header}</span>}
            <p className="dkw-note">{question.question}</p>
            <div className="dkw-options">
              {question.options.map((o) => (
                <button key={o.label} type="button" className="dkw-option" disabled={busy} onClick={() => void answer(o.label)}>
                  <b>{o.label}</b>
                  {o.description && <span>{o.description}</span>}
                </button>
              ))}
            </div>
          </li>
        )}

        {!runId && (
          <li className="dkw-turn" data-who="kell">
            <form
              className="dkw-start"
              onSubmit={(e) => {
                e.preventDefault()
                void start()
              }}
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Anything this Way should know first? (optional)"
                aria-label="Input for this run"
              />
              <button type="submit" className="dkw-cta" disabled={busy}>{busy ? "Starting…" : "Run this Way"}</button>
            </form>
          </li>
        )}
        <div ref={endOfLog} />
      </ol>
    </section>
  )
}
