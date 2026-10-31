import { describe, expect, it, vi } from "vitest";
import { billingApi, gstMode, gstSentence, parseWallet } from "@/lib/billing";
import { PACK_INFO, inrPerCreditUsd } from "@/lib/plans";

const reply = (status: number, body: unknown = {}) => vi.fn(async (..._a: unknown[]) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const api = (fetchImpl: typeof fetch, token: string | null = "tok") => billingApi({ token: async () => token, origin: "https://mcp.test", fetchImpl });
const WALLET = {
  purchased_usd: 5.25, spent_usd: 1, remaining_usd: 4.25,
  credits: [{ payment_ref: "pay_1", source: "razorpay", amount_paise: 100000, at: "2026-11-01T10:00:00Z" }],
  packs: [
    { id: "credits_500", price_inr: 500, credit_usd: 2.5, inr_per_usd: 200 },
    { id: "credits_1000", price_inr: 1000, credit_usd: 5.25, inr_per_usd: 190.5 },
  ],
  packs_need_plan: true, freeform_topup: false,
};

describe("settings that are not final", () => {
  it("GST wording follows the setting and says it is undecided otherwise", () => {
    expect(gstMode("inclusive")).toBe("inclusive");
    expect(gstMode(" EXTRA ")).toBe("extra");
    expect(gstMode(undefined)).toBe("undecided");
    expect(gstMode("maybe")).toBe("undecided");
    expect(gstSentence("inclusive")).toContain("included");
    expect(gstSentence("extra")).toContain("on top");
    expect(gstSentence("undecided")).toContain("being confirmed");
  });
});

describe("the wallet", () => {
  it("reads the balance, the credits and the packs the server offers, in the server's order", () => {
    const w = parseWallet(WALLET);
    expect(w.remainingUsd).toBe(4.25);
    expect(w.credits).toEqual([{ ref: "pay_1", source: "razorpay", inr: 1000, at: "2026-11-01T10:00:00Z" }]);
    expect(w.packs).toEqual([{ id: "credits_500", priceInr: 500, creditUsd: 2.5, inrPerUsd: 200 }, { id: "credits_1000", priceInr: 1000, creditUsd: 5.25, inrPerUsd: 190.5 }]);
    expect(w.packsNeedPlan).toBe(true);
    expect(w.freeformTopup).toBe(false);
  });
  it("ignores a pack with no id, price or credit, and treats missing flags as off", () => {
    const w = parseWallet({ ...WALLET, packs: [{ id: "", price_inr: 500, credit_usd: 2.5 }, { id: "x", price_inr: 0, credit_usd: 1 }, { id: "ok", price_inr: 100, credit_usd: 0.5 }], packs_need_plan: undefined, freeform_topup: undefined });
    expect(w.packs.map((p) => p.id)).toEqual(["ok"]);
    expect(w.packs[0].inrPerUsd).toBeNull();
    expect(w.packsNeedPlan).toBe(false);
    expect(w.freeformTopup).toBe(false);
  });
  it("503 is the 'not switched on' state, 401 is signed out, and no token never calls the server", async () => {
    expect((await api(reply(503) as unknown as typeof fetch).wallet()).kind).toBe("off");
    expect((await api(reply(401) as unknown as typeof fetch).wallet()).kind).toBe("signed-out");
    const f = reply(200);
    expect((await api(f as unknown as typeof fetch, null).wallet()).kind).toBe("signed-out");
    expect(f).not.toHaveBeenCalled();
  });
  it("reads GET /v1/billing/wallet with the session token", async () => {
    const f = reply(200, WALLET);
    const r = await api(f as unknown as typeof fetch).wallet();
    expect(r.kind).toBe("ok");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mcp.test/v1/billing/wallet");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok");
  });
  it("a refusal carries the server's sentence; an unreachable server is a plain error", async () => {
    const refused = await api(reply(500, { error: { message: "Try later." } }) as unknown as typeof fetch).wallet();
    expect(refused).toEqual({ kind: "error", message: "Try later." });
    const down = await api((async () => { throw new TypeError("network"); }) as unknown as typeof fetch).wallet();
    expect(down.kind).toBe("error");
  });
});

describe("buying a credit pack", () => {
  it("the order names the pack and nothing else: no amount, no price", async () => {
    const f = reply(200, { order_id: "order_7", amount_paise: 100000, currency: "INR", key_id: "rzp_test", pack: "credits_1000", credit_usd: 5.25 });
    const r = await api(f as unknown as typeof fetch).packOrder("credits_1000");
    expect(r).toEqual({ kind: "ok", data: { orderId: "order_7", amountPaise: 100000, keyId: "rzp_test", pack: "credits_1000", creditUsd: 5.25 } });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://mcp.test/v1/billing/orders");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ pack: "credits_1000" });
  });
  it("there is no amount-based order any more", () => {
    expect((api(reply(200) as unknown as typeof fetch) as unknown as Record<string, unknown>).order).toBeUndefined();
  });
  it("a person with no plan gets the server's 403 sentence; 503 is off; 429 shows its sentence; an order without id and key is unusable", async () => {
    const msg = "additional credits are for people with an active plan; buy a plan first";
    expect(await api(reply(403, { error: { message: msg } }) as unknown as typeof fetch).packOrder("credits_500")).toEqual({ kind: "error", message: msg });
    expect((await api(reply(503) as unknown as typeof fetch).packOrder("credits_500")).kind).toBe("off");
    expect(await api(reply(429, { error: { message: "Too many payment attempts. Try again in an hour." } }) as unknown as typeof fetch).packOrder("credits_500")).toEqual({ kind: "error", message: "Too many payment attempts. Try again in an hour." });
    expect((await api(reply(200, {}) as unknown as typeof fetch).packOrder("credits_500")).kind).toBe("error");
  });
});

describe("the pack information shown while billing is off", () => {
  it("lists the four packs with the per-dollar figures worked out from the numbers", () => {
    expect(PACK_INFO.map((p) => [p.id, p.priceInr, p.creditUsd])).toEqual([["credits_500", 500, 2.5], ["credits_1000", 1000, 5.25], ["credits_2500", 2500, 13.5], ["credits_5000", 5000, 28]]);
    expect(PACK_INFO.map((p) => inrPerCreditUsd(p.priceInr, p.creditUsd))).toEqual(["200.0", "190.5", "185.2", "178.6"]);
  });
  it("a bigger pack is a little cheaper per dollar of credit", () => {
    const per = PACK_INFO.map((p) => p.priceInr / p.creditUsd);
    for (let i = 1; i < per.length; i++) expect(per[i]).toBeLessThan(per[i - 1]);
  });
});
