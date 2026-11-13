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

/** What the router resolved, before a model is picked. Split out so the route can report it as a status update. */
export type Routed = { goal: Delegation["goal"]; way: Delegation["way"]; tool: Delegation["tool"] }

/** Route the task with `find_ways` and read the Goal, the chosen Way and its bound tool. */
export async function routeTask(task: string, timeoutMs = 20_000): Promise<Routed> {
  const text = task.trim().slice(0, 4000)
  const found = await callMcpTool("find_ways", { query: text }, timeoutMs)
  const goal = found?.goal && typeof found.goal === "object" ? found.goal : null
  const way = Array.isArray(found?.procedures) ? found.procedures[0] : null
  const approval = String(way?.verification_state || way?.approval_status || "candidate")
  return {
    goal: goal ? { id: String(goal.goal_id || ""), name: String(goal.name || goal.canonical_name || "") } : null,
    way: way ? { id: String(way.procedure_id || way.version_id || ""), name: String(way.name || ""), approval } : null,
    tool: toolOf(way),
  }
}

/** Turn a routed answer into the plan: the model rung, the reason, and the least-privilege brief. */
export function planFromRoute(task: string, models: Models, routed: Routed): Delegation {
  const text = task.trim().slice(0, 4000)
  const picked = chooseModel(text, models, routed.way ? { approval: routed.way.approval } : null)
  const plan: Delegation = {
    task: text,
    goal: routed.goal,
    way: routed.way,
    tool: routed.tool,
    model: picked.model,
    rung: picked.rung,
    why: picked.why,
    brief: "",
  }
  plan.brief = briefOf(text, plan)
  return plan
}

/** Route the task, pick the unit, and write the brief. Throws only when kell's server cannot be reached. */
export async function planDelegation(task: string, models: Models, timeoutMs = 3500): Promise<Delegation> {
  return planFromRoute(task, models, await routeTask(task, timeoutMs))
}

/**
 * What the turn must do with the routed Way: the Way's tool does the work, and the agent must not do the same work
 * another way. Empty when the router resolved no Way (then it is an ordinary coding turn).
 *
 * Why this is in the prompt and not only in the panel: the plugin's tool note is advisory, and a model that finds a
 * convenient HTTP endpoint will scan 15 lockfiles itself while the panel says the OSV Way ran (2026-11-13). The turn
 * gets the contract in the user message, so "do not run both" is part of the request it answers.
 */
export function contractOf(plan: Pick<Delegation, "goal" | "way" | "tool"> | null | undefined): string {
  if (!plan?.way) return ""
  const head = `kell delegation for this turn: Goal "${plan.goal?.name || "none"}" -> Way "${plan.way.name}" [${plan.way.approval}]`
  if (!plan.tool) return `${head}. No tool is bound to this Way: do the work yourself in this folder.`
  // `use_tool` names an outside server by its registry name (the Way's name for a tool_goals Way), not by URL.
  const server = plan.tool.server && !/^https?:/i.test(plan.tool.server)
    ? plan.tool.server
    : (plan.way.name || plan.tool.server)
  return [
    `${head} -> tool "${plan.tool.name}"${plan.tool.server ? ` (${plan.tool.server})` : ""}.`,
    `That tool does this task's work: call kell_discover_tools(server="${server}") for its arguments if you do not have them, then kell_use_tool(server="${server}", name="${plan.tool.name}", arguments={...}).`,
    "Do not re-implement or pre-empt the tool's work with shell commands, scripts, direct HTTP calls or your own scan: never run both.",
    "If the tool fails or cannot do the job, say in one line what it could not do, then stop and ask the person. Never claim a check you did not run.",
  ].join("\n")
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
