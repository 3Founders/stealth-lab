"use client"
/**
 * A Way running inside a Room, as a presentation.
 *
 * The main pane shows ONE thing at a time (docs/blinkit/presentation_rooms.md):
 *
 *   needs_inputs  -> only the inputs this Way asks for, a Start button, and nothing else
 *   ready         -> no inputs declared: a Start button and what the run will do
 *   running       -> step N of M, the current step's result, and the next one as it arrives
 *   done          -> the deliverable, then the Ways that are often done after this one
 *
 * It never starts by itself. A person who lands here has paid; they still decide when the run begins, because the
 * first press is the one that spends their output.
 *
 * Prompts are not editable. A person may change a step's *inputs* and restart from that step; the restart is priced
 * for the steps it re-runs, which is why the per-step estimate is shown before the button.
 */
import { useCallback, useEffect, useState } from "react"

import { MCP_ORIGIN } from "@/lib/connector"
import { getAccessToken } from "@/lib/session"
import { downloadArtifact } from "@/lib/api"

type InputDef = { step: number; step_title: string; key: string; label: string; required: boolean; help: string }
type StepDef = { order: number; title: string; price_low_paise: number; price_high_paise: number; is_human?: boolean }
type StepPrice = StepDef & { already_run?: boolean }
type RunView = {
  state: "needs_inputs" | "ready" | "running" | "done" | "failed" | "no_way" | "cancelled"
  run: { run_id: string; status: string } | null
  inputs: InputDef[]
}
type Artifact = { name: string; size_bytes?: number; url: string }
type Related = { id: string; goal_name: string; price_paise?: number | null }

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const token = await getAccessToken()
  const headers: Record<string, string> = { Accept: "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
  if (init?.body !== undefined) headers["Content-Type"] = "application/json"
  const res = await fetch(`${MCP_ORIGIN}${path}`, {
    method: init?.method || "GET",
    headers,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const msg = body?.error?.message || body?.error || `the server answered ${res.status}`
    throw new Error(String(msg))
  }
  return body as T
}

function rupees(paise?: number | null): string {
  if (paise == null || !Number.isFinite(paise)) return "—"
  const r = paise / 100
  return `₹${Number.isInteger(r) ? r.toLocaleString("en-IN") : r.toFixed(2)}`
}

export default function PresentRun({ roomId, wayId, layout = "steps" }: {
  roomId: string
  wayId: string
  /** "steps" is one screen at a time; "canvas" is the same run as a wall of cards. */
  layout?: "steps" | "canvas"
}) {
  const [view, setView] = useState<RunView | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [steps, setSteps] = useState<StepPrice[]>([])
  const [outputs, setOutputs] = useState<{ order: number; title: string; output: string }[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [related, setRelated] = useState<Related[]>([])
  const [restartFrom, setRestartFrom] = useState<number | null>(null)
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [lastCharge, setLastCharge] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await call<RunView>(`/v1/rooms/${encodeURIComponent(roomId)}/run`)
      setView(r)
      if (r.run?.run_id) {
        const full = await call<{ outputs?: typeof outputs; artifacts?: Artifact[] }>(
          `/v1/goals/runs/${encodeURIComponent(r.run.run_id)}`)
        setOutputs(full.outputs || [])
        setArtifacts(full.artifacts || [])
      }
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }, [roomId])

  useEffect(() => { void load() }, [load])

  // The per-step prices, so a restart can show what it costs before it is pressed.
  useEffect(() => {
    if (!wayId) return
    void call<{ steps: StepDef[] }>(`/v1/goals/submissions/${encodeURIComponent(wayId)}/steps/cost`)
      .then((r) => setSteps(r.steps || []))
      .catch(() => { /* the estimate is information; its absence must not break the run */ })
  }, [wayId])

  // Related Ways for the right rail, and the "often done next" list after the final output.
  useEffect(() => {
    if (!wayId) return
    void call<{ follows: Related[]; similar: Related[] }>(
      `/v1/goals/submissions/${encodeURIComponent(wayId)}/related`)
      .then((r) => setRelated([...(r.follows || []), ...(r.similar || [])]))
      .catch(() => { /* a rail that cannot read is empty, not broken */ })
  }, [wayId])

  async function start() {
    setBusy(true)
    setError(null)
    try {
      // The inputs are the point: they go to the run, not to a chat box. The server refuses if a required one is blank.
      await call(`/v1/rooms/${encodeURIComponent(roomId)}/run/start`, {
        method: "POST",
        body: { submission_id: wayId, values },
      })
      await load()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function restart() {
    if (restartFrom == null) return
    setBusy(true)
    setError(null)
    try {
      const out = await call<{ estimate: { price_low_paise: number; price_high_paise: number }; charge_paise: number }>(
        `/v1/rooms/${encodeURIComponent(roomId)}/run/restart`,
        { method: "POST", body: { from_step: restartFrom, edits } })
      setError(null)
      setRestartFrom(null)
      setEdits({})
      // Say what was charged. A restart the person cannot see the price of is the thing they would rightly object to.
      setLastCharge(out.charge_paise)
      setView({ ...(view as RunView), state: "running" })
      await load()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const tail = restartFrom == null ? null : steps.filter((s) => s.order >= restartFrom)
  const tailLow = tail?.reduce((n, s) => n + s.price_low_paise, 0) ?? 0
  const tailHigh = tail?.reduce((n, s) => n + s.price_high_paise, 0) ?? 0

  if (!view) {
    return <section className="pr"><p className="pr-muted" role="status">{error || "Loading the Way…"}</p></section>
  }

  if (view.state === "no_way") {
    return (
      <section className="pr">
        <p className="pr-muted">This room is not running a Way.</p>
      </section>
    )
  }

  return (
    <section className="pr" data-layout={layout} aria-label="Way run">
      {/* 1. The first screen is inputs, or a Start button. Nothing else competes with it. */}
      {view.state === "needs_inputs" && (
        <div className="pr-panel">
          <h2 className="pr-h">This Way needs a few things first</h2>
          <ul className="pr-inputs">
            {view.inputs.map((i) => (
              <li key={`${i.step}.${i.key}`}>
                <label htmlFor={`in-${i.step}-${i.key}`}>
                  {i.label}
                  <span className="pr-which">step {i.step}: {i.step_title}</span>
                </label>
                <input
                  id={`in-${i.step}-${i.key}`}
                  value={values[`${i.step}.${i.key}`] || ""}
                  onChange={(e) => setValues((v) => ({ ...v, [`${i.step}.${i.key}`]: e.target.value }))}
                  required={i.required}
                />
                {i.help && <span className="pr-help">{i.help}</span>}
              </li>
            ))}
          </ul>
          <button type="button" className="pr-cta" disabled={busy} onClick={() => void start()}>
            {busy ? "Starting…" : "Start"}
          </button>
          {error && <p className="pr-err">{error}</p>}
        </div>
      )}

      {view.state === "ready" && (
        <div className="pr-panel">
          <h2 className="pr-h">Ready to run</h2>
          <ol className="pr-plan">
            {steps.map((s) => (
              <li key={s.order}>
                <span className="pr-n">{s.order}</span>
                <span>{s.title}</span>
                <span className="pr-price">{rupees(s.price_low_paise)}–{rupees(s.price_high_paise)}</span>
              </li>
            ))}
          </ol>
          <button type="button" className="pr-cta" disabled={busy} onClick={() => void start()}>
            {busy ? "Starting…" : "Start"}
          </button>
          {error && <p className="pr-err">{error}</p>}
        </div>
      )}

      {/* 2. While it runs: one step at a time, and what each one produced. */}
      {(view.state === "running" || view.state === "done" || view.state === "failed") && (
        <div className="pr-panel">
          {lastCharge != null && (
            <p className="pr-charge" role="status">
              Charged <b>{rupees(lastCharge)}</b> for the steps this restart re-ran.
            </p>
          )}
          <p className="pr-progress">
            {outputs.length} of {steps.length || "?"} steps done
          </p>
          <ol className="pr-steps">
            {outputs.map((o) => (
              <li key={o.order} className="pr-step">
                <header>
                  <span className="pr-n">{o.order}</span>
                  <b>{o.title}</b>
                  <button
                    type="button"
                    className="pr-small"
                    onClick={() => setRestartFrom(restartFrom === o.order ? null : o.order)}
                  >
                    {restartFrom === o.order ? "Cancel" : "Redo from here"}
                  </button>
                </header>
                <pre className="pr-out">{o.output}</pre>
              </li>
            ))}
          </ol>

          {restartFrom != null && (
            <div className="pr-restart">
              <h3>Redo from step {restartFrom}</h3>
              <p className="pr-muted">
                Only steps {restartFrom}–{steps.length || "?"} run again. This costs about{" "}
                <b>{rupees(tailLow)}–{rupees(tailHigh)}</b> and is charged for those steps, not the whole Way.
              </p>
              <label>
                What should change?
                <textarea
                  value={edits[`${restartFrom}.note`] || ""}
                  onChange={(e) => setEdits({ [`${restartFrom}.note`]: e.target.value })}
                  rows={3}
                  placeholder="A change is required: re-running the same steps gives the same result."
                />
              </label>
              <button
                type="button"
                className="pr-cta"
                disabled={busy || !(edits[`${restartFrom}.note`] || "").trim()}
                onClick={() => void restart()}
              >
                {busy ? "Restarting…" : `Redo and pay ${rupees(tailHigh)}`}
              </button>
            </div>
          )}

          {artifacts.length > 0 && (
            <div className="pr-files">
              <h3>Output</h3>
              <ul>
                {artifacts.map((f) => (
                  <li key={f.name}>
                    <button type="button" className="pr-file" onClick={() => void downloadArtifact(f.url, f.name)}>
                      {f.name}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {view.state === "done" && related.length > 0 && (
            <div className="pr-after">
              <h3>Often done after this</h3>
              <ul>
                {related.slice(0, 4).map((r) => (
                  <li key={r.id}>
                    <a href={`/way/${encodeURIComponent(r.id)}`}>{r.goal_name}</a>
                    {r.price_paise != null && <span className="pr-price">{rupees(r.price_paise)}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && <p className="pr-err">{error}</p>}
        </div>
      )}
    </section>
  )
}
