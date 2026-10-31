import { describe, expect, it, vi } from "vitest";
import {
  addDays, anyPlanActive, billingApi, buyPlansOn, parsePlan, plainDate, planActive, seatsProblem, teamNameProblem,
} from "@/lib/billing";
import { BUYABLE, LADDER, PILOT_VISIBLE, PLANS, isBuyable, planById } from "@/lib/plans";

const reply = (status: number, body: unknown = {}) => vi.fn(async (..._a: unknown[]) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const api = (fetchImpl: typeof fetch, token: string | null = "tok") => billingApi({ token: async () => token, origin: "https://mcp.test", fetchImpl });
const bodyOf = (f: ReturnType<typeof reply>) => JSON.parse(String((f.mock.calls[0] as [string, RequestInit])[1].body));

describe("the plan purchase buttons are off unless the deployment says 'on'", () => {
  it("are off by default, and on only for 'on'", () => {
    expect(buyPlansOn(undefined)).toBe(false);
    expect(buyPlansOn("")).toBe(false);
    expect(buyPlansOn("off")).toBe(false);
    expect(buyPlansOn("true")).toBe(false);
    expect(buyPlansOn("on")).toBe(true);
    expect(buyPlansOn(" ON ")).toBe(true);
  });
  it("treat a server with no plan endpoint yet as 'not switched on', not as an error", async () => {
    const f = reply(404, {});
    expect((await api(f as unknown as typeof fetch).plan()).kind).toBe("off");
  });
});

describe("the bounds are checked before any request", () => {
  it("accepts 1 to 10 whole seats and refuses 0, 11, fractions and NaN", () => {
    for (const ok of [1, 2, 3, 10]) expect(seatsProblem(ok)).toBeNull();
    for (const bad of [0, 11, -1, 3.5, Number.NaN]) expect(seatsProblem(bad)).not.toBeNull();
  });
  it("needs a team name of 2 to 60 characters from two seats, ignoring surrounding spaces; one seat needs none", () => {
    expect(teamNameProblem("", 1)).toBeNull();
    expect(teamNameProblem("x", 1)).toBeNull();
    expect(teamNameProblem("", 2)).not.toBeNull();
    expect(teamNameProblem("  A ", 2)).not.toBeNull();
    expect(teamNameProblem("Ab", 2)).toBeNull();
    expect(teamNameProblem("x".repeat(60), 5)).toBeNull();
    expect(teamNameProblem("x".repeat(61), 5)).not.toBeNull();
  });
});

describe("the plan the account has", () => {
  it("reads the server's answer for every plan that can be bought, and a missing plan has no end date", () => {
    expect(parsePlan({ plan: "pilot", seats: 4, team_name: "Acme pilot", active_until: "2026-11-15T00:00:00Z" })).toEqual({ plan: "pilot", seats: 4, teamName: "Acme pilot", activeUntil: "2026-11-15T00:00:00Z" });
    for (const plan of ["starter", "pro", "plus"]) expect(parsePlan({ plan, seats: 0, team_name: null, active_until: "2026-12-01T00:00:00Z" }).plan).toBe(plan);
    expect(parsePlan({ plan: null, seats: 0, team_name: null, active_until: null })).toEqual({ plan: null, seats: 0, teamName: null, activeUntil: null });
    expect(parsePlan({ plan: "teams", active_until: "2026-11-15" }).plan).toBeNull();           // only plans that can be bought online
  });
  it("is active only for that plan and only until its end date; a past date means ended", () => {
    const now = new Date("2026-11-01T00:00:00Z");
    const plus = { plan: "plus" as const, seats: 0, teamName: null, activeUntil: "2026-11-02T00:00:00Z" };
    expect(planActive(plus, "plus", now)).toBe(true);
    expect(planActive(plus, "pro", now)).toBe(false);
    expect(planActive({ ...plus, activeUntil: "2026-10-31T00:00:00Z" }, "plus", now)).toBe(false);
    expect(planActive(null, "plus", now)).toBe(false);
    expect(anyPlanActive(plus, now)).toBe(true);
    expect(anyPlanActive({ ...plus, activeUntil: "2026-10-31T00:00:00Z" }, now)).toBe(false);
    expect(anyPlanActive({ plan: null, seats: 0, teamName: null, activeUntil: null }, now)).toBe(false);
  });
  it("shows dates as plain words, 30 days apart", () => {
    const d = new Date("2026-11-01T10:00:00Z");
    expect(plainDate(d)).toBe("1 November 2026");
    expect(plainDate(addDays(d, 30))).toBe("1 December 2026");
  });
});

describe("plan requests", () => {
  it("a team pilot order carries the plan, seats and team name, and no amount or price", async () => {
    const f = reply(200, { order_id: "o1", amount_paise: 194700, key_id: "k", plan: "pilot", seats: 3, period_days: 30 });
    const r = await api(f as unknown as typeof fetch).planOrder({ plan: "pilot", seats: 3, teamName: "  Acme pilot  " });
    expect(r).toEqual({ kind: "ok", data: { orderId: "o1", amountPaise: 194700, keyId: "k", plan: "pilot", seats: 3, periodDays: 30 } });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mcp.test/v1/billing/orders");
    expect(init.method).toBe("POST");
    expect(bodyOf(f)).toEqual({ plan: "pilot", seats: 3, team_name: "Acme pilot" });
  });
  it("a one-seat pilot sends no team name, even if one was typed", async () => {
    const f = reply(200, { order_id: "o1", amount_paise: 64900, key_id: "k", plan: "pilot", seats: 1, period_days: 30 });
    await api(f as unknown as typeof fetch).planOrder({ plan: "pilot", seats: 1, teamName: "left over text" });
    expect(bodyOf(f)).toEqual({ plan: "pilot", seats: 1 });
  });
  it("Starter, Pro and Plus each carry only the plan name", async () => {
    for (const plan of ["starter", "pro", "plus"] as const) {
      const f = reply(200, { order_id: "o2", amount_paise: 1, key_id: "k", plan, period_days: 30 });
      await api(f as unknown as typeof fetch).planOrder({ plan });
      expect(bodyOf(f)).toEqual({ plan });
    }
  });
  it("503 is 'not switched on', 401 is signed out, a refusal carries the server's sentence, and no token never calls the server", async () => {
    expect((await api(reply(503) as unknown as typeof fetch).planOrder({ plan: "pro" })).kind).toBe("off");
    expect((await api(reply(401) as unknown as typeof fetch).plan()).kind).toBe("signed-out");
    const refused = await api(reply(400, { error: { message: "A pilot is for 1 to 10 seats." } }) as unknown as typeof fetch).planOrder({ plan: "pilot", seats: 11 });
    expect(refused).toEqual({ kind: "error", message: "A pilot is for 1 to 10 seats." });
    const second = await api(reply(409, { error: { message: "You already have a pilot." } }) as unknown as typeof fetch).planOrder({ plan: "pilot", seats: 1 });
    expect(second).toEqual({ kind: "error", message: "You already have a pilot." });
    const f = reply(200);
    expect((await api(f as unknown as typeof fetch, null).plan()).kind).toBe("signed-out");
    expect(f).not.toHaveBeenCalled();
  });
  it("an order without an id and key is unusable, and GET /plan reads the plan", async () => {
    expect((await api(reply(200, {}) as unknown as typeof fetch).planOrder({ plan: "pro" })).kind).toBe("error");
    const f = reply(200, { plan: "plus", seats: 0, team_name: null, active_until: "2026-12-01T00:00:00Z" });
    const r = await api(f as unknown as typeof fetch).plan();
    expect(r).toEqual({ kind: "ok", data: { plan: "plus", seats: 0, teamName: null, activeUntil: "2026-12-01T00:00:00Z" } });
    expect((f.mock.calls[0] as [string, RequestInit])[0]).toBe("https://mcp.test/v1/billing/plan");
  });
});

describe("plan prices live in lib/plans.ts only", () => {
  it("the numeric price agrees with the displayed one", () => {
    for (const p of PLANS) {
      if (p.priceInr === null) { expect(p.price).toBe("Custom"); continue; }
      expect(p.price).toBe(`₹${p.priceInr.toLocaleString("en-IN")}`);
    }
  });
  it("the founders' prices: Starter 200, Pro 1,200, Plus 2,000 for 30 days; the Pilot 649 a seat for 30 days", () => {
    expect(LADDER.map((p) => [p.id, p.priceInr, p.periodDays, p.includedUsd])).toEqual([["starter", 200, 30, 1], ["pro", 1200, 30, 6], ["plus", 2000, 30, 12]]);
    expect(planById("pilot")).toMatchObject({ priceInr: 649, periodDays: 30, includedUsd: 4, per: "per seat for 30 days" });
    expect(planById("plus").includedUsd).toBe(2 * planById("pro").includedUsd!);
  });
  it("only Plus is recommended", () => {
    expect(PLANS.filter((p) => p.recommended).map((p) => p.id)).toEqual(["plus"]);
  });
  it("Teams and Enterprise are 'contact us' and cannot be bought online; the pilot's visibility is one constant", () => {
    expect(BUYABLE).toEqual(["starter", "pro", "plus", "pilot"]);
    expect(isBuyable("teams")).toBe(false);
    expect(isBuyable("enterprise")).toBe(false);
    expect(typeof PILOT_VISIBLE).toBe("boolean");
    expect(isBuyable("pilot")).toBe(PILOT_VISIBLE);
  });
  it("says nothing the founders ruled out: no bring-your-own-key, no public-only restriction, no retention or security claim, no other company's prices", () => {
    const all = JSON.stringify(PLANS);
    expect(all).not.toMatch(/bring your own key|public or non-sensitive|zero data retention|no-retention|SOC 2|Claude|ChatGPT|Codex|half the price|%/i);
  });
});
