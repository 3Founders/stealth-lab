import { describe, expect, it, vi } from "vitest";
import {
  addDays, billingApi, buyPlansOn, parsePlan, plainDate, planActive, seatsProblem, teamNameProblem,
} from "@/lib/billing";
import { PLANS } from "@/lib/plans";

const reply = (status: number, body: unknown = {}) => vi.fn(async (..._a: unknown[]) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const api = (fetchImpl: typeof fetch, token: string | null = "tok") => billingApi({ token: async () => token, origin: "https://mcp.test", fetchImpl });

describe("the plan purchase buttons are wired in by default", () => {
  it("are on unless the deployment says 'off'", () => {
    expect(buyPlansOn(undefined)).toBe(true);
    expect(buyPlansOn("")).toBe(true);
    expect(buyPlansOn("on")).toBe(true);
    expect(buyPlansOn(" OFF ")).toBe(false);
  });
  it("treat a server with no plan endpoint yet as 'not switched on', not as an error", async () => {
    const f = reply(404, {});
    expect((await api(f as unknown as typeof fetch).plan()).kind).toBe("off");
  });
});

describe("the bounds are checked before any request", () => {
  it("accepts 3 to 10 whole seats and refuses 2, 11, fractions and NaN", () => {
    for (const ok of [3, 4, 10]) expect(seatsProblem(ok)).toBeNull();
    for (const bad of [2, 11, 0, -1, 3.5, Number.NaN]) expect(seatsProblem(bad)).not.toBeNull();
  });
  it("needs a team name of 2 to 60 characters, ignoring surrounding spaces", () => {
    expect(teamNameProblem("")).not.toBeNull();
    expect(teamNameProblem("  A ")).not.toBeNull();
    expect(teamNameProblem("Ab")).toBeNull();
    expect(teamNameProblem("x".repeat(60))).toBeNull();
    expect(teamNameProblem("x".repeat(61))).not.toBeNull();
  });
});

describe("the plan the account has", () => {
  it("reads the server's answer, and a missing plan has no end date", () => {
    expect(parsePlan({ plan: "pilot", seats: 4, team_name: "Acme pilot", active_until: "2026-11-15T00:00:00Z" })).toEqual({ plan: "pilot", seats: 4, teamName: "Acme pilot", activeUntil: "2026-11-15T00:00:00Z" });
    expect(parsePlan({ plan: null, seats: 0, team_name: null, active_until: null })).toEqual({ plan: null, seats: 0, teamName: null, activeUntil: null });
    expect(parsePlan({ plan: "teams", active_until: "2026-11-15" }).plan).toBeNull();           // only plans that can be bought online
  });
  it("is active only for that plan and only until its end date", () => {
    const now = new Date("2026-11-01T00:00:00Z");
    const pro = { plan: "pro" as const, seats: 0, teamName: null, activeUntil: "2026-11-02T00:00:00Z" };
    expect(planActive(pro, "pro", now)).toBe(true);
    expect(planActive(pro, "pilot", now)).toBe(false);
    expect(planActive({ ...pro, activeUntil: "2026-10-31T00:00:00Z" }, "pro", now)).toBe(false);
    expect(planActive(null, "pro", now)).toBe(false);
  });
  it("shows dates as plain words, 14 days and 30 days apart", () => {
    const d = new Date("2026-11-01T10:00:00Z");
    expect(plainDate(d)).toBe("1 November 2026");
    expect(plainDate(addDays(d, 14))).toBe("15 November 2026");
    expect(plainDate(addDays(d, 30))).toBe("1 December 2026");
  });
});

describe("plan requests", () => {
  it("a pilot order carries the plan, seats and team name, and no amount or price", async () => {
    const f = reply(200, { order_id: "o1", amount_paise: 194700, key_id: "k", plan: "pilot", seats: 3, period_days: 14 });
    const r = await api(f as unknown as typeof fetch).planOrder({ plan: "pilot", seats: 3, teamName: "  Acme pilot  " });
    expect(r).toEqual({ kind: "ok", data: { orderId: "o1", amountPaise: 194700, keyId: "k", plan: "pilot", seats: 3, periodDays: 14 } });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mcp.test/v1/billing/orders");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ plan: "pilot", seats: 3, team_name: "Acme pilot" });
  });
  it("a Pro order carries only the plan", async () => {
    const f = reply(200, { order_id: "o2", amount_paise: 99900, key_id: "k", plan: "pro", period_days: 30 });
    await api(f as unknown as typeof fetch).planOrder({ plan: "pro" });
    expect(JSON.parse(String((f.mock.calls[0] as [string, RequestInit])[1].body))).toEqual({ plan: "pro" });
  });
  it("503 is 'not switched on', 401 is signed out, a refusal carries the server's sentence, and no token never calls the server", async () => {
    expect((await api(reply(503) as unknown as typeof fetch).planOrder({ plan: "pro" })).kind).toBe("off");
    expect((await api(reply(401) as unknown as typeof fetch).plan()).kind).toBe("signed-out");
    const refused = await api(reply(400, { error: { message: "A pilot is for 3 to 10 seats." } }) as unknown as typeof fetch).planOrder({ plan: "pilot", seats: 3, teamName: "ab" });
    expect(refused).toEqual({ kind: "error", message: "A pilot is for 3 to 10 seats." });
    const f = reply(200);
    expect((await api(f as unknown as typeof fetch, null).plan()).kind).toBe("signed-out");
    expect(f).not.toHaveBeenCalled();
  });
  it("an order without an id and key is unusable, and GET /plan reads the plan", async () => {
    expect((await api(reply(200, {}) as unknown as typeof fetch).planOrder({ plan: "pro" })).kind).toBe("error");
    const f = reply(200, { plan: "pro", seats: 0, team_name: null, active_until: "2026-12-01T00:00:00Z" });
    const r = await api(f as unknown as typeof fetch).plan();
    expect(r).toEqual({ kind: "ok", data: { plan: "pro", seats: 0, teamName: null, activeUntil: "2026-12-01T00:00:00Z" } });
    expect((f.mock.calls[0] as [string, RequestInit])[0]).toBe("https://mcp.test/v1/billing/plan");
  });
});

describe("plan prices live in lib/plans.ts only", () => {
  it("the numeric price agrees with the displayed one", () => {
    for (const p of PLANS) {
      if (p.priceInr === null) { expect(p.price).toBe("Custom"); continue; }
      expect(p.price).toBe(`₹${p.priceInr.toLocaleString("en-IN")}`);
    }
    expect(PLANS.find((p) => p.id === "pilot")?.priceInr).toBe(649);
    expect(PLANS.find((p) => p.id === "pro")?.priceInr).toBe(999);
  });
});
