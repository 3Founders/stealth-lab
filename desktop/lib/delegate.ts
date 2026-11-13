// The delegation plan for one prompt: the Goal and Way kell's router resolves, the tool the Way is bound to, which model in
// this app's ladder should run the turn, and the least-privilege brief a delegated worker is allowed to see.
//
// Source: the MCP tool `find_ways` (lib/mcp.ts), the same lookup the engine's plugin runs before a prompt. It answers with
// the Goal, its chosen Way and that Way's step bindings -- so a security task can name the OSV scanner, and a procedure
// bound to Hinoki names Hinoki. When a binding is a tool the app has not been given, the plan says so; nothing is invented.
//
// "The best model for the procedure" is chosen here from the Way's verification state plus the app's ladder (settings): a
// security/analysis task, or a Way nobody has verified yet, gets the strong rung; a proven Way on an ordinary task gets the
// cheap one. kell's per-cluster model router (docs/tool_marketplace_design.md) replaces `chooseModel` when it lands; the
// panel says which rule fired, so the answer is never a black box.
//
// Least privilege: `brief` carries the task, the Goal/Way and the one tool -- never the project path, files, shell or the
// chat history. The scoped side call (app/api/delegate/ask) sends exactly this and nothing else.
import { callMcpTool } from "./mcp"

export type DelegationModel = { providerID: string; id: string }
export type Delegation = {
  task: string
  goal: { id: string; name: string } | null
  way: { id: string; name: string; approval: string } | null
  tool: { name: string; server: string } | null
  model: DelegationModel | null
  rung: "strong" | "cheap" | null
  why: string
  brief: string
}

type Models = { cheap?: string | null; strong?: string | null; fallback?: string | null }

// Tasks where a wrong answer is expensive enough to pay for the strong rung.
const NEEDS_STRONG = /security|vulnerab|\bcve\b|exploit|threat|malware|incident|penetrat|audit|review|analy[sz]e|architect|migrat|refactor|debug|root cause|regulat|compliance|legal|medical|financ/i
const PROVEN = new Set(["verified", "approved", "ai_approved"])

/** Which model runs this turn, and the one sentence that says why. `null` model = the engine's own default. */
export function chooseModel(task: string, models: Models, way: { approval: string } | null) {
  const strong = models.strong || models.fallback || models.cheap || null
  const cheap = models.cheap || models.fallback || strong
  if (!strong && !cheap) return { model: null, rung: null, why: "no model list on this machine yet: the engine's default runs the turn" }
  const security = NEEDS_STRONG.test(task)
  const unproven = !!way && !PROVEN.has(way.approval)
  if (strong && (security || unproven)) {
    const why = security ? "a security/analysis task: the strong rung"
      : `the Way is "${way?.approval}" (not verified yet): the strong rung`
    return { model: { providerID: "kell", id: strong }, rung: "strong" as const, why }
  }
  return { model: cheap ? { providerID: "kell", id: cheap } : null, rung: cheap ? "cheap" as const : null,
           why: way ? `the Way is "${way.approval}": the cheap rung is enough` : "an ordinary task: the cheap rung" }
}

/** The brief a delegated worker may see. No project path, no files, no history -- only what the task needs. */
export function briefOf(task: string, plan: Pick<Delegation, "goal" | "way" | "tool">): string {
  const lines = [
    "You are a delegated worker inside kell's desktop app. You see this brief and nothing else:",
    "no project files, no shell, no chat history, no other tools.",
    "",
    `Task: ${task}`,
  ]
  if (plan.goal) lines.push(`kell's router resolved this Goal: "${plan.goal.name}"`)
  if (plan.way) lines.push(`Way: "${plan.way.name}" (${plan.way.approval})`)
  if (plan.tool) lines.push(`The Way is bound to the tool ${plan.tool.name} (${plan.tool.server})`)
  lines.push(
    "",
    "Answer with: (1) the result, (2) what you actually checked, (3) what you could not check and why.",
    "If the task needs something you do not have, name it and stop. Never claim a check you did not run.",
  )
  return lines.join("\n")
}

/** The step binding a Way actually runs: the first tool binding in the chosen procedure. */
function toolOf(way: any): { name: string; server: string } | null {
  for (const step of Array.isArray(way?.steps) ? way.steps : []) {
    const b = step?.binding
    if (!b || typeof b !== "object") continue
    if (b.kind === "tool" || b.kind === "mcp") {
      return { name: String(b.tool || b.name || b.server || "tool"), server: String(b.server_url || b.server || "") }
    }
  }
  return null
}

/** Route the task, pick the unit, and write the brief. Throws only when kell's server cannot be reached. */
export async function planDelegation(task: string, models: Models, timeoutMs = 3500): Promise<Delegation> {
  const text = task.trim().slice(0, 4000)
  const found = await callMcpTool("find_ways", { query: text }, timeoutMs)
  const goal = found?.goal && typeof found.goal === "object" ? found.goal : null
  const way = Array.isArray(found?.procedures) ? found.procedures[0] : null
  const tool = toolOf(way)
  const approval = String(way?.verification_state || way?.approval_status || "candidate")
  const picked = chooseModel(text, models, way ? { approval } : null)
  const plan: Delegation = {
    task: text,
    goal: goal ? { id: String(goal.goal_id || ""), name: String(goal.name || goal.canonical_name || "") } : null,
    way: way ? { id: String(way.procedure_id || way.version_id || ""), name: String(way.name || ""), approval } : null,
    tool,
    model: picked.model,
    rung: picked.rung,
    why: picked.why,
    brief: "",
  }
  plan.brief = briefOf(text, plan)
  return plan
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
