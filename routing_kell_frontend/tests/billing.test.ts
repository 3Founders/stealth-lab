// Buying credits: the numbers shown are never invented, a 503 is a clean "not switched on" state, and nothing assumes a payment landed.
import { describe, expect, it } from "vitest";
import { amountProblem, billingApi, gstMode, gstSentence, marginPct, parseWallet, remainingInr, workedExample } from "@/lib/billing";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const api = (res: () => Response | Promise<Response>, token: string | null = "tok") =>
  billingApi({ token: async () => token, origin: "https://mcp.example", fetchImpl: (async () => res()) as unknown as typeof fetch });

describe("settings that are not final", () => {
  it("the margin is a number only when the deployment sets one", () => {
    expect(marginPct(undefined)).toBeNull();
    expect(marginPct("")).toBeNull();
    expect(marginPct("abc")).toBeNull();
    expect(marginPct("-5")).toBeNull();
    expect(marginPct("50")).toBe(50);
    expect(marginPct("12.5")).toBe(12.5);
  });

  it("GST wording follows the setting and says it is undecided otherwise", () => {
    expect(gstMode(undefined)).toBe("undecided");
    expect(gstMode("Inclusive")).toBe("inclusive");
    expect(gstSentence("extra")).toMatch(/added on top/);
    expect(gstSentence("undecided")).toMatch(/being confirmed/);
  });

  it("the worked example needs a margin; without one there is no number to mistake for a price", () => {
    expect(workedExample(10, null)).toBeNull();
    expect(workedExample(10, 50)).toEqual({ provider: 10, margin: 5, total: 15 });
    expect(workedExample(10, 12.5)).toEqual({ provider: 10, margin: 1.25, total: 11.25 });
  });
});

describe("top-up amount", () => {
  it("accepts whole rupees inside the range only", () => {
    expect(amountProblem("100")).toBeNull();
    expect(amountProblem("50000")).toBeNull();
    expect(amountProblem("99")).toMatch(/smallest/);
    expect(amountProblem("50001")).toMatch(/largest/);
    expect(amountProblem("12.5")).toMatch(/whole number/);
    expect(amountProblem("")).toMatch(/whole number/);
  });
});

describe("the wallet", () => {
  it("reads the server's numbers, and shows the balance in rupees from the server's own rate", () => {
    const w = parseWallet({ purchased_usd: "10", spent_usd: "2.5", remaining_usd: "7.5", usd_per_100_inr: "0.74", min_inr: 100, max_inr: 50000,
      credits: [{ payment_ref: "pay_1", source: "razorpay", amount_paise: 50000, amount_usd: "3.7", at: "2026-10-30T00:00:00Z" }] });
    expect(w.remainingUsd).toBe(7.5);
    expect(w.credits[0].inr).toBe(500);
    expect(remainingInr(w)).toBeCloseTo(1013.51, 2);
    expect(remainingInr({ ...w, usdPerInr: null })).toBeNull();
  });

  it("503 is the 'not switched on' state, 401 is signed out, and no token never calls the server", async () => {
    expect((await api(() => json(503, { error: { message: "off" } })).wallet()).kind).toBe("off");
    expect((await api(() => json(401, {})).wallet()).kind).toBe("signed-out");
    let called = false;
    const none = billingApi({ token: async () => null, fetchImpl: (async () => { called = true; return json(200, {}); }) as unknown as typeof fetch });
    expect((await none.wallet()).kind).toBe("signed-out");
    expect(called).toBe(false);
  });

  it("a refusal carries the server's sentence; an unreachable server is a plain error", async () => {
    const r = await api(() => json(400, { error: { message: "amount_inr must be between 100 and 50000" } })).order(5);
    expect(r).toEqual({ kind: "error", message: "amount_inr must be between 100 and 50000" });
    const down = billingApi({ token: async () => "t", fetchImpl: (async () => { throw new Error("x"); }) as unknown as typeof fetch });
    expect((await down.wallet()).kind).toBe("error");
  });

  it("an order is only usable with an order id and key; its answer is not a credit", async () => {
    const ok = await api(() => json(201, { order_id: "order_1", amount_paise: 50000, key_id: "rzp_test_x", credit_usd: "3.7" })).order(500);
    expect(ok).toEqual({ kind: "ok", data: { orderId: "order_1", amountPaise: 50000, keyId: "rzp_test_x" } });
    expect((await api(() => json(201, { amount_paise: 1 })).order(500)).kind).toBe("error");
  });
});
