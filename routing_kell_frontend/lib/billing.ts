/**
 * Buying plans and add-on credit packs (backend/app/mcp_server/billing_routes.py; plan and open decisions in docs/billing.md).
 *
 * Calls carry the website-session token as a Bearer to the hosted MCP origin only, no cookies. Credits are added ONLY by the payment
 * provider's signed webhook; the browser returning from Checkout adds nothing, so after a payment this page waits for the balance
 * to change instead of assuming it did.
 *
 * Nothing here is a price. The GST wording is a deployment setting that is NOT final (docs/billing.md, "Open decisions"), so it comes from
 * NEXT_PUBLIC_KELL_GST_MODE and the page says "being confirmed" when absent. Credit packs come from the server's wallet answer.
 */
import { MCP_ORIGIN } from "@/lib/connector";
import type { PlanRow } from "@/lib/plans";

export type GstMode = "inclusive" | "extra" | "undecided";
export function gstMode(raw: string | undefined = process.env.NEXT_PUBLIC_KELL_GST_MODE): GstMode {
  const v = (raw ?? "").trim().toLowerCase();
  return v === "inclusive" || v === "extra" ? v : "undecided";
}

export function gstSentence(mode: GstMode): string {
  if (mode === "inclusive") return "GST is included in the amount you pay.";
  if (mode === "extra") return "GST is added on top of the amount, and shown before you pay.";
  return "How GST is applied is being confirmed. The amount shown in the payment window is the amount you pay.";
}

/** An add-on credit pack the server offers. The list comes from GET /v1/billing/wallet; the page never works a price out itself. */
export interface Pack { id: string; priceInr: number; creditUsd: number; inrPerUsd: number | null }

export interface Wallet {
  purchasedUsd: number; spentUsd: number; remainingUsd: number;
  credits: Array<{ ref: string; source: string; inr: number; at: string }>;
  /** The packs on offer, in the server's order. */
  packs: Pack[];
  /** True when the server sells packs only to people with an active plan. */
  packsNeedPlan: boolean;
  /** Free-form amounts are refused by the server unless this is true (off by default); the page has no such field. */
  freeformTopup: boolean;
}

export type Loaded<T> =
  | { kind: "ok"; data: T }
  | { kind: "off" }            // 503: buying credits is not switched on yet
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

// ---- plans (Starter, Pro and Plus): docs/pricing_page.md, Phase 2

/**
 * Buying a plan online is OFF unless the deployment says "on". The server side is not live until the founders say so; with the flag off the
 * cards on /pricing link to Contact and no payment block is shown. Set NEXT_PUBLIC_KELL_BUY_PLANS=on only when they do.
 */
export function buyPlansOn(raw: string | undefined = process.env.NEXT_PUBLIC_KELL_BUY_PLANS): boolean {
  return (raw ?? "").trim().toLowerCase() === "on";
}

export type PlanName = "starter" | "pro" | "plus" | "pilot";
const PLAN_NAMES: PlanName[] = ["starter", "pro", "plus", "pilot"];
export interface PlanState { plan: PlanName | null; activeUntil: string | null }

export function parsePlan(j: Record<string, unknown>): PlanState {
  const plan = PLAN_NAMES.find((n) => n === j.plan) ?? null;
  const until = typeof j.active_until === "string" && j.active_until ? j.active_until : null;
  return { plan, activeUntil: plan ? until : null };
}

/** Whether a plan is running now: it names a plan and its end date has not passed. */
export function planActive(s: PlanState | null | undefined, plan: PlanName, now = new Date()): boolean {
  if (!s || s.plan !== plan || !s.activeUntil) return false;
  const t = Date.parse(s.activeUntil);
  return Number.isFinite(t) && t > now.getTime();
}

/** Whether any plan is running now (the server returns the active higher tier first). */
export function anyPlanActive(s: PlanState | null | undefined, now = new Date()): boolean {
  return Boolean(s?.plan) && planActive(s, s!.plan!, now);
}

/** What a plan order asks for: the plan name, the seats for the one-person/team plan, and the referral code. Never an amount. */
export type PlanOrderRequest = { plan: PlanName; seats?: number; referralCode?: string };

/** The answer to a code check: a refusal's sentence comes from the server, the same words the order route would use. */
export type CodeCheck = { kind: "valid" } | { kind: "free"; creditUsd: string; team: boolean; placesLeft: number | null } | { kind: "off" } | { kind: "error"; message: string };

