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
export function verdict(s: ExecSignals): Verdict {
  const reasons: Reason[] = [];
  const add = (level: Reason["level"], text: string) => reasons.push({ level, text });

  if (s.killSwitch) add("action", "The emergency stop (kill switch) is on, so every model call is being refused.");
  if (s.auditIntact === false) add("action", "The audit log failed its integrity check. Treat it as untrusted until investigated.");
  if (s.budget != null) {
    if (s.budget <= 0n ? s.monthToDate > 0n : s.monthToDate >= s.budget) add("action", "Spending has reached the monthly budget.");
    else if (s.budget > 0n && s.projected > s.budget) add("watch", "At the current pace, spending will pass the monthly budget before month end.");
  }
  if (s.failureRate != null) {
    if (s.failureRate >= FAILURE_ACTION) add("action", `${(s.failureRate * 100).toFixed(1)}% of requests are failing (action threshold ${FAILURE_ACTION * 100}%).`);
    else if (s.failureRate >= FAILURE_WATCH) add("watch", `${(s.failureRate * 100).toFixed(1)}% of requests are failing (watch threshold ${FAILURE_WATCH * 100}%).`);
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

export interface Share { key: string; amount: Micros; pct: number }

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

export { dayOf };
