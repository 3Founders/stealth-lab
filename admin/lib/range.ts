/** Date-range helpers. `until` is always exclusive, matching the backend's since/until contract; all dates are UTC. */
export interface Range { since: string; until: string }

const DAY = 864e5;
const RE = /^\d{4}-\d{2}-\d{2}$/;
const iso = (d: Date) => d.toISOString().slice(0, 10);
const utc = (y: number, m: number, d: number) => new Date(Date.UTC(y, m, d));

export const isRange = (r: Partial<Range> | null | undefined): r is Range =>
  !!r && RE.test(r.since ?? "") && RE.test(r.until ?? "") && (r.since as string) < (r.until as string);

/** The last `days` days including today. */
export function lastDays(days: number, now = new Date()): Range {
  return { since: iso(new Date(now.getTime() - (days - 1) * DAY)), until: iso(new Date(now.getTime() + DAY)) };
}

export const thisMonth = (now = new Date()): Range => ({ since: iso(utc(now.getUTCFullYear(), now.getUTCMonth(), 1)), until: iso(new Date(now.getTime() + DAY)) });
export const lastMonth = (now = new Date()): Range => ({ since: iso(utc(now.getUTCFullYear(), now.getUTCMonth() - 1, 1)), until: iso(utc(now.getUTCFullYear(), now.getUTCMonth(), 1)) });

export const PRESETS: { label: string; make: (now?: Date) => Range }[] = [
  { label: "7 days", make: (n) => lastDays(7, n) },
  { label: "30 days", make: (n) => lastDays(30, n) },
  { label: "This month", make: thisMonth },
  { label: "Last month", make: lastMonth },
];

export const lengthDays = (r: Range) => Math.round((Date.parse(r.until) - Date.parse(r.since)) / DAY);

/** The window of equal length immediately before `r`, for period-over-period comparison. */
export function previousRange(r: Range): Range {
  const len = lengthDays(r);
  return { since: iso(new Date(Date.parse(r.since) - len * DAY)), until: r.since };
}

/** A range from `?since=&until=` (shareable links), or null when absent or invalid. */
export function rangeFromQuery(search: string): Range | null {
  const q = new URLSearchParams(search);
  const r = { since: q.get("since") ?? "", until: q.get("until") ?? "" };
  return isRange(r) ? r : null;
}

export const rangeToQuery = (r: Range) => new URLSearchParams({ since: r.since, until: r.until }).toString();

/** Percent change from `prev` to `cur`. Null when there's no baseline (prev is 0), which the UI shows as "new". */
export function pctChange(cur: number, prev: number): number | null {
  if (!Number.isFinite(cur) || !Number.isFinite(prev) || prev === 0) return null;
  return ((cur - prev) / Math.abs(prev)) * 100;
}
