import { describe, expect, it } from "vitest";
import { DEFAULT_THRESHOLDS, FAILURE_ACTION, FAILURE_WATCH, activeUsers, adoptionCohorts, describeSpendChange, spendChange, topMovers, weeklyActive, activeUsersByDay, adoptionRate, concentration, costPerRequest, failureByDay, failureRate, successRate, topShares, verdict, type ExecSignals } from "@/lib/exec";
import { toMicros } from "@/lib/money";
import { emptyTotals } from "@/lib/usage";
import type { UserUsageRow } from "@/lib/org-api";

const m = toMicros;
const base = (over: Partial<ExecSignals> = {}): ExecSignals => ({
  calls: 100, killSwitch: false, noPolicy: false, budget: m("1000"), monthToDate: m("100"), projected: m("300"),
  failureRate: 0.01, unpricedCalls: 0, spikeDays: 0, usersAtCap: 0, auditIntact: true, ...over,
});
const urow = (u: string, day: string, calls = 1): UserUsageRow => ({
  actor_subject: u, day, calls, failed: 0, tokens_input_fresh: 0, tokens_cache_read: 0, tokens_cache_write: 0, tokens_output: 0, cost_usd: "0", upper_bound_calls: 0,
});
const grp = (key: string, total: string, calls = 1, failed = 0) => ({ ...emptyTotals(key), total: m(total), calls, failed });

describe("verdict", () => {
  it("is on track when nothing is wrong", () => {
    const v = verdict(base());
    expect(v).toMatchObject({ level: "good", headline: "On track", reasons: [] });
  });
  it("is quiet, not good, when there were no requests", () => {
    expect(verdict(base({ calls: 0, failureRate: null })).level).toBe("quiet");
  });
  it("kill switch, broken audit chain, spent budget and a high failure rate are each 'action'", () => {
    expect(verdict(base({ killSwitch: true })).level).toBe("action");
    expect(verdict(base({ auditIntact: false })).level).toBe("action");
    expect(verdict(base({ monthToDate: m("1000") })).level).toBe("action");
    expect(verdict(base({ failureRate: FAILURE_ACTION })).level).toBe("action");
  });
  it("a $0 budget with any spend is action; with no spend it is not", () => {
    expect(verdict(base({ budget: 0n, monthToDate: m("0.01"), projected: m("0.01") })).level).toBe("action");
    expect(verdict(base({ budget: 0n, monthToDate: 0n, projected: 0n })).level).toBe("good");
  });
  it("pace over budget, moderate failures, spikes, caps, no policy and unpriced calls are 'watch'", () => {
    expect(verdict(base({ projected: m("1200") })).level).toBe("watch");
    expect(verdict(base({ failureRate: FAILURE_WATCH })).level).toBe("watch");
    expect(verdict(base({ spikeDays: 2 })).level).toBe("watch");
    expect(verdict(base({ usersAtCap: 1 })).level).toBe("watch");
    expect(verdict(base({ noPolicy: true })).level).toBe("watch");
    expect(verdict(base({ unpricedCalls: 4 })).level).toBe("watch");
  });
  it("the worst reason sets the level and actions are listed first", () => {
    const v = verdict(base({ spikeDays: 1, killSwitch: true }));
    expect(v.level).toBe("action");
    expect(v.reasons[0].level).toBe("action");
    expect(v.reasons.length).toBe(2);
  });
  it("does not judge a source that was not checked (unknown audit, unknown failure rate)", () => {
    expect(verdict(base({ auditIntact: null, failureRate: null })).level).toBe("good");
  });
});

describe("rates", () => {
  it("success/failure are null with no requests, not 100%/0%", () => {
    expect(successRate(0, 0)).toBeNull();
    expect(failureRate(0, 0)).toBeNull();
    expect(successRate(200, 10)).toBeCloseTo(0.95);
    expect(failureRate(200, 10)).toBeCloseTo(0.05);
  });
  it("cost per request is exact integer micros, null with no requests", () => {
    expect(costPerRequest(m("1"), 4)).toBe(m("0.25"));
    expect(costPerRequest(m("1"), 0)).toBeNull();
  });
  it("adoption needs a member count", () => {
    expect(adoptionRate(3, 0)).toBeNull();
    expect(adoptionRate(3, 12)).toBe(0.25);
    expect(adoptionRate(20, 12)).toBe(1);
  });
});