/** An amount of credit as people read it: "0.25", not "0.250000". The credits are the wallet's dollars one for one; the page calls them kell credits. */
export const kellCredits = (v: string | number): string => String(Number(v));

export const addDays = (d: Date, days: number) => new Date(d.getTime() + days * 86_400_000);
export const plainDate = (d: Date | string) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

export interface BillingDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

/** Auto-renew: the plan and (optionally) seats. The server starts a Razorpay Subscription; Checkout authorises the mandate. */
export type AutopayOrderRequest = { plan: PlanName; seats?: number; referralCode?: string };

/** The account's running auto-renew, as GET /v1/billing/subscription answers it. */
export interface SubscriptionState {
  plan: string | null; seats: number; status: string | null; currentEnd: string | null; amountPaise: number; periodDays: number;
}

export function parseWallet(j: Record<string, unknown>): Wallet {
  const num = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const credits = Array.isArray(j.credits) ? j.credits : [];
  const packs = Array.isArray(j.packs) ? j.packs : [];
  return {
    purchasedUsd: num(j.purchased_usd), spentUsd: num(j.spent_usd), remainingUsd: num(j.remaining_usd),
    credits: credits.map((c) => {
      const r = c as Record<string, unknown>;
      return { ref: String(r.payment_ref ?? ""), source: String(r.source ?? ""), inr: num(r.amount_paise) / 100, at: String(r.at ?? "") };
    }),
    packs: packs
      .map((x) => { const r = x as Record<string, unknown>; const per = num(r.inr_per_usd); return { id: String(r.id ?? ""), priceInr: num(r.price_inr), creditUsd: num(r.credit_usd), inrPerUsd: per > 0 ? per : null }; })
      .filter((x) => x.id && x.priceInr > 0 && x.creditUsd > 0),
    packsNeedPlan: j.packs_need_plan === true,
    freeformTopup: j.freeform_topup === true,
  };
}

