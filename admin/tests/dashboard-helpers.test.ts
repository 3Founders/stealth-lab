import { describe, expect, it } from "vitest";
import { anomalousDays, budgetStatus } from "@/lib/alerts";
import { csvCell, toCsv } from "@/lib/export";
import { toMicros } from "@/lib/money";
import { MAX_RANGE_DAYS, PRESETS, isRange, lastDays, lastMonth, lengthDays, pctChange, previousRange, rangeFromQuery, rangeToQuery, thisMonth } from "@/lib/range";

describe("csv export", () => {
  it("quotes commas, quotes and newlines", () => {
    expect(csvCell('a,"b"\nc')).toBe('"a,""b""\nc"');
    expect(csvCell(null)).toBe("");
    expect(csvCell(5n)).toBe("5");
  });
  it("defuses spreadsheet formulas but keeps plain negative numbers numeric", () => {
    expect(csvCell("=HYPERLINK(\"x\")")).toBe(`"'=HYPERLINK(""x"")"`);
    expect(csvCell("+1+1")).toBe("'+1+1");
    expect(csvCell("@SUM(A1)")).toBe("'@SUM(A1)");
    expect(csvCell("-12.5")).toBe("-12.5");
    expect(csvCell(-3)).toBe("-3");
  });
  it("joins rows with CRLF and ends with a newline", () => {
    expect(toCsv(["a", "b"], [[1, "x"]])).toBe("a,b\r\n1,x\r\n");
  });
});

describe("ranges", () => {
  const now = new Date(Date.UTC(2026, 8, 15, 10)); // 15 Sep 2026
  it("last 7 days includes today and ends exclusive tomorrow", () => {
    expect(lastDays(7, now)).toEqual({ since: "2026-09-09", until: "2026-09-16" });
    expect(lengthDays(lastDays(7, now))).toBe(7);
  });
  it("this/last month", () => {
    expect(thisMonth(now)).toEqual({ since: "2026-09-01", until: "2026-09-16" });
    expect(lastMonth(now)).toEqual({ since: "2026-08-01", until: "2026-09-01" });
    expect(lastMonth(new Date(Date.UTC(2026, 0, 5)))).toEqual({ since: "2025-12-01", until: "2026-01-01" });
  });
  it("previous range is the equal-length window just before", () => {
    expect(previousRange({ since: "2026-09-09", until: "2026-09-16" })).toEqual({ since: "2026-09-02", until: "2026-09-09" });
  });
  it("round-trips through a shareable query and rejects bad input", () => {
    const r = { since: "2026-09-01", until: "2026-09-10" };
    expect(rangeFromQuery("?" + rangeToQuery(r))).toEqual(r);
    expect(rangeFromQuery("?since=2026-09-10&until=2026-09-01")).toBeNull();
    expect(rangeFromQuery("?since=x&until=y")).toBeNull();
    expect(isRange({ since: "2026-09-01", until: "2026-09-01" })).toBe(false);
  });
  it("every preset is a valid range", () => {
    for (const p of PRESETS) expect(isRange(p.make(now))).toBe(true);
  });
  it("pctChange has no baseline when prev is 0", () => {
    expect(pctChange(150, 100)).toBe(50);
    expect(pctChange(50, 100)).toBe(-50);
    expect(pctChange(5, 0)).toBeNull();
  });
});

describe("budgetStatus", () => {
  const m = toMicros;
  it("returns null with no budget, over when spent, warn on pace or 80%", () => {
    expect(budgetStatus(m("1"), m("2"), null)).toBeNull();
    expect(budgetStatus(m("100"), m("100"), m("100"))?.level).toBe("over");
    expect(budgetStatus(m("50"), m("120"), m("100"))?.level).toBe("warn"); // pace
    expect(budgetStatus(m("85"), m("90"), m("100"))?.level).toBe("warn"); // 80%
    expect(budgetStatus(m("10"), m("30"), m("100"))?.level).toBe("ok");
  });
  it("a $0 budget with spend is over; with no spend it says nothing", () => {
    expect(budgetStatus(m("0.5"), m("1"), m("0"))?.level).toBe("over");
    expect(budgetStatus(m("0"), m("0"), m("0"))).toBeNull();
  });
});

describe("anomalousDays", () => {
  const days = (vals: number[]) => vals.map((v, i) => ({ day: `2026-09-${String(i + 1).padStart(2, "0")}`, value: v }));
  it("flags a spike against a steady baseline", () => {
    const f = anomalousDays(days([10, 11, 9, 10, 12, 10, 11, 10, 60]));
    expect([...f]).toEqual(["2026-09-09"]);
  });
  it("does not flag normal variation or a drop", () => {
    expect(anomalousDays(days([10, 11, 9, 10, 12, 10, 11, 10, 13, 2])).size).toBe(0);
  });
  it("needs at least 7 days of history", () => {
    expect(anomalousDays(days([1, 1, 1, 100])).size).toBe(0);
  });
  it("a flat baseline flags only a doubling", () => {
    expect(anomalousDays(days([5, 5, 5, 5, 5, 5, 5, 9])).size).toBe(0);
    expect([...anomalousDays(days([5, 5, 5, 5, 5, 5, 5, 11]))]).toEqual(["2026-09-08"]);
  });
});

describe("budget alert levels (75% / 90%)", () => {
  const mm = toMicros;
  it("warns from 75% and again from 90%, with different wording; ok below 75%", () => {
    expect(budgetStatus(mm("74"), mm("74"), mm("100"))?.level).toBe("ok");
    const at75 = budgetStatus(mm("75"), mm("75"), mm("100"));
    expect(at75?.level).toBe("warn");
    const at90 = budgetStatus(mm("90"), mm("90"), mm("100"));
    expect(at90?.level).toBe("warn");
    expect(at90?.message).toMatch(/nearly at the limit/);
    expect(at75?.message).not.toMatch(/nearly at the limit/);
    expect(budgetStatus(mm("100"), mm("100"), mm("100"))?.level).toBe("over");
  });
});

describe("range limit", () => {
  it("accepts exactly the backend maximum and rejects one day more", () => {
    const since = "2026-01-01";
    const until = (days: number) => new Date(Date.parse(since) + days * 864e5).toISOString().slice(0, 10);
    expect(isRange({ since, until: until(MAX_RANGE_DAYS) })).toBe(true);
    expect(isRange({ since, until: until(MAX_RANGE_DAYS + 1) })).toBe(false);
  });
});