describe("activity", () => {
  const rows = [urow("a", "2026-09-01"), urow("a", "2026-09-02"), urow("b", "2026-09-02"), urow("c", "2026-09-02", 0)];
  it("counts distinct people with at least one request", () => {
    expect(activeUsers(rows)).toBe(2);
  });
  it("counts distinct people per day, oldest first", () => {
    expect(activeUsersByDay(rows)).toEqual([{ day: "2026-09-01", value: 1 }, { day: "2026-09-02", value: 2 }]);
  });
  it("failure by day skips days with no requests", () => {
    expect(failureByDay([grp("2026-09-02", "1", 10, 1), grp("2026-09-01", "0", 0, 0)])).toEqual([{ day: "2026-09-02", value: 0.1 }]);
  });
});

describe("topShares", () => {
  it("takes the top n, rolls the rest into one row, and the percentages add up to 100", () => {
    const s = topShares([grp("a", "50"), grp("b", "30"), grp("c", "15"), grp("d", "5")], 2);
    expect(s.map((x) => x.key)).toEqual(["a", "b", "Everything else"]);
    expect(s.reduce((t, x) => t + x.pct, 0)).toBeCloseTo(100, 5);
    expect(s[2].amount).toBe(m("20"));
    expect(concentration(s)?.key).toBe("a");
  });
  it("is empty with no spend", () => {
    expect(topShares([grp("a", "0")], 3)).toEqual([]);
  });
});

describe("verdict with thresholds", () => {
  it("flags a nearly spent budget at the urgent level, and a used-up one at the warn level", () => {
    expect(verdict(base({ budget: m("100"), monthToDate: m("91"), projected: m("95") })).reasons.some((r) => /nearly at the limit/.test(r.text))).toBe(true);
    const w = verdict(base({ budget: m("100"), monthToDate: m("76"), projected: m("95") }));
    expect(w.level).toBe("watch");
    expect(w.reasons.some((r) => /already used/.test(r.text))).toBe(true);
    expect(verdict(base({ budget: m("100"), monthToDate: m("40"), projected: m("90") })).level).toBe("good");
  });
  it("honours custom thresholds", () => {
    const strict = { ...DEFAULT_THRESHOLDS, failureWatch: 0.01, failureAction: 0.02 };
    expect(verdict(base({ failureRate: 0.03 }), strict).level).toBe("action");
    expect(verdict(base({ failureRate: 0.03 })).level).toBe("good");
    const loose = { ...DEFAULT_THRESHOLDS, failureWatch: 0.5, failureAction: 0.9 };
    expect(verdict(base({ failureRate: 0.2 }), loose).level).toBe("good");
  });
});

describe("spendChange (volume vs unit cost)", () => {
  it("splits a change exactly: volume + unit cost = delta", () => {
    const sc = spendChange(m("300"), 150, m("100"), 100);
    expect(sc.computable).toBe(true);
    expect(sc.delta).toBe(m("200"));
    expect(sc.volume).toBe(m("50"));        // 50 more requests at last period's $1 each
    expect(sc.unitCost).toBe(m("150"));     // each request now costs $2
    expect(sc.volume + sc.unitCost).toBe(sc.delta);
  });
  it("stays exact when it doesn't divide evenly", () => {
    const sc = spendChange(m("10.000001"), 7, m("3.333333"), 3);
    expect(sc.volume + sc.unitCost).toBe(sc.delta);
  });
  it("handles a fall and cannot be computed without a previous period", () => {
    const down = spendChange(m("50"), 50, m("100"), 100);
    expect(down.volume).toBe(m("-50"));
    expect(down.unitCost).toBe(0n);
    expect(spendChange(m("10"), 5, 0n, 0).computable).toBe(false);
  });
});