export function billingApi(deps: BillingDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch: typeof fetch = deps.fetchImpl ?? ((...a) => fetch(...a));

  /**
   * One request to the billing surface. `auth: false` is for the two routes that need no session (the code check and the plan
   * numbers): the pricing page works before anyone signs in, and no token is sent when there is none.
   */
  async function call(method: "GET" | "POST", path: string, body?: unknown, opts: { auth?: boolean } = {}): Promise<Loaded<Record<string, unknown>>> {
    const auth = opts.auth !== false;
    const token = auth ? await deps.token() : null;
    if (auth && !token) return { kind: "signed-out" };
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/billing${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { ...(token ? { authorization: `Bearer ${token}` } : {}), ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { return { kind: "error", message: "Couldn't reach the keळ server. Try again in a moment." }; }
    let json: Record<string, unknown> = {};
    try { json = await res.json(); } catch { /* no body */ }
    if (res.status === 503) return { kind: "off" };
    if (res.status === 401) return { kind: "signed-out" };
    if (!res.ok) {
      const msg = (json.error as { message?: unknown } | undefined)?.message;
      return { kind: "error", message: typeof msg === "string" && msg ? msg : `The server answered ${res.status}.` };
    }
    return { kind: "ok", data: json };
  }

  return {
    async wallet(): Promise<Loaded<Wallet>> {
      const r = await call("GET", "/wallet");
      return r.kind === "ok" ? { kind: "ok", data: parseWallet(r.data) } : r;
    },
    /** The plan the signed-in account has bought, if any (docs/pricing_page.md, Phase 2). */
    async plan(): Promise<Loaded<PlanState>> {
      const r = await call("GET", "/plan");
      if (r.kind === "error" && /answered 404/.test(r.message)) return { kind: "off" };   // a server that has no plan endpoint yet: plans are not switched on
      return r.kind === "ok" ? { kind: "ok", data: parsePlan(r.data) } : r;
    },
    /**
     * The plan numbers as the server sells them now (the dashboard's billing_plan_rows). No session needed: the pricing page is
     * public. While billing is off (503) the page keeps its built-in copy from lib/plans.ts.
     */
    async plans(): Promise<Loaded<PlanRow[]>> {
      const r = await call("GET", "/plans", undefined, { auth: false });
      if (r.kind !== "ok") return r;
      const rows = Array.isArray(r.data.plans) ? r.data.plans : [];
      const out: PlanRow[] = [];
      for (const x of rows) {
        const d = x as Record<string, unknown>;
        const inr = Number(d.inr), days = Number(d.days), usd = Number(d.included_usd);
        if (typeof d.plan === "string" && Number.isFinite(inr) && inr > 0 && Number.isFinite(days) && days > 0 && Number.isFinite(usd) && usd > 0) {
          const max = Number(d.max_redemptions);
          out.push({ plan: d.plan, inr, days, includedUsd: usd,
                     maxRedemptions: Number.isFinite(max) && max > 0 ? max : null,
                     sold: Number.isFinite(Number(d.sold)) ? Number(d.sold) : 0 });
        }
      }
      return { kind: "ok", data: out };
    },
    /**
     * Is a referral code active? The refusal sentence comes from the server, so the page repeats exactly what the order route
     * would say. No session: a code is applied before anyone signs in. 503 = billing is off (nothing can be checked yet).
     */
    async checkCode(code: string): Promise<CodeCheck> {
      const r = await call("POST", "/codes/check", { code }, { auth: false });
      if (r.kind === "ok" && r.data.kind === "free") {
        return { kind: "free", creditUsd: String(r.data.credit_usd ?? "0"), team: r.data.team === true,
                 placesLeft: typeof r.data.places_left === "number" ? r.data.places_left : null };
      }
      if (r.kind === "ok") return { kind: "valid" };
      if (r.kind === "off") return { kind: "off" };
      if (r.kind === "signed-out") return { kind: "error", message: "The code could not be checked." };   // cannot happen: no session is asked for
      return { kind: "error", message: r.message };
    },
    /** Redeem a free-credit code (no payment): a small credit once per person, for a limited number of people. */
    async redeemCode(code: string): Promise<Loaded<{ creditedUsd: string; already: boolean; placesLeft: number | null; remainingUsd: string; team: boolean }>> {
      const r = await call("POST", "/redeem", { code });
      if (r.kind !== "ok") return r;
      const d = r.data;
      return { kind: "ok", data: { creditedUsd: String(d.credited_usd ?? "0"), already: d.already === true,
                                   placesLeft: typeof d.places_left === "number" ? d.places_left : null, remainingUsd: String(d.remaining_usd ?? "0"), team: d.kind === "team" } };
    },
    /** The signed-in buyer's own personal referral code, if they have one (made when their first plan is activated). */
    async myReferral(): Promise<Loaded<{ code: string | null }>> {
      const r = await call("GET", "/referral");
      if (r.kind !== "ok") return r;
      return { kind: "ok", data: { code: typeof r.data.code === "string" && r.data.code ? r.data.code : null } };
    },
    /** Start an opt-in auto-renew. Nothing is charged until Checkout authorises the mandate. */
    async autopayOrder(req: AutopayOrderRequest): Promise<Loaded<{ subscriptionId: string; keyId: string; plan: string; periodDays: number; amountPaise: number }>> {
      const body: Record<string, unknown> = { plan: req.plan, autopay: true };
      if (req.seats !== undefined) body.seats = req.seats;
      if (req.referralCode) body.referral_code = req.referralCode;
      const r = await call("POST", "/orders", body);
      if (r.kind !== "ok") return r;
      const d = r.data;
      if (typeof d.subscription_id !== "string" || typeof d.key_id !== "string") return { kind: "error", message: "The payment window could not be prepared." };
      return { kind: "ok", data: { subscriptionId: d.subscription_id, keyId: d.key_id, plan: String(d.plan ?? req.plan), periodDays: Number(d.period_days) || 0, amountPaise: Number(d.amount_paise) || 0 } };
    },
    /** The account's running auto-renew, if any (docs/autopay.md). */
    async mySubscription(): Promise<Loaded<SubscriptionState>> {
      const r = await call("GET", "/subscription");
      if (r.kind !== "ok") return r;
      const d = r.data;
      return { kind: "ok", data: {
        plan: typeof d.plan === "string" && d.plan ? d.plan : null,
        seats: Number(d.seats) || 0,
        status: typeof d.status === "string" && d.status ? d.status : null,
        currentEnd: typeof d.current_end === "string" && d.current_end ? d.current_end : null,
        amountPaise: Number(d.amount_paise) || 0,
        periodDays: Number(d.period_days) || 0,
      } };
    },
    /** Stop future auto-renew charges; the plan already paid for keeps running. */
    async cancelSubscription(): Promise<Loaded<{ cancelled: boolean }>> {
      const r = await call("POST", "/subscription/cancel", {});
      if (r.kind !== "ok") return r;
      return { kind: "ok", data: { cancelled: r.data.cancelled === true } };
    },
        async planOrder(req: PlanOrderRequest): Promise<Loaded<{ orderId: string; amountPaise: number; keyId: string; plan: string; periodDays: number }>> {
      const body: Record<string, unknown> = { plan: req.plan };
      if (req.seats !== undefined) body.seats = req.seats;
      if (req.referralCode) body.referral_code = req.referralCode;
      const r = await call("POST", "/orders", body);
      if (r.kind !== "ok") return r;
      const d = r.data;
      if (typeof d.order_id !== "string" || typeof d.key_id !== "string") return { kind: "error", message: "The payment window could not be prepared." };
      return { kind: "ok", data: { orderId: d.order_id, amountPaise: Number(d.amount_paise), keyId: d.key_id, plan: String(d.plan ?? req.plan), periodDays: Number(d.period_days) || 0 } };
    },
    /**
     * An order for an add-on credit pack. The request names the pack and nothing else: the server holds the price. It refuses (403) a person
     * with no active plan; that message is passed on as it is.
     */
    async packOrder(packId: string): Promise<Loaded<{ orderId: string; amountPaise: number; keyId: string; pack: string; creditUsd: number }>> {
      const r = await call("POST", "/orders", { pack: packId });
      if (r.kind !== "ok") return r;
      const d = r.data;
      if (typeof d.order_id !== "string" || typeof d.key_id !== "string") return { kind: "error", message: "The payment window could not be prepared." };
      return { kind: "ok", data: { orderId: d.order_id, amountPaise: Number(d.amount_paise), keyId: d.key_id, pack: String(d.pack ?? packId), creditUsd: Number(d.credit_usd) || 0 } };
    },
  };
}

export const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

interface RazorpayOptions {
  key: string; order_id?: string; subscription_id?: string; amount?: number; currency: "INR"; name: string; description: string;
  handler: () => void; modal: { ondismiss: () => void }; theme: { color: string };
}
type RazorpayCtor = new (o: RazorpayOptions) => { open(): void; on(ev: string, cb: (r: unknown) => void): void };

/** Loads Razorpay's Checkout script once, only when someone clicks Pay. */
export function loadCheckout(): Promise<RazorpayCtor | null> {
  const w = window as unknown as { Razorpay?: RazorpayCtor };
  if (w.Razorpay) return Promise.resolve(w.Razorpay);
  return new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = CHECKOUT_SRC; s.async = true;
    s.onload = () => resolve(w.Razorpay ?? null);
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
  });
}

