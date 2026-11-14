// The pure half of the delegation plan: types, the model rung, the brief, the turn's contract, and the plan for a
// chosen Way. It has no imports from the server (no MCP, no engine), so the page can build the plan in the browser
// when the person picks a card; lib/delegate.ts adds the MCP lookup and the scoped engine call on top.
export type DelegationModel = { providerID: string; id: string }
/** One step of the Way, as the router returned it. The graph draws these in order. */
export type DelegationStep = {
  order: number
  title: string
  kind: string
  tool?: string
  server?: string
  note?: string
  sourceId?: string
}
export type Delegation = {
  task: string
  goal: { id: string; name: string } | null
  way: { id: string; name: string; approval: string } | null
  tool: { name: string; server: string } | null
  steps: DelegationStep[]
  model: DelegationModel | null
  rung: "strong" | "cheap" | null
  why: string
  brief: string
}

export type Models = { cheap?: string | null; strong?: string | null; fallback?: string | null }

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
export function toolOf(way: any): { name: string; server: string } | null {
  for (const step of Array.isArray(way?.steps) ? way.steps : []) {
    const b = step?.binding
    if (!b || typeof b !== "object") continue
    if (b.kind === "tool" || b.kind === "mcp") {
      return { name: String(b.tool || b.name || b.server || "tool"), server: String(b.server_url || b.server || "") }
    }
  }
  return null
}

/** The Way's steps in order, flattened for the procedure graph. A submitted Way's steps are sub-Ways; the
 *  backend adds their `title`, and until that deploy the graph fills the titles from the submission (app/api/procedure). */
export function stepsOf(way: any): DelegationStep[] {
  const steps = Array.isArray(way?.steps) ? way.steps : []
  return steps.map((step: any, i: number) => {
    const b = step?.binding && typeof step.binding === "object" ? step.binding : null
    const raw = String(step?.kind || (b ? "tool" : "prompt"))
    const kind = raw === "action" ? "tool" : raw === "subgoal" ? "step" : raw === "instruction" ? "prompt" : raw
    const order = Number(step?.order ?? i + 1) || i + 1
    return {
      order,
      title: String(step?.title || step?.do || step?.description || `Step ${order}`),
      kind,
      ...(b ? { tool: String(b.tool || b.name || ""), server: String(b.server_url || b.server || "") } : {}),
      ...(step?.note ? { note: String(step.note) } : {}),
      ...(step?.source_locator?.source_id ? { sourceId: String(step.source_locator.source_id) } : {}),
    }
  })
}

/** One Way the router offered. The panel shows these as cards; only the person's pick becomes the plan. */
export type WayOption = {
  id: string
  name: string
  approval: string
  review: string
  why: string
  tool: { name: string; server: string } | null
  steps: DelegationStep[]
}

/** What the router resolved, before a model is picked or a Way is chosen. */
export type Routed = { goal: Delegation["goal"]; options: WayOption[] }

/** Turn one chosen Way into the plan: the model rung, the reason, and the least-privilege brief. */
export function planFromOption(task: string, models: Models, goal: Delegation["goal"], option: WayOption | null): Delegation {
  const text = task.trim().slice(0, 4000)
  const picked = chooseModel(text, models, option ? { approval: option.approval } : null)
  const plan: Delegation = {
    task: text,
    goal,
    way: option ? { id: option.id, name: option.name, approval: option.approval } : null,
    tool: option?.tool ?? null,
    steps: option?.steps ?? [],
    model: picked.model,
    rung: picked.rung,
    why: picked.why,
    brief: "",
  }
  plan.brief = briefOf(text, plan)
  return plan
}

/** Turn a routed answer into the plan for its first Way (scripts and a one-shot caller; the desktop asks first). */
export function planFromRoute(task: string, models: Models, routed: Routed): Delegation {
  return planFromOption(task, models, routed.goal, routed.options[0] ?? null)
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
  // The Way runs on kell's executor (backend app/execution/way_runs.py): it walks the steps in order, feeds each output to
  // the next, runs scripts and plain fetches itself, and pauses for the person. The older one-tool contract is only the
  // fallback for a Way the executor refuses (not approved, or not a stored procedure).
  const run = plan.way.id ? [
    `${head}.`,
    `Run this Way step by step with kell_run_way(way_id="${plan.way.id}", input=<the person's task, in full>). First tell the person the reply's notice, word for word.`,
    "Then follow each reply: status waiting_session -> do your_turn.do yourself (your_turn.earlier holds what the steps before produced; a tool step goes through kell_use_tool), then kell_run_way(run_id=<run_id>, output=<the step's result, in full>), or failed=<why> if it cannot be done.",
    "Status waiting_human -> show the person waiting_on and wait for them; when they say it is done, kell_run_way(run_id=<run_id>, done=true, note=<what they did or found>). Status done -> give the person result. Status failed -> say why in one line and ask before doing anything else.",
    "Do the Way's steps only through the run: never do a step outside it, skip one, or run both.",
  ] : []
  const fallback = run.length ? "If kell_run_way refuses this Way, do this instead: " : ""
  if (!plan.tool) return [...run, `${fallback}${run.length ? "" : `${head}. `}No tool is bound to this Way: do the work yourself in this folder.`].join("\n")
  // `use_tool` names an outside server by its registry name (the Way's name for a tool_goals Way), not by URL.
  const server = plan.tool.server && !/^https?:/i.test(plan.tool.server)
    ? plan.tool.server
    : (plan.way.name || plan.tool.server)
  return [
    ...run,
    `${fallback}${head} -> tool "${plan.tool.name}"${plan.tool.server ? ` (${plan.tool.server})` : ""}.`,
    `That tool does this task's work: call kell_discover_tools(server="${server}") for its arguments if you do not have them, then kell_use_tool(server="${server}", name="${plan.tool.name}", arguments={...}).`,
    "Do not re-implement or pre-empt the tool's work with shell commands, scripts, direct HTTP calls or your own scan: never run both.",
    "If the tool fails or cannot do the job, say in one line what it could not do, then stop and ask the person. Never claim a check you did not run.",
  ].join("\n")
}
