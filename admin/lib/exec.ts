/**
 * Logic behind the executive view. Everything an executive reads as a verdict or a headline number is derived here, in
 * plain functions with tests, from data the backend already reported. Nothing is estimated: where a number can't be
 * known (value delivered, savings) the view says so rather than inventing one.
 */
import { ZERO, type Micros } from "@/lib/money";
import type { UserUsageRow } from "@/lib/org-api";
import { dayOf, type Totals } from "@/lib/usage";

/** Failure-rate thresholds the verdict uses. Defaults, shown to the reader, not business rules the backend enforces. */
export const FAILURE_WATCH = 0.05;
export const FAILURE_ACTION = 0.15;

/** What counts as worth flagging. Defaults here; an organisation's admin can change them for their own view (lib/thresholds.ts). */
export interface Thresholds { failureWatch: number; failureAction: number; budgetWarnPct: number; budgetUrgentPct: number }
export const DEFAULT_THRESHOLDS: Thresholds = { failureWatch: FAILURE_WATCH, failureAction: FAILURE_ACTION, budgetWarnPct: 75, budgetUrgentPct: 90 };

export type Level = "good" | "watch" | "action" | "quiet";
export interface Reason { level: "watch" | "action"; text: string }
export interface Verdict { level: Level; headline: string; reasons: Reason[] }

export interface ExecSignals {
  /** Calls in the period; 0 means there is nothing to judge. */
  calls: number;
  killSwitch?: boolean | null;
  noPolicy?: boolean;
  budget: Micros | null;
  monthToDate: Micros;
  projected: Micros;
  failureRate: number | null;
  unpricedCalls: number;
  spikeDays: number;
  usersAtCap: number;
  /** null/undefined = not checked (source unavailable); false = the hash chain failed verification. */
  auditIntact?: boolean | null;
}

const HEADLINES: Record<Level, string> = { good: "On track", watch: "Needs attention", action: "Action needed", quiet: "No activity yet" };

/** Overall status in one line, plus every reason behind it. The worst reason sets the level. */
export function verdict(s: ExecSignals, t: Thresholds = DEFAULT_THRESHOLDS): Verdict {
  const reasons: Reason[] = [];
  const add = (level: Reason["level"], text: string) => reasons.push({ level, text });

  if (s.killSwitch) add("action", "The emergency stop (kill switch) is on, so every model call is being refused.");
  if (s.auditIntact === false) add("action", "The audit log failed its integrity check. Treat it as untrusted until investigated.");
  if (s.budget != null) {
    if (s.budget <= 0n ? s.monthToDate > 0n : s.monthToDate >= s.budget) add("action", "Spending has reached the monthly budget.");
    else if (s.budget > 0n && s.projected > s.budget) add("watch", "At the current pace, spending will pass the monthly budget before month end.");
    else if (s.budget > 0n) {
      const used = Number((s.monthToDate * 1000n) / s.budget) / 10;
      if (used >= t.budgetUrgentPct) add("watch", `${used.toFixed(0)}% of the monthly budget is used: nearly at the limit.`);
      else if (used >= t.budgetWarnPct) add("watch", `${used.toFixed(0)}% of the monthly budget is already used.`);
    }
  }
  if (s.failureRate != null) {
    if (s.failureRate >= t.failureAction) add("action", `${(s.failureRate * 100).toFixed(1)}% of requests are failing (action threshold ${+(t.failureAction * 100).toFixed(1)}%).`);
    else if (s.failureRate >= t.failureWatch) add("watch", `${(s.failureRate * 100).toFixed(1)}% of requests are failing (watch threshold ${+(t.failureWatch * 100).toFixed(1)}%).`);
  }
  if (s.noPolicy) add("watch", "No usage policy is set, so nothing is currently allowed.");
  if (s.spikeDays > 0) add("watch", `Spending was unusually high on ${s.spikeDays} day${s.spikeDays === 1 ? "" : "s"}.`);
  if (s.usersAtCap > 0) add("watch", `${s.usersAtCap} ${s.usersAtCap === 1 ? "person has" : "people have"} hit today's personal spending cap.`);
  if (s.unpricedCalls > 0) add("watch", `${s.unpricedCalls} request${s.unpricedCalls === 1 ? "" : "s"} have unknown cost; spend shown is a worst-case figure for those.`);

  const level: Level = reasons.some((r) => r.level === "action") ? "action" : reasons.length > 0 ? "watch" : s.calls === 0 ? "quiet" : "good";
  return { level, headline: HEADLINES[level], reasons: reasons.sort((a, b) => (a.level === b.level ? 0 : a.level === "action" ? -1 : 1)) };
}

