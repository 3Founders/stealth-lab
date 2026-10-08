import { describe, expect, it } from "vitest";
import { FAILURE_ACTION, FAILURE_WATCH, activeUsers, activeUsersByDay, adoptionRate, concentration, costPerRequest, failureByDay, failureRate, successRate, topShares, verdict, type ExecSignals } from "@/lib/exec";
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
