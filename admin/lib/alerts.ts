/**
 * Client-side alert logic: a budget-burn status and a robust daily-spend anomaly flag. These are computed in the
 * browser from data the admin already loaded, so they are visible only while the page is open. Real alert delivery
 * (email, Slack, paging) needs server-side rules and is a separate backend feature; nothing here pretends otherwise.
 */
import { percent, type Micros } from "@/lib/money";

export type BurnLevel = "ok" | "warn" | "over";
export interface BurnStatus { level: BurnLevel; message: string }

/** Budget status from month-to-date spend, linear month-end projection and the monthly budget. */
export function budgetStatus(mtd: Micros, projected: Micros, budget: Micros | null): BurnStatus | null {
  if (budget == null) return null;
  if (budget <= 0n) return mtd > 0n ? { level: "over", message: "The monthly budget is $0 (no spend allowed) but spend has been recorded." } : null;
  if (mtd >= budget) return { level: "over", message: `Month-to-date spend has reached the monthly budget (${percent(mtd, budget)}%).` };
  if (projected > budget) return { level: "warn", message: `On the current pace this month ends at ${percent(projected, budget)}% of the monthly budget.` };
  if ((mtd * 100n) / budget >= 80n) return { level: "warn", message: `${percent(mtd, budget)}% of the monthly budget is already used.` };
  return { level: "ok", message: `${percent(mtd, budget)}% of the monthly budget used; projected ${percent(projected, budget)}%.` };
}

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

export const MIN_BASELINE_DAYS = 7;
export const ROLLING_DAYS = 14;

/**
 * Days whose value is unusually HIGH against the rolling baseline of the previous ROLLING_DAYS days. Uses the median
 * and the median absolute deviation (a spike doesn't inflate its own baseline), flags a modified z-score above 3.5 that is
 * also at least 1.5x the baseline median, and never flags a day with fewer than MIN_BASELINE_DAYS of history. A flat baseline (MAD 0) flags only a doubling.
 */
export function anomalousDays(series: { day: string; value: number }[]): Set<string> {
  const flagged = new Set<string>();
  const s = [...series].sort((a, b) => a.day.localeCompare(b.day));
  for (let i = MIN_BASELINE_DAYS; i < s.length; i++) {
    const base = s.slice(Math.max(0, i - ROLLING_DAYS), i).map((p) => p.value);
    const med = median(base);
    const mad = median(base.map((v) => Math.abs(v - med)));
    const x = s[i].value;
    if (x <= med || x < 1.5 * med) continue; // statistically odd but under +50% is noise, not an incident
    const high = mad === 0 ? x > 2 * med && x > 0 : (0.6745 * (x - med)) / mad > 3.5;
    if (high) flagged.add(s[i].day);
  }
  return flagged;
}