describe("topMovers", () => {
  it("ranks groups by size of change, up or down, and omits unchanged ones", () => {
    const cur = [grp("a", "100"), grp("b", "10"), grp("c", "50"), grp("same", "5")];
    const prev = [grp("a", "60"), grp("b", "40"), grp("same", "5"), grp("gone", "20")];
    const mv = topMovers(cur, prev, 10);
    expect(mv.map((x) => x.key)).toEqual(["c", "a", "b", "gone"].sort((x, y) => Math.abs(Number((({ c: 50, a: 40, b: -30, gone: -20 } as Record<string, number>)[y])) ) - Math.abs(Number((({ c: 50, a: 40, b: -30, gone: -20 } as Record<string, number>)[x])))));
    expect(mv.find((x) => x.key === "c")).toMatchObject({ delta: m("50"), pct: null }); // new: no baseline
    expect(mv.find((x) => x.key === "a")?.pct).toBeCloseTo(66.66, 1);
    expect(mv.find((x) => x.key === "same")).toBeUndefined();
  });
  it("limits to n", () => {
    expect(topMovers([grp("a", "9"), grp("b", "8"), grp("c", "7")], [], 2).length).toBe(2);
  });
});

describe("adoptionCohorts", () => {
  const days = (n: number, u: string) => Array.from({ length: n }, (_, i) => urow(u, `2026-09-${String(i + 1).padStart(2, "0")}`));
  it("places people by the share of days they were active, and counts the rest as inactive", () => {
    const rows = [...days(20, "power"), ...days(8, "regular"), ...days(2, "light")]; // of 30 days
    const c = adoptionCohorts(rows, 10, 30)!;
    expect(c).toEqual({ power: 1, regular: 1, light: 1, active: 3, inactive: 7 });
  });
  it("is null when the period is too short or members are unknown", () => {
    expect(adoptionCohorts(days(3, "a"), 5, 5)).toBeNull();
    expect(adoptionCohorts(days(3, "a"), 0, 30)).toBeNull();
  });
  it("never reports negative inactive when more people were active than members", () => {
    expect(adoptionCohorts([...days(15, "a"), ...days(15, "b")], 1, 30)!.inactive).toBe(0);
  });
});

describe("weeklyActive", () => {
  it("counts distinct people in the last 7 days and the 7 before", () => {
    const rows = [urow("a", "2026-09-29"), urow("a", "2026-09-28"), urow("b", "2026-09-25"), urow("c", "2026-09-20"), urow("d", "2026-09-10")];
    expect(weeklyActive(rows, "2026-10-01")).toEqual({ now: 2, before: 1 });
  });
});

describe("describeSpendChange", () => {
  const fmt = (x: bigint) => `$${Number(x) / 1e6}`;
  it("names both causes with signs", () => {
    expect(describeSpendChange(spendChange(m("300"), 150, m("100"), 100), fmt)).toBe("Spending rose by $200: +$50 from more requests and +$150 from each request costing more.");
  });
  it("reads correctly when the causes pull in opposite directions", () => {
    // fewer requests (-$50) but each costs far more (+$110): net up $60
    const sc = spendChange(m("110"), 50, m("50"), 100);
    expect(sc.delta).toBe(m("60"));
    expect(describeSpendChange(sc, fmt)).toBe("Spending rose by $60: −$25 from fewer requests and +$85 from each request costing more.");
  });
  it("handles a fall, no change, and no baseline", () => {
    expect(describeSpendChange(spendChange(m("50"), 50, m("100"), 100), fmt)).toBe("Spending fell by $50: −$50 from fewer requests.");
    expect(describeSpendChange(spendChange(m("100"), 100, m("100"), 100), fmt)).toBe("Spending is unchanged from the previous period.");
    expect(describeSpendChange(spendChange(m("10"), 5, 0n, 0), fmt)).toMatch(/no earlier activity/);
  });
});
