import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_THRESHOLDS } from "@/lib/exec";
import { loadThresholds, resetThresholds, saveThresholds, validateThresholds } from "@/lib/thresholds";

beforeEach(() => window.localStorage.clear());

describe("validateThresholds", () => {
  it("accepts the defaults and sensible custom values", () => {
    expect(validateThresholds(DEFAULT_THRESHOLDS)).toEqual(DEFAULT_THRESHOLDS);
    expect(validateThresholds({ failureWatch: 0.02, failureAction: 0.1, budgetWarnPct: 60, budgetUrgentPct: 85 })).not.toBeNull();
  });
  it("rejects out-of-order, zero, non-numeric and impossible values", () => {
    expect(validateThresholds({ ...DEFAULT_THRESHOLDS, failureWatch: 0.2, failureAction: 0.1 })).toBeNull();
    expect(validateThresholds({ ...DEFAULT_THRESHOLDS, failureWatch: 0 })).toBeNull();
    expect(validateThresholds({ ...DEFAULT_THRESHOLDS, failureAction: 1.5 })).toBeNull();
    expect(validateThresholds({ ...DEFAULT_THRESHOLDS, budgetWarnPct: 95, budgetUrgentPct: 90 })).toBeNull();
    expect(validateThresholds({ ...DEFAULT_THRESHOLDS, budgetUrgentPct: 100 })).toBeNull();
    expect(validateThresholds({ ...DEFAULT_THRESHOLDS, budgetWarnPct: Number.NaN })).toBeNull();
    expect(validateThresholds(null)).toBeNull();
  });
});

describe("storage", () => {
  it("round-trips per organisation and falls back to the defaults", () => {
    expect(loadThresholds("a")).toEqual(DEFAULT_THRESHOLDS);
    const t = { failureWatch: 0.02, failureAction: 0.1, budgetWarnPct: 60, budgetUrgentPct: 85 };
    expect(saveThresholds("a", t)).toBe(true);
    expect(loadThresholds("a")).toEqual(t);
    expect(loadThresholds("b")).toEqual(DEFAULT_THRESHOLDS);
    resetThresholds("a");
    expect(loadThresholds("a")).toEqual(DEFAULT_THRESHOLDS);
  });
  it("refuses to save invalid values and ignores corrupt stored ones", () => {
    expect(saveThresholds("a", { ...DEFAULT_THRESHOLDS, failureWatch: 0.9, failureAction: 0.1 })).toBe(false);
    window.localStorage.setItem("kel-admin-thresholds:a", "{not json");
    expect(loadThresholds("a")).toEqual(DEFAULT_THRESHOLDS);
    window.localStorage.setItem("kel-admin-thresholds:a", JSON.stringify({ failureWatch: 5 }));
    expect(loadThresholds("a")).toEqual(DEFAULT_THRESHOLDS);
  });
});
