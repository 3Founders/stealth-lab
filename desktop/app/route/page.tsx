"use client"
// Route a task: the desktop face of the goal-first router (app/tool_routing/goal_router.py). It asks the MCP server the
// app is already signed in to for Goal -> Way -> Tool, and shows the answer. Read-only: nothing is called here.
import { useEffect, useRef, useState } from "react"

type Tool = {
  card_id: string; name: string; server: string; title: string; description: string
  source: string; score: number; rank_score?: number | null; trial?: boolean
}
type Way = { procedure_id: string; name: string; approval_status: string; cards: { card_id?: string }[] }
type Goal = {
  goal_id: string; canonical_name: string; description: string; matched_by: string
  score: number; ways: Way[]; tools: Tool[]
}
type Reply = { query: string; goals: Goal[]; tools?: Tool[]; error?: string }

const EXAMPLES = [
  "send a slack message",
  "open the page, log in and screenshot the dashboard",
  "find CVEs in my repository",
  "look up stripe invoices",
]

function ToolCard({ tool }: { tool: Tool }) {
  return (
    <div className="dk-card" style={{ gridTemplateColumns: "1fr", gap: 4, marginBottom: 6 }}>
      <span className="name">{tool.title || tool.name || tool.server}</span>
      <span className="meta">{tool.server || tool.source}</span>
      {tool.description && <span className="meta" style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{tool.description}</span>}
      <span className="meta" style={{ fontVariantNumeric: "tabular-nums" }}>
        {tool.rank_score != null ? `match ${Number(tool.rank_score).toFixed(3)}` : "stable order"}
        {Number(tool.score || 0) > 0 ? ` · goal fit ${Number(tool.score).toFixed(2)}` : ""}
        {tool.trial ? " · new, free trial" : ""}
      </span>
    </div>
  )
}

export default function RoutePage() {
  const [query, setQuery] = useState("")
  const [asked, setAsked] = useState("")
  const [reply, setReply] = useState<Reply | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const controller = useRef<AbortController | undefined>(undefined)

  async function load(term: string) {
    const text = term.trim()
    if (!text) return
    controller.current?.abort()
    const active = new AbortController()
    controller.current = active
    setAsked(text)
    setBusy(true)
    setError("")
    try {
      const r = await fetch(`/api/goals/route?q=${encodeURIComponent(text)}&k=5`, { signal: active.signal })
      const body = (await r.json()) as Reply
      if (controller.current !== active) return
      if (!r.ok) throw new Error(body?.error || `the router answered ${r.status}`)
      setReply(body)
    } catch (e: any) {
      if ((e as Error)?.name === "AbortError") return
      if (controller.current === active) { setReply(null); setError(String(e?.message || e)) }
    } finally {
      if (controller.current === active) setBusy(false)
    }
  }

  useEffect(() => () => controller.current?.abort(), [])

  return (
    <div style={{ minHeight: "100dvh", background: "var(--paper)", color: "var(--ink)", padding: "18px 20px 60px" }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, maxWidth: 1100, margin: "0 auto 18px" }}>
        <button type="button" className="btn-line" onClick={() => window.location.assign("/")}>← Back</button>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 400 }}>Route a task</h1>
        <span className="dk-note" style={{ margin: 0 }}>Goal → Way → Tool. Read-only; nothing is called.</span>
      </header>

      <main style={{ maxWidth: 1100, margin: "0 auto" }}>
        <form
          onSubmit={(e) => { e.preventDefault(); void load(query) }}
          style={{ display: "flex", gap: 8, alignItems: "center", borderBottom: "2px solid var(--ink)", paddingBottom: 8 }}
        >
          <input
            className="dk-field"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. send a slack message"
            autoComplete="off"
            aria-label="Describe a task"
            style={{ border: 0, background: "transparent", fontSize: 18, padding: "8px 0" }}
          />
          <button className="btn-ink" type="submit" disabled={busy}>{busy ? "Routing…" : "Route"}</button>
        </form>

        <div className="dk-row" style={{ margin: "10px 0 18px" }}>
          {EXAMPLES.map((example) => (
            <button key={example} type="button" className="btn-line" style={{ fontSize: 12.5, padding: "5px 10px" }}
              onClick={() => { setQuery(example); void load(example) }}>
              {example}
            </button>
          ))}
        </div>

        {busy && <p className="dk-note" role="status">Resolving the Goal, then ranking its ways and tools. The first search can take a few seconds.</p>}
        {error && <p className="dk-err">{error}</p>}

        {reply && !busy && (
          <div>
            {reply.goals.length === 0 && !(reply.tools && reply.tools.length) && (
              <p className="dk-note">Nothing matched “{asked}”. Try the task in other words.</p>
            )}

            {reply.goals.length === 0 && reply.tools && reply.tools.length > 0 && (
              <section style={{ marginBottom: 26 }}>
                <h2 style={{ fontSize: 15, fontWeight: 500, margin: "0 0 4px" }}>No Goal matched <span className="dk-note" style={{ margin: 0 }}>tools that may help</span></h2>
                <p className="dk-note" style={{ margin: "0 0 10px" }}>
                  The Goal tier has no category for this task yet. These are the tools the catalog found for the request.
                </p>
                {reply.tools.map((tool) => <ToolCard key={tool.card_id} tool={tool} />)}
              </section>
            )}

            {reply.goals.map((goal) => (
              <article key={goal.goal_id} style={{ borderTop: "1px solid var(--rule-strong)", padding: "18px 0 24px" }}>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <h2 style={{ fontSize: 18, fontWeight: 500, margin: 0 }}>{goal.canonical_name}</h2>
                  <span className="dk-note" style={{ margin: 0 }}>{goal.matched_by} · score {Number(goal.score || 0).toFixed(3)}</span>
                </div>
                {goal.description && <p className="dk-note" style={{ margin: "6px 0 0" }}>{goal.description}</p>}

                <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.15fr)", gap: 24, marginTop: 14 }}>
                  <section>
                    <h3 style={{ fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", fontWeight: 400, margin: "0 0 8px" }}>Ways</h3>
                    {goal.ways.length === 0 && <p className="dk-note" style={{ margin: 0 }}>No way is bound to this Goal yet.</p>}
                    {goal.ways.map((way) => (
                      <div key={way.procedure_id} className="dk-card" style={{ gridTemplateColumns: "1fr", gap: 4, marginBottom: 6 }}>
                        <span className="name">{way.name}</span>
                        <span className="meta" style={{ textTransform: "uppercase", letterSpacing: ".06em" }}>{way.approval_status}</span>
                        {way.cards?.length > 0 && (
                          <span className="meta" style={{ fontFamily: "Cascadia Mono, ui-monospace, Consolas, monospace", fontSize: 10.5 }}>
                            {way.cards.map((c) => c.card_id).filter(Boolean).join(" · ")}
                          </span>
                        )}
                      </div>
                    ))}
                  </section>
                  <section>
                    <h3 style={{ fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", fontWeight: 400, margin: "0 0 8px" }}>Tools</h3>
                    {goal.tools.length === 0 && <p className="dk-note" style={{ margin: 0 }}>No tool is mapped to this Goal yet.</p>}
                    {goal.tools.map((tool) => <ToolCard key={tool.card_id} tool={tool} />)}
                  </section>
                </div>
              </article>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
