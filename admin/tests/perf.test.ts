import { describe, expect, it } from "vitest";
import { cacheRollup, dailySeries, fmtMs, fmtRate, num, rowKey, tierLabel } from "@/lib/perf";
import type { PerformanceRow } from "@/lib/org-api";

const row = (over: Partial<PerformanceRow> = {}): PerformanceRow => ({
  day: "2026-09-10", provider: "p", model: "m", tool: "call_model", tier: "light", calls: 10,
  provider_p50_ms: 400, provider_p95_ms: 900, provider_p99_ms: 1500, gate_p50_ms: 4, gate_p95_ms: 9, gate_p99_ms: 12, gate_time_share: 0.01,
  calls_reporting_cache: 10, calls_with_cache_hit: 5, cache_hit_rate_requests: 0.5, cache_hit_rate_tokens: 0.4, ...over,
});

describe("null is meaningful", () => {
  it("a null cache rate reads 'not reported', never 0%", () => {
    expect(fmtRate(null)).toBe("not reported");
    expect(fmtRate(undefined)).toBe("not reported");
    expect(fmtRate(0)).toBe("0.0%"); // a real zero is distinct from missing
    expect(fmtRate("0.256")).toBe("25.6%");
  });
  it("num keeps null as null and parses numeric strings", () => {
    expect(num(null)).toBeNull();
    expect(num("")).toBeNull();
    expect(num("12.5")).toBe(12.5);
    expect(num("x")).toBeNull();
  });
  it("a null tier is 'untiered'", () => {
    expect(tierLabel(null)).toBe("untiered");
    expect(tierLabel("  ")).toBe("untiered");
    expect(tierLabel("flagship")).toBe("flagship");
  });
});

describe("fmtMs", () => {
  it("formats ms and seconds, dash for missing", () => {
    expect(fmtMs(4.25)).toBe("4.3 ms");
    expect(fmtMs(412.6)).toBe("413 ms");
    expect(fmtMs(1500)).toBe("1.50 s");
    expect(fmtMs(null)).toBe("—");
  });
});

describe("cacheRollup", () => {
  it("merges request hit rate exactly (sum hits / sum reporting), not as a mean of rates", () => {
    const g = cacheRollup([
      row({ calls_reporting_cache: 10, calls_with_cache_hit: 9, cache_hit_rate_requests: 0.9 }),
      row({ day: "2026-09-11", calls_reporting_cache: 90, calls_with_cache_hit: 9, cache_hit_rate_requests: 0.1 }),
    ], () => "all")[0];
    expect(g.requestHitRate).toBeCloseTo(0.18, 10); // 18/100, not (0.9+0.1)/2
  });
  it("is null (not 0) when no call reported cache", () => {
    const g = cacheRollup([row({ calls_reporting_cache: null, calls_with_cache_hit: null, cache_hit_rate_requests: null })], () => "all")[0];
    expect(g.requestHitRate).toBeNull();
    const z = cacheRollup([row({ calls_reporting_cache: 0, calls_with_cache_hit: 0 })], () => "all")[0];
    expect(z.requestHitRate).toBeNull();
  });
  it("groups by key", () => {
    const g = cacheRollup([row({ tier: "light" }), row({ tier: null }), row({ tier: "light" })], (r) => tierLabel(r.tier));
    expect(g.find((x) => x.key === "light")?.calls).toBe(20);
    expect(g.find((x) => x.key === "untiered")?.calls).toBe(10);
  });
});

describe("dailySeries", () => {
  it("returns each day's own value for one series, sorted, with no merging", () => {
    const rows = [row({ day: "2026-09-11", provider_p95_ms: 700 }), row({ day: "2026-09-10", provider_p95_ms: 900 }), row({ model: "other", provider_p95_ms: 1 })];
    const s = dailySeries(rows, rowKey(rows[0]), "provider_p95_ms");
    expect(s).toEqual([{ day: "2026-09-10", value: 900 }, { day: "2026-09-11", value: 700 }]);
  });
});