/** Share of requests that succeeded, 0..1. Null when there were no requests (so the view says "n/a", not 100%). */
export const successRate = (calls: number, failed: number): number | null => (calls > 0 ? Math.max(0, (calls - failed) / calls) : null);
export const failureRate = (calls: number, failed: number): number | null => (calls > 0 ? Math.min(1, failed / calls) : null);

/** Average cost of one request in micro-dollars (rounded down). Null when there were no requests. */
export const costPerRequest = (total: Micros, calls: number): Micros | null => (calls > 0 ? total / BigInt(Math.round(calls)) : null);

/** People who made at least one request in the rows. */
export function activeUsers(rows: UserUsageRow[]): number {
  const s = new Set<string>();
  for (const r of rows) if ((Number(r.calls) || 0) > 0) s.add(r.actor_subject);
  return s.size;
}

/** Distinct active people per UTC day, oldest first. */
export function activeUsersByDay(rows: UserUsageRow[]): { day: string; value: number }[] {
  const m = new Map<string, Set<string>>();
  for (const r of rows) {
    if ((Number(r.calls) || 0) <= 0) continue;
    const d = String(r.day).slice(0, 10);
    (m.get(d) ?? m.set(d, new Set()).get(d)!).add(r.actor_subject);
  }
  return [...m.entries()].map(([day, set]) => ({ day, value: set.size })).sort((a, b) => a.day.localeCompare(b.day));
}

/** Share of members who were active. Null with no members (the denominator is unknown). */
export const adoptionRate = (active: number, members: number): number | null => (members > 0 ? Math.min(1, active / members) : null);

/** Failure rate per UTC day from day-grouped totals. Days with no requests are omitted rather than shown as 0%. */
export function failureByDay(days: Totals[]): { day: string; value: number }[] {
  return days.filter((t) => t.calls > 0).map((t) => ({ day: t.key, value: t.failed / t.calls })).sort((a, b) => a.day.localeCompare(b.day));
}

export interface Share { key: string; amount: Micros; pct: number; /** A head-count, shown instead of dollars (adoption depth). */ count?: number }

/** Top `n` groups by spend with the remainder rolled into one "Everything else" row. Percentages sum to 100. */
export function topShares(groups: Totals[], n: number): Share[] {
  const total = groups.reduce((s, g) => s + g.total, ZERO);
  if (total <= 0n) return [];
  const sorted = [...groups].sort((a, b) => (b.total > a.total ? 1 : b.total < a.total ? -1 : a.key.localeCompare(b.key)));
  const pct = (v: Micros) => Number((v * 10_000n) / total) / 100;
  const head = sorted.slice(0, n).filter((g) => g.total > 0n).map((g) => ({ key: g.key, amount: g.total, pct: pct(g.total) }));
  const rest = sorted.slice(n).reduce((s, g) => s + g.total, ZERO);
  return rest > 0n ? [...head, { key: "Everything else", amount: rest, pct: pct(rest) }] : head;
}

/** The single biggest source of spend, for a one-line "most of it goes to X" caption. */
export const concentration = (shares: Share[]): Share | null => shares.find((s) => s.key !== "Everything else") ?? null;


// ---------------------------------------------------------------------------------------------------------------------
// Why did spend change?  Spend = completed requests x cost per completed request, so a change splits exactly into
// "more or fewer requests" (volume) and "each request costing more or less" (unit cost, which also absorbs any shift
// between cheaper and dearer models). Integer arithmetic: volume + unitCost always equals delta, to the micro-dollar.
// ---------------------------------------------------------------------------------------------------------------------

export interface SpendChange { delta: Micros; volume: Micros; unitCost: Micros; computable: boolean }

export function spendChange(curSpend: Micros, curRequests: number, prevSpend: Micros, prevRequests: number): SpendChange {
  const delta = curSpend - prevSpend;
  if (prevRequests <= 0) return { delta, volume: ZERO, unitCost: ZERO, computable: false }; // nothing to compare against
  const volume = ((BigInt(Math.round(curRequests)) - BigInt(Math.round(prevRequests))) * prevSpend) / BigInt(Math.round(prevRequests));
  return { delta, volume, unitCost: delta - volume, computable: true };
}

