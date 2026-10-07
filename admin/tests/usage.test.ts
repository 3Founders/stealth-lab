import { describe, expect, it } from "vitest";
import { formatUsd, percent, perMillionTokens, toMicros } from "@/lib/money";
import { groupBy, processing, projectMonth, sumAll } from "@/lib/usage";
import type { UsageRow } from "@/lib/org-api";

const row = (over: Partial<UsageRow> = {}): UsageRow => ({
  organization_id: "o", day: "2026-09-10", provider: "p", model: "m", tool: "call_model",
  calls: 1, succeeded: 1, failed: 0, tokens_input_fresh: 1000, tokens_cache_read: 0, tokens_cache_write: 0, tokens_output: 500,
  cost_input_usd: "0.003000", cost_output_usd: "0.007500", cost_cache_read_usd: "0.000000", cost_cache_write_usd: "0.000000",
  cost_unattributed_usd: "0.000000", cost_usd: "0.010500", upper_bound_calls: 0, ...over,
});

describe("toMicros / formatUsd", () => {
  it("parses decimal strings exactly, with no float drift", () => {
    expect(toMicros("0.1") + toMicros("0.2")).toBe(toMicros("0.3"));
    expect(toMicros("12.345678")).toBe(12_345_678n);
    expect(toMicros("0.0000005")).toBe(1n); // half-up beyond 6 decimals
    expect(toMicros(null)).toBe(0n);
    expect(toMicros("abc")).toBe(0n);
    expect(toMicros(".5")).toBe(500_000n);
  });
  it("shows sub-cent amounts instead of $0.00", () => {
    expect(formatUsd(toMicros("0.0042"))).toBe("$0.0042");
    expect(formatUsd(toMicros("1234.5"))).toBe("$1,234.50");
    expect(formatUsd(0n)).toBe("$0.00");
  });
});

describe("percent / perMillionTokens", () => {
  it("guards divide-by-zero", () => {
    expect(percent(5n, 0n)).toBeNull();
    expect(perMillionTokens(5n, 0)).toBeNull();
  });
  it("is cost ÷ tokens × 1e6 ($3 for 1M tokens → $3 per 1M)", () => {
    expect(perMillionTokens(toMicros("3"), 1_000_000)).toBe(toMicros("3"));
    expect(perMillionTokens(toMicros("0.003"), 1000)).toBe(toMicros("3"));
  });
  it("computes shares", () => {
    expect(percent(toMicros("1"), toMicros("4"))).toBe(25);
  });
});

describe("aggregation", () => {
  it("sums exactly and keeps components + unattributed equal to the total", () => {
    const rows = Array.from({ length: 1000 }, () => row());
    const t = sumAll(rows);
    expect(t.total).toBe(toMicros("10.5"));
    expect(t.input + t.output + processing(t) + t.other).toBe(t.total);
    expect(t.inconsistentRows).toBe(0);
  });
  it("flags a row whose parts don't add up beyond rounding, but tolerates 1-2 micros", () => {
    expect(sumAll([row({ cost_usd: "0.010501" })]).inconsistentRows).toBe(0);
    expect(sumAll([row({ cost_usd: "0.020000" })]).inconsistentRows).toBe(1);
  });
  it("processing = cache read + cache write, and unattributed stays separate", () => {
    const t = sumAll([row({ cost_cache_read_usd: "0.5", cost_cache_write_usd: "0.25", cost_unattributed_usd: "1", cost_usd: "1.760500" })]);
    expect(processing(t)).toBe(toMicros("0.75"));
    expect(t.other).toBe(toMicros("1"));
  });
  it("treats an older backend's missing split as zero components (all in total)", () => {
    const old = { ...row(), cost_input_usd: undefined, cost_output_usd: undefined, cost_cache_read_usd: undefined, cost_cache_write_usd: undefined, cost_unattributed_usd: undefined } as UsageRow;
    const t = sumAll([old]);
    expect(t.input + t.output + processing(t) + t.other).toBe(0n);
    expect(t.total).toBe(toMicros("0.0105"));
  });
  it("groups by key", () => {
    const g = groupBy([row({ model: "a" }), row({ model: "a" }), row({ model: "b" })], (r) => r.model);
    expect(g.find((x) => x.key === "a")?.calls).toBe(2);
  });
  it("counts unpriced calls", () => {
    expect(sumAll([row({ upper_bound_calls: 3 }), row({ upper_bound_calls: 2 })]).upperBoundCalls).toBe(5);
  });
});

describe("projectMonth", () => {
  it("projects linearly: month to date ÷ days elapsed × days in month, ignoring other months", () => {
    const rows = [row({ day: "2026-09-01", cost_usd: "10" }), row({ day: "2026-09-10", cost_usd: "20" }), row({ day: "2026-08-31", cost_usd: "999" })];
    const p = projectMonth(rows, new Date(Date.UTC(2026, 8, 10, 12)));
    expect(p.monthToDate).toBe(toMicros("30"));
    expect(p.daysInMonth).toBe(30);
    expect(p.projected).toBe(toMicros("90")); // 30 / 10 days × 30
  });
});
