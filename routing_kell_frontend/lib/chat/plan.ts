import { anyPlanActive, plainDate, type Loaded, type PlanState } from "@/lib/billing";

const PLAN_LABEL = { starter: "Starter", pro: "Pro", plus: "Plus", pilot: "Pilot" } as const;

/** The line under your name in the profile: the plan you are on and when it ends, or that you have none. Unknown (still loading) is an
 * empty string, so nothing wrong is shown. */
export function planLine(p: PlanState | null | undefined, now = new Date()): string {
  if (!p) return "";
  if (p.plan && anyPlanActive(p, now)) return `${PLAN_LABEL[p.plan]} plan${p.activeUntil ? ` · until ${plainDate(p.activeUntil)}` : ""}`;
  if (p.plan) return `${PLAN_LABEL[p.plan]} plan ended`;
  return "No plan yet";
}

/** What to show for every outcome of asking the server for the plan, so a failure is never a silent blank. */
export function planText(r: Loaded<PlanState> | null | undefined, now = new Date()): string {
  if (!r) return "Checking your plan…";
  if (r.kind === "ok") return planLine(r.data, now);
  if (r.kind === "off") return "Plans aren't switched on yet";
  if (r.kind === "signed-out") return "Sign in to see your plan";
  return "Couldn't load your plan";
}