/** Opens the payment window for an order. Resolves "paid" (the window reported success: NOT yet credited), "closed" or "failed". */
export async function openCheckout(o: { orderId: string; amountPaise: number; keyId: string }, description = "Prepaid credits"): Promise<"paid" | "closed" | "failed"> {
  const Razorpay = await loadCheckout();
  if (!Razorpay) return "failed";
  return new Promise((resolve) => {
    const rz = new Razorpay({
      key: o.keyId, order_id: o.orderId, amount: o.amountPaise, currency: "INR", name: "keळ", description,
      handler: () => resolve("paid"), modal: { ondismiss: () => resolve("closed") }, theme: { color: "#193A70" },
    });
    rz.on("payment.failed", () => resolve("failed"));
    rz.open();
  });
}

/** Opens the payment window to authorise an auto-renew mandate. Resolves like openCheckout; nothing is charged here beyond the first cycle. */
export async function openSubscriptionCheckout(o: { subscriptionId: string; keyId: string }, description = "Auto-renew"): Promise<"paid" | "closed" | "failed"> {
  const Razorpay = await loadCheckout();
  if (!Razorpay) return "failed";
  return new Promise((resolve) => {
    const rz = new Razorpay({
      key: o.keyId, subscription_id: o.subscriptionId, currency: "INR", name: "keळ", description,
      handler: () => resolve("paid"), modal: { ondismiss: () => resolve("closed") }, theme: { color: "#193A70" },
    });
    rz.on("payment.failed", () => resolve("failed"));
    rz.open();
  });
}
