import { describe, expect, it, vi } from "vitest";
import { addDays, anyPlanActive, billingApi, buyPlansOn, parsePlan, plainDate, planActive } from "@/lib/billing";
import { BUYABLE, LADDER, PLANS, isBuyable, planById } from "@/lib/plans";

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

describe("the plan the account has", () => {
  it("reads the server's answer for the plans that can be bought, and a missing plan has no end date", () => {
    for (const plan of ["starter", "pro", "plus"]) expect(parsePlan({ plan, seats: 0, team_name: null, active_until: "2026-12-01T00:00:00Z" })).toEqual({ plan, activeUntil: "2026-12-01T00:00:00Z" });
    expect(parsePlan({ plan: null, active_until: null })).toEqual({ plan: null, activeUntil: null });
    expect(parsePlan({ plan: "teams", active_until: "2026-11-15" }).plan).toBeNull();           // only plans that can be bought online
    expect(parsePlan({ plan: "pilot", active_until: "2026-11-15" }).plan).toBeNull();           // there is no pilot on the site
  });
  it("is active only for that plan and only until its end date; a past date means ended", () => {
    const now = new Date("2026-11-01T00:00:00Z");
    const plus = { plan: "plus" as const, activeUntil: "2026-11-02T00:00:00Z" };
    expect(planActive(plus, "plus", now)).toBe(true);
    expect(planActive(plus, "pro", now)).toBe(false);
    expect(planActive({ ...plus, activeUntil: "2026-10-31T00:00:00Z" }, "plus", now)).toBe(false);
    expect(planActive(null, "plus", now)).toBe(false);
    expect(anyPlanActive(plus, now)).toBe(true);
    expect(anyPlanActive({ ...plus, activeUntil: "2026-10-31T00:00:00Z" }, now)).toBe(false);
    expect(anyPlanActive({ plan: null, activeUntil: null }, now)).toBe(false);
  });
  it("shows dates as plain words, 30 days apart", () => {
    const d = new Date("2026-11-01T10:00:00Z");
    expect(plainDate(d)).toBe("1 November 2026");
    expect(plainDate(addDays(d, 30))).toBe("1 December 2026");
  });
});

describe("plan requests", () => {
  it("Starter, Pro and Plus each carry only the plan name: no amount, no price, no seats", async () => {
    for (const plan of ["starter", "pro", "plus"] as const) {
      const f = reply(200, { order_id: "o2", amount_paise: 1, key_id: "k", plan, period_days: 30 });
      const r = await api(f as unknown as typeof fetch).planOrder({ plan });
      expect(r).toEqual({ kind: "ok", data: { orderId: "o2", amountPaise: 1, keyId: "k", plan, periodDays: 30 } });
      expect(bodyOf(f)).toEqual({ plan });
      const [url, init] = f.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("https://mcp.test/v1/billing/orders");
      expect(init.method).toBe("POST");
    }
  });
  it("503 is 'not switched on', 401 is signed out, a refusal carries the server's sentence, and no token never calls the server", async () => {
    expect((await api(reply(503) as unknown as typeof fetch).planOrder({ plan: "pro" })).kind).toBe("off");
    expect((await api(reply(401) as unknown as typeof fetch).plan()).kind).toBe("signed-out");
    const refused = await api(reply(400, { error: { message: "Unknown plan." } }) as unknown as typeof fetch).planOrder({ plan: "pro" });
    expect(refused).toEqual({ kind: "error", message: "Unknown plan." });
    const f = reply(200);
    expect((await api(f as unknown as typeof fetch, null).plan()).kind).toBe("signed-out");
    expect(f).not.toHaveBeenCalled();
  });
  it("an order without an id and key is unusable, and GET /plan reads the plan", async () => {
    expect((await api(reply(200, {}) as unknown as typeof fetch).planOrder({ plan: "pro" })).kind).toBe("error");
    const f = reply(200, { plan: "plus", seats: 0, team_name: null, active_until: "2026-12-01T00:00:00Z" });
    const r = await api(f as unknown as typeof fetch).plan();
    expect(r).toEqual({ kind: "ok", data: { plan: "plus", activeUntil: "2026-12-01T00:00:00Z" } });
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
  it("the founders' prices: Starter 200, Pro 1,200, Plus 2,000 for 30 days, with $1, $6 and $12 of usage", () => {
    expect(LADDER.map((p) => [p.id, p.priceInr, p.periodDays, p.includedUsd])).toEqual([["starter", 200, 30, 1], ["pro", 1200, 30, 6], ["plus", 2000, 30, 12]]);
    expect(planById("plus").includedUsd).toBe(2 * planById("pro").includedUsd!);
  });
  it("only Plus is recommended", () => {
    expect(PLANS.filter((p) => p.recommended).map((p) => p.id)).toEqual(["plus"]);
  });
  it("there is no pilot: the plans are Starter, Pro, Plus, Teams and Enterprise, and Teams and Enterprise cannot be bought online", () => {
    expect(PLANS.map((p) => p.id)).toEqual(["starter", "pro", "plus", "teams", "enterprise"]);
    expect(BUYABLE).toEqual(["starter", "pro", "plus"]);
    expect(isBuyable("teams")).toBe(false);
    expect(isBuyable("enterprise")).toBe(false);
    expect(isBuyable("pilot")).toBe(false);
    expect(JSON.stringify(PLANS)).not.toMatch(/pilot/i);
  });
  it("says nothing the founders ruled out: no bring-your-own-key, no public-only restriction, no retention or security claim, no other company's prices", () => {
    const all = JSON.stringify(PLANS);
    expect(all).not.toMatch(/bring your own key|public or non-sensitive|zero data retention|no-retention|SOC 2|Claude|ChatGPT|Codex|half the price|%/i);
  });
});
