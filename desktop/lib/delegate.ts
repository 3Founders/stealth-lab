// The MCP/engine half of the delegation plan. The pure types and planning functions live in lib/plan.ts, so the page
// can import them without pulling node:fs (lib/mcp.ts) into the browser bundle.
import { callMcpTool } from "./mcp"
import { planFromRoute, stepsOf, toolOf, type Delegation, type Models, type Routed } from "./plan"

export * from "./plan"

/** Route the task with `find_ways` and read the Goal and every Way it offered. */
export async function routeTask(task: string, timeoutMs = 20_000): Promise<Routed> {
  const text = task.trim().slice(0, 4000)
  const found = await callMcpTool("find_ways", { query: text }, timeoutMs)
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
