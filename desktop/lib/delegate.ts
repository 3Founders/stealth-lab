// The MCP/engine half of the delegation plan. The pure types and planning functions live in lib/plan.ts, so the page
// can import them without pulling node:fs (lib/mcp.ts) into the browser bundle.
import { callMcpTool } from "./mcp"
import { planFromRoute, stepsOf, toolOf, type Delegation, type Models, type Routed, type WayOption } from "./plan"

export * from "./plan"

/** find_ways' reply as the panel's shape: the Goal it resolved and every Way it offered. */
function fromFindWays(found: any): Routed {
  const goal = found?.goal && typeof found.goal === "object" ? found.goal : null
  const ways = Array.isArray(found?.procedures) ? found.procedures.slice(0, 6) : []
  return {
    goal: goal ? { id: String(goal.goal_id || ""), name: String(goal.name || goal.canonical_name || "") } : null,
    options: ways.map((way: any) => ({
      id: String(way.procedure_id || way.version_id || ""),
      name: String(way.name || ""),
      approval: String(way.verification_state || way.approval_status || "candidate"),
      review: String(way.approval_status || ""),
      why: String(way.why_chosen || ""),
      tool: toolOf(way),
      steps: stepsOf(way),
    })),
  }
}

/**
 * The Goal catalog's answer (`GET /v1/goals/route`) as the same shape. That route exists to answer "which Goal and Ways
 * fit this task" (app/tool_routing/route_api.py); find_ways first judges whether knowledge helps at all, and for an
 * ordinary task ("search the web for X") it says `not_needed`, so the panel had nothing to show (2026-11-16). A Way's
 * bound card is the tool when its steps do not carry a binding.
 */
function fromCatalog(answer: any): Routed {
  const goals = Array.isArray(answer?.goals) ? answer.goals : []
  // The best Goal that actually has a written Way; else the best Goal, so the graph can at least name it.
  const goal = goals.find((g: any) => Array.isArray(g?.ways) && g.ways.length) || goals[0] || null
  const ways = Array.isArray(goal?.ways) ? goal.ways.slice(0, 6) : []
  return {
    goal: goal?.goal_id ? { id: String(goal.goal_id), name: String(goal.canonical_name || "") } : null,
    options: ways.map((way: any) => {
      const card = Array.isArray(way?.cards) ? way.cards[0] : null
      return {
        id: String(way?.procedure_id || ""),
        name: String(way?.name || ""),
        approval: String(way?.verification_state || way?.approval_status || "candidate"),
        review: String(way?.approval_status || ""),
        why: String(goal?.description || goal?.matched_by || ""),
        tool: toolOf(way) || (card?.name
          ? { name: String(card.name), server: String(card.server || card.connector_url || "") }
          : null),
        steps: stepsOf(way),
      }
    }),
  }
}

/** Route the task: find_ways (judged Goal + repo knowledge), then the Goal catalog when it offers nothing. */
export async function routeTask(task: string, timeoutMs = 20_000): Promise<Routed> {
  const text = task.trim().slice(0, 4000)
  let firstErr: any = null
  let routed: Routed = { goal: null, options: [] }
  try {
    routed = fromFindWays(await callMcpTool("find_ways", { query: text }, timeoutMs))
  } catch (err) { firstErr = err }
  if (!routed.goal && !routed.options.length) {
    try {
      routed = fromCatalog(await callMcpTool("route_goal", { query: text, limit: 5 }, Math.min(timeoutMs, 12_000)))
    } catch (err) {
      throw firstErr || err
    }
  }
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
