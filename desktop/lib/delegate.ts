// The MCP/engine half of the delegation plan. The pure types and planning functions live in lib/plan.ts, so the page
// can import them without pulling node:fs (lib/mcp.ts) into the browser bundle.
import { callMcpTool } from "./mcp"
import { planFromRoute, stepsOf, toolOf, type Delegation, type Models, type Routed, type WayOption } from "./plan"

export * from "./plan"

/** find_ways' reply as the panel's shape: the Goal it resolved and every Way it offered. */
function fromFindWays(found: any): Routed {
  const goal = found?.goal && typeof found.goal === "object" ? found.goal : null
  const ways = Array.isArray(found?.procedures) ? found.procedures.slice(0, 6) : []
  const asGoal = goal ? { id: String(goal.goal_id || ""), name: String(goal.name || goal.canonical_name || "") } : null
  return {
    goal: asGoal,
    options: ways.map((way: any) => ({
      id: String(way.procedure_id || way.version_id || ""),
      name: String(way.name || ""),
      approval: String(way.verification_state || way.approval_status || "candidate"),
      review: String(way.approval_status || ""),
      why: String(way.why_chosen || ""),
      goal: asGoal ?? undefined,
      tool: toolOf(way),
      steps: stepsOf(way),
    })),
  }
}

/**
 * The Goal catalog's answer (`route_goal`) as the picker's shape. Ways can live under several Goals ("Research how
 * experts do it", "Write the research plan...", "Search the literature..."), and a task often matches more than one, so
 * every Goal with a written Way contributes; a Goal with no Way is not a runnable procedure and is skipped. The best
 * Goal sets the score floor, so a low-scoring Goal far down the catalog does not fill the strip with noise. Each Way
 * keeps its own Goal: picking it moves the graph to that Goal, not the first one in the answer (2026-11-16).
 */
function fromCatalog(answer: any, limit = 8): Routed {
  const goals = Array.isArray(answer?.goals) ? answer.goals : []
  const top = Number(goals[0]?.score ?? 0)
  const floor = Math.max(0.35, top * 0.5)
  const seen = new Set<string>()
  const options: WayOption[] = []
  let firstGoal: any = null
  for (const g of goals) {
    if (options.length >= limit) break
    const score = Number(g?.score ?? 0)
    const ways = Array.isArray(g?.ways) ? g.ways : []
    for (const way of ways) {
      if (options.length >= limit) break
      const id = String(way?.procedure_id || way?.version_id || "")
      if (!id || seen.has(id)) continue
      if (score < floor) continue
      seen.add(id)
      if (!firstGoal) firstGoal = g
      const card = Array.isArray(way?.cards) ? way.cards[0] : null
      options.push({
        id,
        name: String(way?.name || ""),
        approval: String(way?.verification_state || way?.approval_status || "candidate"),
        review: String(way?.approval_status || ""),
        why: String(way?.why_chosen || g?.description || g?.matched_by || ""),
        goal: g?.goal_id ? { id: String(g.goal_id), name: String(g.canonical_name || "") } : undefined,
        tool: toolOf(way) || (card?.name
          ? { name: String(card.name), server: String(card.server || card.connector_url || "") }
          : null),
        steps: stepsOf(way),
      })
    }
  }
  const named = firstGoal ?? goals[0]
  return {
    goal: named?.goal_id ? { id: String(named.goal_id), name: String(named.canonical_name || "") } : null,
    options,
  }
}

/**
 * Route the task: find_ways (the judged, repo-aware answer) and the Goal catalog run together. find_ways decides whether
 * knowledge helps and names the best Way; the catalog adds the other Ways that fit, so the picker is a real choice
 * rather than one card. A slow or refused find_ways never hides the catalog, and vice versa.
 */
export async function routeTask(task: string, timeoutMs = 20_000): Promise<Routed> {
  const text = task.trim().slice(0, 4000)
  let firstErr: any = null
  // A task that asks for procedures (or is about research) also reads the catalog for the literature/novelty side:
  // "research" alone does not rank "Search the literature and check novelty" high, and it is one of the Ways people
  // most often want next to the plan (2026-11-16). Both reads are cached by the catalog and run together.
  const researchAsk = /\b(research|literature|study|paper|novelty|procedure|procedures|ways?)\b/i.test(text)
  const catalogRead = (q: string) => callMcpTool("route_goal", { query: q, limit: 10 }, Math.min(timeoutMs, 12_000))
  const catalogP = Promise.allSettled([
    catalogRead(text),
    ...(researchAsk ? [catalogRead("research literature novelty")] : []),
  ]).then((results) => {
    const options: WayOption[] = []
    const seen = new Set<string>()
    let goal: Delegation["goal"] = null
    for (const r of results) {
      if (r.status !== "fulfilled") {
        if (!firstErr) firstErr = r.reason
        continue
      }
      const part = fromCatalog(r.value)
      if (!goal) goal = part.goal
      for (const o of part.options) {
        if (seen.has(o.id)) continue
        seen.add(o.id)
        options.push(o)
      }
    }
    return { goal, options: options.slice(0, 10) }
  })
  let routed: Routed = { goal: null, options: [] }
  try {
    routed = fromFindWays(await callMcpTool("find_ways", { query: text }, timeoutMs))
  } catch (err) { firstErr = err }
  const catalog = await catalogP
  if (catalog.options.length) {
    const seen = new Set(routed.options.map((o) => o.id))
    routed = {
      goal: routed.goal ?? catalog.goal,
      options: [...routed.options, ...catalog.options.filter((o) => !seen.has(o.id))].slice(0, 10),
    }
  }
  if (!routed.goal && !routed.options.length && firstErr) throw firstErr
  return routed
}

/** Route the task, pick the first unit, and write the brief. Throws only when kell's server cannot be reached. */
export async function planDelegation(task: string, models: Models, timeoutMs = 3500): Promise<Delegation> {
  return planFromRoute(task, models, await routeTask(task, timeoutMs))
}

/** The scoped one-shot: the brief and the question, no tools, no files. */
export async function askDelegated(plan: Delegation, question: string): Promise<{ answer: string; model: string | null }> {
  const { engineStatus, clientFor } = await import("./engine")
  const e = engineStatus()
  if (!e) throw Object.assign(new Error("the engine is down; open a project first"), { status: 502 })
  const prompt = `${plan.brief}\n\nQuestion from the person: ${question.trim().slice(0, 4000)}`
  const client = await clientFor(e)
  const out = await client.experimental.generate.text({ prompt, ...(plan.model ? { model: plan.model as any } : {}) } as any)
  const answer = String((out as any)?.data?.text ?? (out as any)?.text ?? "").trim()
  if (!answer) throw Object.assign(new Error("the delegated model returned no answer"), { status: 502 })
  return { answer, model: plan.model?.id ?? null }
}