/** One sentence saying how much spend moved and why, with signed parts so a partly offset change reads correctly. */
export function describeSpendChange(sc: SpendChange, fmt: (m: Micros) => string): string {
  const abs = (x: Micros) => (x < ZERO ? -x : x);
  if (sc.delta === ZERO && sc.volume === ZERO) return "Spending is unchanged from the previous period.";
  const dir = sc.delta > ZERO ? "rose" : sc.delta < ZERO ? "fell" : "stayed level";
  if (!sc.computable) return `Spending ${dir} by ${fmt(abs(sc.delta))}; there was no earlier activity to compare it with.`;
  const signed = (x: Micros) => `${x < ZERO ? "−" : "+"}${fmt(abs(x))}`;
  const parts: string[] = [];
  if (sc.volume !== ZERO) parts.push(`${signed(sc.volume)} from ${sc.volume > ZERO ? "more" : "fewer"} requests`);
  if (sc.unitCost !== ZERO) parts.push(`${signed(sc.unitCost)} from each request costing ${sc.unitCost > ZERO ? "more" : "less"}`);
  return `Spending ${dir} by ${fmt(abs(sc.delta))}: ${parts.join(" and ")}.`;
}

export interface Mover { key: string; cur: Micros; prev: Micros; delta: Micros; pct: number | null }

/** The groups whose spend moved most (up or down) between two periods. Groups that did not change are left out. */
export function topMovers(cur: Totals[], prev: Totals[], n: number): Mover[] {
  const c = new Map(cur.map((t) => [t.key, t.total]));
  const p = new Map(prev.map((t) => [t.key, t.total]));
  const keys = new Set([...c.keys(), ...p.keys()]);
  const out: Mover[] = [];
  for (const key of keys) {
    const a = c.get(key) ?? ZERO, b = p.get(key) ?? ZERO, delta = a - b;
    if (delta === ZERO) continue;
    out.push({ key, cur: a, prev: b, delta, pct: b > ZERO ? Number((delta * 10_000n) / b) / 100 : null });
  }
  const abs = (x: Micros) => (x < ZERO ? -x : x);
  return out.sort((x, y) => (abs(y.delta) > abs(x.delta) ? 1 : abs(y.delta) < abs(x.delta) ? -1 : x.key.localeCompare(y.key))).slice(0, n);
}

// ---------------------------------------------------------------------------------------------------------------------
// Adoption depth: not just "who used it" but how often. Counts only, never names. A person is placed by the share of
// the period's days on which they made at least one request.
// ---------------------------------------------------------------------------------------------------------------------

export const COHORT_POWER_SHARE = 0.5;
export const COHORT_REGULAR_SHARE = 0.2;
export const COHORT_MIN_DAYS = 7;

export interface Cohorts { power: number; regular: number; light: number; inactive: number; active: number }

/** Null when the period is too short to say how often anyone uses it, or when the member count is unknown. */
export function adoptionCohorts(rows: UserUsageRow[], memberCount: number, periodDays: number): Cohorts | null {
  if (periodDays < COHORT_MIN_DAYS || memberCount <= 0) return null;
  const days = new Map<string, Set<string>>();
  for (const r of rows) {
    if ((Number(r.calls) || 0) <= 0) continue;
    (days.get(r.actor_subject) ?? days.set(r.actor_subject, new Set()).get(r.actor_subject)!).add(String(r.day).slice(0, 10));
  }
  let power = 0, regular = 0, light = 0;
  for (const set of days.values()) {
    const share = set.size / periodDays;
    if (share >= COHORT_POWER_SHARE) power++;
    else if (share >= COHORT_REGULAR_SHARE) regular++;
    else light++;
  }
  const active = days.size;
  return { power, regular, light, active, inactive: Math.max(0, memberCount - active) };
}

/**
 * Distinct people who made a request in the last 7 days before `until` (exclusive), and in the 7 days before that.
 * The interim North Star: it leads revenue and spend, and becomes "verified successful tasks per week" once verified
 * outcomes exist in the backend.
 */
export function weeklyActive(rows: UserUsageRow[], until: string): { now: number; before: number } {
  const end = Date.parse(until);
  const inWindow = (day: string, from: number, to: number) => { const t = Date.parse(day); return t >= from && t < to; };
  const wk = 7 * 864e5;
  const a = new Set<string>(), b = new Set<string>();
  for (const r of rows) {
    if ((Number(r.calls) || 0) <= 0) continue;
    const day = String(r.day).slice(0, 10);
    if (inWindow(day, end - wk, end)) a.add(r.actor_subject);
    else if (inWindow(day, end - 2 * wk, end - wk)) b.add(r.actor_subject);
  }
  return { now: a.size, before: b.size };
}

export { dayOf };
