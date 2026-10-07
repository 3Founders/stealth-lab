/**
 * Helpers for the Tech Lead (performance) view.
 *
 * Two rules from the backend govern everything here:
 *  1. A percentile cannot be merged. The mean of two p95s is not a p95, and a call-weighted mean isn't either, so this
 *     view only ever shows a percentile exactly as the backend computed it (one day x model x tool x tier), and labels
 *     trends "daily p95". There is deliberately no helper that combines percentiles.
 *  2. A NULL cache rate means "the provider did not report cache tokens", never 0%. It stays null all the way to the
 *     screen, where it reads "not reported".
 * What CAN be merged exactly is a request hit rate (sum of hits ÷ sum of reporting calls), so that helper exists.
 */
import type { PerformanceRow } from "@/lib/org-api";

/** Number or numeric string → number; null/undefined/garbage → null (null is meaningful, so it is never turned into 0). */
export function num(v: unknown): number | null {
  if (v == null || v === "") return null;
  const x = typeof v === "number" ? v : Number(v);
  return Number.isFinite(x) ? x : null;
}

export function fmtMs(v: unknown): string {
  const x = num(v);
  if (x == null) return "—";
  if (x < 10) return `${x.toFixed(1)} ms`;
  if (x < 1000) return `${Math.round(x)} ms`;
  return `${(x / 1000).toFixed(2)} s`;
}

/** A 0..1 rate as a percentage; null → "not reported" (the caller decides the wording for non-cache rates). */
export function fmtRate(v: unknown, missing = "not reported"): string {
  const x = num(v);
  return x == null ? missing : `${(x * 100).toFixed(1)}%`;
}

export const tierLabel = (tier: string | null | undefined) => (tier && tier.trim() ? tier : "untiered");
export const rowKey = (r: PerformanceRow) => `${r.provider} / ${r.model} · ${r.tool} · ${tierLabel(r.tier)}`;
export const dayOf = (r: PerformanceRow) => String(r.day).slice(0, 10);

export interface CacheRollup {
  key: string;
  calls: number;
  reporting: number;
  hits: number;
  /** hits ÷ reporting; null when no call reported cache (then it is "not reported", not 0%). */
  requestHitRate: number | null;
}

/** Exact merged request hit rate per group. Token hit rates are NOT merged: that needs token counts the API doesn't send. */
export function cacheRollup(rows: PerformanceRow[], key: (r: PerformanceRow) => string): CacheRollup[] {
  const m = new Map<string, CacheRollup>();
  for (const r of rows) {
    const k = key(r);
    const g = m.get(k) ?? { key: k, calls: 0, reporting: 0, hits: 0, requestHitRate: null };
    g.calls += num(r.calls) ?? 0;
    g.reporting += num(r.calls_reporting_cache) ?? 0;
    g.hits += num(r.calls_with_cache_hit) ?? 0;
    m.set(k, g);
  }
  for (const g of m.values()) g.requestHitRate = g.reporting > 0 ? g.hits / g.reporting : null;
  return [...m.values()];
}

export type Metric = "provider_p50_ms" | "provider_p95_ms" | "provider_p99_ms" | "gate_p50_ms" | "gate_p95_ms" | "gate_p99_ms";
export const METRICS: { key: Metric; label: string; short: string }[] = [
  { key: "provider_p50_ms", label: "Provider latency p50", short: "p50" },
  { key: "provider_p95_ms", label: "Provider latency p95", short: "p95" },
  { key: "provider_p99_ms", label: "Provider latency p99", short: "p99" },
  { key: "gate_p50_ms", label: "Router overhead p50", short: "p50" },
  { key: "gate_p95_ms", label: "Router overhead p95", short: "p95" },
  { key: "gate_p99_ms", label: "Router overhead p99", short: "p99" },
];

/** One point per day for a single series, each value exactly as reported for that day (null = no data that day). */
export function dailySeries(rows: PerformanceRow[], series: string, metric: Metric): { day: string; value: number | null }[] {
  return rows
    .filter((r) => rowKey(r) === series)
    .map((r) => ({ day: dayOf(r), value: num(r[metric]) }))
    .sort((a, b) => a.day.localeCompare(b.day));
}
