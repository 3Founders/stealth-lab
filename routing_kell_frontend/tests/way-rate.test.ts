import { describe, expect, it } from "vitest";
import { rateFor, rateLabel } from "@/lib/way-rate";

/**
 * The rule this pins (docs/KELL.md §4): a rate is never shown without its n and its interval. The failure mode
 * is not "wrong maths" — it is a page that quietly renders "100%" for a 3-of-3 streak, which is the exact
 * unfalsifiable claim the product exists to replace.
 */
describe("rateFor", () => {
  it("returns nothing (never 0, never 1) when there is no evidence", () => {
    expect(rateFor(null, null)).toBeNull();
    expect(rateFor(null, 10)).toBeNull();
    expect(rateFor(0, 0)).toBeNull();
    expect(rateFor(0, null)).toBeNull();
  });

  it("refuses impossible counts rather than rendering a rate", () => {
    expect(rateFor(5, 3)).toBeNull(); // more successes than trials is a bug upstream
    expect(rateFor(-1, 3)).toBeNull();
    expect(rateFor(Number.NaN, 3)).toBeNull();
    expect(rateFor(1, Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("does not let a small perfect streak read as certainty", () => {
    const iv = rateFor(3, 3)!;
    expect(iv.p).toBe(1);
    // The whole point: 3/3 is a point estimate of 100% but an honest interval well short of it.
    expect(iv.upper).toBe(1);
    expect(iv.lower).toBeLessThan(0.5);
  });

  it("tightens as n grows", () => {
    const small = rateFor(7, 10)!;
    const large = rateFor(700, 1000)!;
    expect(large.upper - large.lower).toBeLessThan(small.upper - small.lower);
  });

  it("keeps the interval inside [0, 1]", () => {
    for (const [s, t] of [[0, 5], [5, 5], [1, 1000], [999, 1000]] as const) {
      const iv = rateFor(s, t)!;
      expect(iv.lower).toBeGreaterThanOrEqual(0);
      expect(iv.upper).toBeLessThanOrEqual(1);
      expect(iv.lower).toBeLessThanOrEqual(iv.upper);
    }
  });

  it("computes the Wilson interval, not the naive one", () => {
    // Wilson lower for 9/10 at 95% is ~0.596; the naive normal bound would be 0.9 - 0.186 = 0.714.
    const iv = rateFor(9, 10)!;
    expect(iv.lower).toBeGreaterThan(0.55);
    expect(iv.lower).toBeLessThan(0.62);
    expect(iv.p).toBeCloseTo(0.9, 10);
  });

  it("carries n and the counts through, so the label can never lose them", () => {
    const iv = rateFor(33, 35)!;
    expect(iv.successes).toBe(33);
    expect(iv.total).toBe(35);
    const label = rateLabel(iv);
    expect(label).toContain("33/35");
    expect(label).toContain("CI");
    expect(label).toMatch(/%/);
  });
});
