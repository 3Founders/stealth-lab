export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "n/a";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}

export function formatTokens(n: number | null | undefined): string {
  return n === null || n === undefined ? "n/a" : n.toLocaleString("en-US");
}

/** Cost of one response. When no prices are set for the model the helper sends null: say so rather than showing $0. */
export function formatCost(cost: number | string | null | undefined): string {
  if (cost === null || cost === undefined || cost === "") return "price not set";
  const n = typeof cost === "number" ? cost : Number(cost);
  if (!Number.isFinite(n)) return "price not set";
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toPrecision(2)}` : `$${n.toFixed(4)}`;
}

/** Per-million-token list prices a person typed, shown back as "in / out". */
export function formatPrices(input: number | null, output: number | null): string {
  if (input === null || output === null) return "price not set";
  return `$${input} in / $${output} out per 1M tokens`;
}
