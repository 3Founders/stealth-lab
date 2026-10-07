/**
 * Pure aggregation and derivation over the org usage rows. Dollar amounts come from the ledger and are only
 * summed here (never recomputed from tokens × price: prices are snapshotted per call on the server, so a browser
 * recompute would disagree with the ledger and any invoice).
 *
 * Cost buckets, as defined with the backend:
 *   input      = cost_input_usd                         (fresh input tokens)
 *   output     = cost_output_usd
 *   processing = cost_cache_read_usd + cost_cache_write_usd   (re-processing of cached context)
 *   other      = cost_unattributed_usd                  (provider-reported or worst-case; no split)
 * "Processing" is an interpretation; the read and write halves are kept apart so it can be regrouped.
 */
import { INVARIANT_TOLERANCE_MICROS, ZERO, toMicros, type Micros } from "@/lib/money";
import type { UsageRow } from "@/lib/org-api";

export interface Totals {
  key: string;
  calls: number;
  succeeded: number;
  failed: number;
  upperBoundCalls: number;
  tokensInput: number;
  tokensCacheRead: number;
  tokensCacheWrite: number;
  tokensOutput: number;
  input: Micros;
  output: Micros;
  cacheRead: Micros;
  cacheWrite: Micros;
  other: Micros;
  total: Micros;
  /** Rows whose components + unattributed differ from their total by more than the rounding tolerance. */
  inconsistentRows: number;
}

const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : Number(v) || 0);
export const processing = (t: Pick<Totals, "cacheRead" | "cacheWrite">): Micros => t.cacheRead + t.cacheWrite;
export const tokensProcessing = (t: Pick<Totals, "tokensCacheRead" | "tokensCacheWrite">) => t.tokensCacheRead + t.tokensCacheWrite;

export function emptyTotals(key: string): Totals {
  return { key, calls: 0, succeeded: 0, failed: 0, upperBoundCalls: 0, tokensInput: 0, tokensCacheRead: 0, tokensCacheWrite: 0, tokensOutput: 0,
    input: ZERO, output: ZERO, cacheRead: ZERO, cacheWrite: ZERO, other: ZERO, total: ZERO, inconsistentRows: 0 };
}

export function addRow(t: Totals, r: UsageRow): Totals {
  const input = toMicros(r.cost_input_usd), output = toMicros(r.cost_output_usd);
  const cacheRead = toMicros(r.cost_cache_read_usd), cacheWrite = toMicros(r.cost_cache_write_usd);
  const other = toMicros(r.cost_unattributed_usd), total = toMicros(r.cost_usd);
  const drift = input + output + cacheRead + cacheWrite + other - total;
  t.calls += n(r.calls); t.succeeded += n(r.succeeded); t.failed += n(r.failed); t.upperBoundCalls += n(r.upper_bound_calls);
  t.tokensInput += n(r.tokens_input_fresh); t.tokensCacheRead += n(r.tokens_cache_read);
  t.tokensCacheWrite += n(r.tokens_cache_write); t.tokensOutput += n(r.tokens_output);
  t.input += input; t.output += output; t.cacheRead += cacheRead; t.cacheWrite += cacheWrite; t.other += other; t.total += total;
  if ((drift < 0n ? -drift : drift) > INVARIANT_TOLERANCE_MICROS) t.inconsistentRows += 1;
  return t;
}

export function groupBy(rows: UsageRow[], key: (r: UsageRow) => string): Totals[] {
  const m = new Map<string, Totals>();
  for (const r of rows) {
    const k = key(r);
    m.set(k, addRow(m.get(k) ?? emptyTotals(k), r));
  }
  return [...m.values()];
}

export const sumAll = (rows: UsageRow[]): Totals => rows.reduce(addRow, emptyTotals("all"));
export const dayOf = (r: UsageRow) => String(r.day).slice(0, 10);

export interface MonthProjection {
  monthToDate: Micros;
  /** Whole days of the month elapsed, today included (a partial day counts as a day, so early-month figures run low). */
  daysElapsed: number;
  daysInMonth: number;
  projected: Micros;
}

/** Simple linear month-end projection: month-to-date ÷ days elapsed × days in month. A trend line, not a forecast. */
export function projectMonth(rows: UsageRow[], now: Date): MonthProjection {
  const y = now.getUTCFullYear(), mo = now.getUTCMonth();
  const prefix = `${y}-${String(mo + 1).padStart(2, "0")}`;
  const monthToDate = sumAll(rows.filter((r) => dayOf(r).startsWith(prefix))).total;
  const daysInMonth = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
  const daysElapsed = now.getUTCDate();
  const projected = (monthToDate * BigInt(daysInMonth)) / BigInt(daysElapsed);
  return { monthToDate, daysElapsed, daysInMonth, projected };
}

/** The first day of the month containing `now`, UTC, as YYYY-MM-DD. */
export const monthStartOf = (now: Date) => new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10);
