/**
 * Per-user spend, joined to the member list. Dollar amounts are summed as exact micro-dollars from the decimal
 * strings the backend sends. `actor_subject` (usage), `user_id` (budget, calls, denials) and `subject` (members) are
 * all the same identity-provider id, so they join directly. A user with spend but no member row (removed, or a
 * service identity) is still listed, under a shortened id, rather than dropped.
 */
import { ZERO, toMicros, type Micros } from "@/lib/money";
import type { BudgetPosition, Member, UserUsageRow } from "@/lib/org-api";

export interface UserTotals {
  subject: string;
  calls: number;
  failed: number;
  upperBoundCalls: number;
  tokens: number;
  input: Micros;
  output: Micros;
  processing: Micros;
  total: Micros;
  lastDay: string;
}

export function totalsByUser(rows: UserUsageRow[]): UserTotals[] {
  const m = new Map<string, UserTotals>();
  for (const r of rows) {
    const t = m.get(r.actor_subject) ?? { subject: r.actor_subject, calls: 0, failed: 0, upperBoundCalls: 0, tokens: 0, input: ZERO, output: ZERO, processing: ZERO, total: ZERO, lastDay: "" };
    t.calls += Number(r.calls) || 0;
    t.failed += Number(r.failed) || 0;
    t.upperBoundCalls += Number(r.upper_bound_calls) || 0;
    t.tokens += (Number(r.tokens_input_fresh) || 0) + (Number(r.tokens_cache_read) || 0) + (Number(r.tokens_cache_write) || 0) + (Number(r.tokens_output) || 0);
    t.input += toMicros(r.cost_input_usd);
    t.output += toMicros(r.cost_output_usd);
    t.processing += toMicros(r.cost_cache_read_usd) + toMicros(r.cost_cache_write_usd);
    t.total += toMicros(r.cost_usd);
    const day = String(r.day).slice(0, 10);
    if (day > t.lastDay) t.lastDay = day;
    m.set(r.actor_subject, t);
  }
  return [...m.values()].sort((a, b) => (b.total > a.total ? 1 : b.total < a.total ? -1 : a.subject.localeCompare(b.subject)));
}

export const shortId = (id: string) => (id.length > 12 ? `${id.slice(0, 8)}…` : id);

/** A readable label for a user id: display name, else email, else a shortened id. Never fabricated. */
export function displayName(subject: string | null | undefined, members: Member[]): string {
  if (!subject) return "(system)";
  const m = members.find((x) => x.subject === subject || x.user_id === subject);
  return m?.display_name?.trim() || m?.email?.trim() || shortId(subject);
}

export type CapState = "ok" | "near" | "at" | "none";

/** Position of a user's spend today against the per-user daily budget. `held` reservations count: they will settle. */
export function capStatus(usedPlusHeld: Micros, cap: Micros | null): { state: CapState; pct: number | null } {
  if (cap == null) return { state: "none", pct: null };
  if (cap <= 0n) return { state: usedPlusHeld > 0n ? "at" : "none", pct: null };
  const pct = Number((usedPlusHeld * 1000n) / cap) / 10;
  return { state: pct >= 100 ? "at" : pct >= 80 ? "near" : "ok", pct };
}

export interface UserBudgetToday { user_id: string; used: Micros; held: Micros; remaining: Micros; position: ReturnType<typeof capStatus> }

export function userBudgets(b: BudgetPosition): UserBudgetToday[] {
  const cap = toMicros(b.per_user_daily_budget_usd);
  return b.users_today
    .map((u) => {
      const used = toMicros(u.used_usd), held = toMicros(u.held_usd);
      return { user_id: u.user_id, used, held, remaining: toMicros(u.remaining_usd), position: capStatus(used + held, cap) };
    })
    .sort((a, b) => (b.used + b.held > a.used + a.held ? 1 : -1));
}
