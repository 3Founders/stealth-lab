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

// ---- plans (Starter, Pro, Plus and the Pilot): docs/pricing_page.md, Phase 2

/**
 * Buying a plan online is OFF unless the deployment says "on". The server side is not live until the founders say so; with the flag off the
 * cards on /pricing link to Contact and no payment block is shown. Set NEXT_PUBLIC_KELL_BUY_PLANS=on only when they do.
 */
export function buyPlansOn(raw: string | undefined = process.env.NEXT_PUBLIC_KELL_BUY_PLANS): boolean {
  return (raw ?? "").trim().toLowerCase() === "on";
}

/** A pilot is 1 to 10 seats. One seat is an individual pilot (no team); two or more make a team workspace that needs a name. */
export const PILOT_MIN_SEATS = 1;
export const PILOT_MAX_SEATS = 10;
export const TEAM_FROM_SEATS = 2;
export const TEAM_NAME_MIN = 2;
export const TEAM_NAME_MAX = 60;

export function seatsProblem(n: number): string | null {
  if (!Number.isInteger(n) || n < PILOT_MIN_SEATS || n > PILOT_MAX_SEATS) return `A pilot is for ${PILOT_MIN_SEATS} to ${PILOT_MAX_SEATS} seats.`;
  return null;
}

/** The team name is needed only from two seats; for one seat it is not asked for and not sent. */
export function teamNameProblem(raw: string, seats: number = TEAM_FROM_SEATS): string | null {
  if (seats < TEAM_FROM_SEATS) return null;
  const n = raw.trim().length;
  if (n < TEAM_NAME_MIN) return `Give the team a name of at least ${TEAM_NAME_MIN} characters.`;
  if (n > TEAM_NAME_MAX) return `The team name can be at most ${TEAM_NAME_MAX} characters.`;
  return null;
}

export type PlanName = "starter" | "pro" | "plus" | "pilot";
const PLAN_NAMES: PlanName[] = ["starter", "pro", "plus", "pilot"];
export interface PlanState { plan: PlanName | null; seats: number; teamName: string | null; activeUntil: string | null }

export function parsePlan(j: Record<string, unknown>): PlanState {
  const plan = PLAN_NAMES.find((n) => n === j.plan) ?? null;
  const until = typeof j.active_until === "string" && j.active_until ? j.active_until : null;
  return { plan, seats: Number(j.seats) || 0, teamName: typeof j.team_name === "string" && j.team_name ? j.team_name : null, activeUntil: plan ? until : null };
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

/** What a plan order asks for: the plan, and for a pilot the seats and (from two seats) the team name. Never an amount. */
export type PlanOrderRequest = { plan: "starter" | "pro" | "plus" } | { plan: "pilot"; seats: number; teamName?: string };

export const addDays = (d: Date, days: number) => new Date(d.getTime() + days * 86_400_000);
export const plainDate = (d: Date | string) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

export interface BillingDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

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

  async function call(method: "GET" | "POST", path: string, body?: unknown): Promise<Loaded<Record<string, unknown>>> {
    const token = await deps.token();
    if (!token) return { kind: "signed-out" };
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/billing${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
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
     * An order for a plan. The request names the plan, and for a pilot the seats and (from two seats) the team name; it never carries an
     * amount, because the server works out what to charge.
     */
    async planOrder(req: PlanOrderRequest): Promise<Loaded<{ orderId: string; amountPaise: number; keyId: string; plan: string; seats: number; periodDays: number }>> {
      const body = req.plan === "pilot"
        ? { plan: "pilot", seats: req.seats, ...(req.seats >= TEAM_FROM_SEATS ? { team_name: (req.teamName ?? "").trim() } : {}) }
        : { plan: req.plan };
      const r = await call("POST", "/orders", body);
      if (r.kind !== "ok") return r;
      const d = r.data;
      if (typeof d.order_id !== "string" || typeof d.key_id !== "string") return { kind: "error", message: "The payment window could not be prepared." };
      return { kind: "ok", data: { orderId: d.order_id, amountPaise: Number(d.amount_paise), keyId: d.key_id, plan: String(d.plan ?? req.plan), seats: Number(d.seats) || 0, periodDays: Number(d.period_days) || 0 } };
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
  key: string; order_id: string; amount: number; currency: "INR"; name: string; description: string;
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
