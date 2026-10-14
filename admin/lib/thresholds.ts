/**
 * Display thresholds: what this dashboard flags as "watch" or "action". They are a viewing preference, kept in this
 * browser per organisation, and they change only what the page highlights. They do NOT change what the backend enforces
 * (budgets, the kill switch, policy), which is set under Policy. Invalid or out-of-order values are rejected, so a typo
 * can't silently turn the warnings off.
 */
import { DEFAULT_THRESHOLDS, type Thresholds } from "@/lib/exec";

const key = (orgId: string) => `kel-admin-thresholds:${orgId}`;

/** Returns the thresholds if they are sensible, else null. */
export function validateThresholds(t: Partial<Thresholds> | null | undefined): Thresholds | null {
  if (!t) return null;
  const { failureWatch, failureAction, budgetWarnPct, budgetUrgentPct } = t;
  const nums = [failureWatch, failureAction, budgetWarnPct, budgetUrgentPct];
  if (nums.some((n) => typeof n !== "number" || !Number.isFinite(n))) return null;
  if (!(failureWatch! > 0 && failureWatch! < failureAction! && failureAction! <= 1)) return null;
  if (!(budgetWarnPct! > 0 && budgetWarnPct! < budgetUrgentPct! && budgetUrgentPct! < 100)) return null;
  return { failureWatch: failureWatch!, failureAction: failureAction!, budgetWarnPct: budgetWarnPct!, budgetUrgentPct: budgetUrgentPct! };
}

export function loadThresholds(orgId: string): Thresholds {
  try {
    const raw = window.localStorage.getItem(key(orgId));
    return (raw && validateThresholds(JSON.parse(raw))) || DEFAULT_THRESHOLDS;
  } catch {
    return DEFAULT_THRESHOLDS; // storage blocked or corrupt: fall back to the defaults
  }
}

export function saveThresholds(orgId: string, t: Thresholds): boolean {
  if (!validateThresholds(t)) return false;
  try { window.localStorage.setItem(key(orgId), JSON.stringify(t)); return true; } catch { return false; }
}

export function resetThresholds(orgId: string): void {
  try { window.localStorage.removeItem(key(orgId)); } catch { /* ignore */ }
}

export const sameThresholds = (a: Thresholds, b: Thresholds) =>
  a.failureWatch === b.failureWatch && a.failureAction === b.failureAction && a.budgetWarnPct === b.budgetWarnPct && a.budgetUrgentPct === b.budgetUrgentPct;
