/**
 * The one rate the Way page is allowed to draw, and the reason it is a module rather than three lines in the page.
 *
 * The rule (docs/KELL.md §4): **never a bare percentage.** LMArena reports score + interval + rank spread and
 * tells readers to compare spreads; a page that shows "94%" with no n and no interval is unfalsifiable, which
 * is exactly the failure this product exists to fix. So the estimate and its interval are computed together or
 * not at all: `rateFor` returns `null` when there is nothing honest to show, and the caller renders
 * "not measured" rather than a zero.
 *
 * Wilson, not the naive `p ± z·sqrt(p(1-p)/n)`: at small n or p near 0/1 the naive interval collapses to a point
 * or leaves [0,1] entirely, which would let a 3-of-3 streak render as a confident number. Wilson is also what the
 * backend ranks on (`product_model.BEST_VERIFIED_FLOOR`), so the page and the ranking cannot disagree.
 */

export type Interval = {
  /** Point estimate, 0..1. */
  p: number;
  /** Wilson lower bound, 0..1 — what the backend ranks by. */
  lower: number;
  /** Wilson upper bound, 0..1. */
  upper: number;
  successes: number;
  total: number;
};

/**
 * The Wilson score interval at `z` standard deviations (default 1.96, i.e. 95%).
 *
 * Returns `null` — never `0` and never `1` — when there is no evidence to summarise: a missing count, a
 * non-positive total, or more successes than trials (which would be a bug upstream, not a rate).
 */
export function rateFor(successes: number | null | undefined, total: number | null | undefined, z = 1.96): Interval | null {
  if (successes == null || total == null) return null;
  if (!Number.isFinite(successes) || !Number.isFinite(total)) return null;
  if (total <= 0 || successes < 0 || successes > total) return null;

  const p = successes / total;
  const denom = 1 + (z * z) / total;
  const centre = (p + (z * z) / (2 * total)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / denom;

  return {
    p,
    lower: Math.max(0, centre - half),
    upper: Math.min(1, centre + half),
    successes,
    total,
  };
}

/** "94.3% (33/35, 95% CI 81–99%)" — the only string this page is allowed to show for a rate. */
export function rateLabel(iv: Interval, digits = 1): string {
  const pct = (x: number) => (x * 100).toFixed(digits);
  // The bounds are rounded to whole percent: a two-decimal interval on 35 trials is false precision.
  return `${pct(iv.p)}% (${iv.successes}/${iv.total}, 95% CI ${Math.round(iv.lower * 100)}–${Math.round(iv.upper * 100)}%)`;
}

/**
 * A price, in the currency it was stored in. Prices are paise-native on the backend (docs/procedure_billing.md
 * §5): INR is stored as paise and shown as rupees, so no rounding happens between what was charged and what is
 * displayed. Takes the smallest unit and the currency, never a float rupee figure.
 *
 * Returns `null` for "free", so the caller can choose not to render a price block at all rather than print ₹0.
 */
export function priceLabel(amountPaise: number | null | undefined, currency = "INR"): string | null {
  if (amountPaise == null) return null;
  if (!Number.isFinite(amountPaise) || amountPaise < 0) return null;
  if (amountPaise === 0) return null; // a deliberate free listing is still free
  const code = (currency || "INR").toUpperCase();
  if (code === "INR") {
    const rupees = amountPaise / 100;
    // Whole rupees print without decimals; a paise remainder prints exactly, never rounded away.
    return `₹${Number.isInteger(rupees) ? rupees.toLocaleString("en-IN") : rupees.toFixed(2)}`;
  }
  if (code === "USD") {
    return `$${(amountPaise / 100).toFixed(2)}`;
  }
  return `${(amountPaise / 100).toFixed(2)} ${code}`;
}
