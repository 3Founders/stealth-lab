import { anyPlanActive, plainDate, type PlanState } from "@/lib/billing";

const PLAN_LABEL = { starter: "Starter", pro: "Pro", plus: "Plus", pilot: "Pilot" } as const;

/** The line under your name in the profile: the plan you are on and when it ends, or that you have none. Unknown (still loading, or
 * billing is off) is an empty string, so nothing wrong is shown. */
export function planLine(p: PlanState | null | undefined, now = new Date()): string {
  if (!p) return "";
  if (p.plan && anyPlanActive(p, now)) return `${PLAN_LABEL[p.plan]} plan${p.activeUntil ? ` · until ${plainDate(p.activeUntil)}` : ""}`;
  if (p.plan) return `${PLAN_LABEL[p.plan]} plan ended`;
  return "No plan yet";
}
