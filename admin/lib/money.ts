/**
 * Exact USD arithmetic for the usage dashboard. The backend sends costs as decimal STRINGS; summing them as floats
 * would drift and make "components add up to the total" unprovable. Amounts are held as integer micro-dollars
 * (1e-6 USD, the precision the ledger itself is rounded to) in a BigInt, so sums are exact and order-independent.
 * Rounding happens only when a figure is displayed.
 */
export type Micros = bigint;

export const ZERO: Micros = 0n;
/** Components + unattributed must equal the total to within this many micro-dollars (the ledger's own rounding). */
export const INVARIANT_TOLERANCE_MICROS: Micros = 2n;

/** Parses "12.345678", "0", 3, null → micro-dollars. Digits beyond 6 decimals are rounded half-up; garbage is 0. */
export function toMicros(value: unknown): Micros {
  if (value == null) return ZERO;
  const text = typeof value === "number" ? (Number.isFinite(value) ? value.toFixed(9) : "0") : String(value).trim();
  const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(text);
  if (!m || (m[2] === "" && (m[3] ?? "") === "")) return ZERO;
  const [, sign, whole, frac = ""] = m;
  const padded = (frac + "000000").slice(0, 6);
  let micros = BigInt(whole || "0") * 1_000_000n + BigInt(padded);
  if (frac.length > 6 && frac.charCodeAt(6) >= 53 /* '5' */) micros += 1n;
  return sign === "-" ? -micros : micros;
}

/** "$1,234.57"; keeps sub-cent amounts visible ("$0.0042") so small components don't render as $0.00. */
export function formatUsd(micros: Micros, opts: { cents?: boolean } = {}): string {
  const neg = micros < 0n;
  const abs = neg ? -micros : micros;
  let decimals = 2;
  if (!opts.cents && abs > 0n && abs < 10_000n) decimals = 4; // under one cent
  const scale = 10n ** BigInt(6 - decimals);
  const rounded = (abs + scale / 2n) / scale; // half-up
  const unit = 10n ** BigInt(decimals);
  const whole = (rounded / unit).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (rounded % unit).toString().padStart(decimals, "0");
  return `${neg ? "-" : ""}$${whole}.${frac}`;
}

/** Share of `part` in `whole` as a percentage with one decimal; null when there is no whole to share. */
export function percent(part: Micros, whole: Micros): number | null {
  if (whole <= 0n) return null;
  return Number((part * 10_000n) / whole) / 100;
}

/** Effective dollars per one million tokens = cost / tokens × 1e6. Null (skip) when the component has no tokens. */
export function perMillionTokens(cost: Micros, tokens: number): Micros | null {
  if (!tokens || tokens <= 0) return null;
  // micros per token × 1e6 tokens ÷ 1e6 (micros→USD) cancels: result in micros-of-USD-per-1M = cost / tokens × 1e6
  return (cost * 1_000_000n) / BigInt(Math.round(tokens));
}
