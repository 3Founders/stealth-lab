/**
 * Token savings: what the same tokens would have cost at the Opus 5.5 list price, minus what the ledger says they
 * cost. A list-price comparison only: it assumes every call could have run on Opus 5.5 with the same token counts,
 * and says nothing about quality. Prices are USD per 1M tokens (the router's price card for claude-opus-5-5,
 * backend/app/routing/data/prior_cards.json). A token costs price micro-dollars, so the arithmetic stays exact in
 * the same integer micro-dollars as lib/money.ts. Cache write has no card price; it is billed at 1.25x input,
 * Anthropic's published ratio.
 */
import type { Micros } from "@/lib/money";
import type { Totals } from "@/lib/usage";

export const BASELINE = { model: "Claude Opus 5.5", input: 4n, output: 20n, cacheWrite: 5n } as const;
const CACHE_READ_DECI = 2n; // $0.20 per 1M, held in tenths of a micro-dollar so it stays an integer

export function baselineCost(t: Pick<Totals, "tokensInput" | "tokensOutput" | "tokensCacheRead" | "tokensCacheWrite">): Micros {
  return BigInt(Math.round(t.tokensInput)) * BASELINE.input
    + BigInt(Math.round(t.tokensOutput)) * BASELINE.output
    + (BigInt(Math.round(t.tokensCacheRead)) * CACHE_READ_DECI) / 10n
    + BigInt(Math.round(t.tokensCacheWrite)) * BASELINE.cacheWrite;
}

export interface Savings { baseline: Micros; actual: Micros; saved: Micros; /** null when the baseline is zero. */ pct: number | null }

export function savings(t: Totals): Savings {
  const baseline = baselineCost(t);
  const saved = baseline - t.total;
  return { baseline, actual: t.total, saved, pct: baseline > 0n ? Number((saved * 10000n) / baseline) / 100 : null };
}
