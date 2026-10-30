/**
 * Buying prepaid credits (backend/app/mcp_server/billing_routes.py; plan and open decisions in docs/billing.md).
 *
 * Calls carry the website-session token as a Bearer to the hosted MCP origin only, no cookies. Credits are added ONLY by the payment
 * provider's signed webhook; the browser returning from Checkout adds nothing, so after a payment this page waits for the balance
 * to change instead of assuming it did.
 *
 * Nothing here is a price. The margin and the GST wording are deployment settings that are NOT final (docs/billing.md, "Open
 * decisions"), so they come from NEXT_PUBLIC_KELL_MARGIN_PCT and NEXT_PUBLIC_KELL_GST_MODE and the page says "not set" when absent.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export const DEFAULT_MIN_INR = 100;
export const DEFAULT_MAX_INR = 50_000;
export const PRESET_AMOUNTS_INR = [100, 500, 1000, 5000] as const;

/** The margin as a whole or decimal percent, or null when the deployment has not set one. */
export function marginPct(raw: string | undefined = process.env.NEXT_PUBLIC_KELL_MARGIN_PCT): number | null {
  const n = Number((raw ?? "").trim());
  return (raw ?? "").trim() !== "" && Number.isFinite(n) && n >= 0 && n <= 1000 ? n : null;
}

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

/**
 * The worked example: a provider charge of `providerInr` rupees becomes `providerInr` plus the margin. With no margin set it returns
 * null and the page explains the rule in words only, because a number here would read as a price.
 */
export function workedExample(providerInr: number, pct: number | null): { provider: number; margin: number; total: number } | null {
  if (pct === null || !(providerInr > 0)) return null;
  const margin = Math.round(providerInr * pct) / 100;
  return { provider: providerInr, margin, total: Math.round((providerInr + margin) * 100) / 100 };
}

export function amountProblem(value: string, min = DEFAULT_MIN_INR, max = DEFAULT_MAX_INR): string | null {
  if (!/^\d+$/.test(value.trim())) return "Enter a whole number of rupees.";
  const n = Number(value);
  if (n < min) return `The smallest top-up is ₹${min}.`;
  if (n > max) return `The largest top-up is ₹${max.toLocaleString("en-IN")}.`;
  return null;
}

export interface Wallet {
  purchasedUsd: number; spentUsd: number; remainingUsd: number;
  /** Dollars of provider usage one top-up rupee buys (server-computed from its rate and margin), or null. */
  usdPerInr: number | null;
  minInr: number; maxInr: number;
  credits: Array<{ ref: string; source: string; inr: number; at: string }>;
}

export type Loaded<T> =
  | { kind: "ok"; data: T }
  | { kind: "off" }            // 503: buying credits is not switched on yet
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

export interface BillingDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

export function parseWallet(j: Record<string, unknown>): Wallet {
  const num = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const per100 = num(j.usd_per_100_inr);
  const credits = Array.isArray(j.credits) ? j.credits : [];
  return {
    purchasedUsd: num(j.purchased_usd), spentUsd: num(j.spent_usd), remainingUsd: num(j.remaining_usd),
    usdPerInr: per100 > 0 ? per100 / 100 : null,
    minInr: num(j.min_inr) || DEFAULT_MIN_INR, maxInr: num(j.max_inr) || DEFAULT_MAX_INR,
    credits: credits.map((c) => {
      const r = c as Record<string, unknown>;
      return { ref: String(r.payment_ref ?? ""), source: String(r.source ?? ""), inr: num(r.amount_paise) / 100, at: String(r.at ?? "") };
    }),
  };
}

/** Credit left, in the rupees it was bought with (balance in dollars divided by the dollars one rupee buys). */
export function remainingInr(w: Wallet): number | null {
  return w.usdPerInr ? Math.max(0, Math.round((w.remainingUsd / w.usdPerInr) * 100) / 100) : null;
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
    async order(amountInr: number): Promise<Loaded<{ orderId: string; amountPaise: number; keyId: string }>> {
      const r = await call("POST", "/orders", { amount_inr: amountInr });
      if (r.kind !== "ok") return r;
      const d = r.data;
      if (typeof d.order_id !== "string" || typeof d.key_id !== "string") return { kind: "error", message: "The payment window could not be prepared." };
      return { kind: "ok", data: { orderId: d.order_id, amountPaise: Number(d.amount_paise), keyId: d.key_id } };
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
export async function openCheckout(o: { orderId: string; amountPaise: number; keyId: string }): Promise<"paid" | "closed" | "failed"> {
  const Razorpay = await loadCheckout();
  if (!Razorpay) return "failed";
  return new Promise((resolve) => {
    const rz = new Razorpay({
      key: o.keyId, order_id: o.orderId, amount: o.amountPaise, currency: "INR", name: "keळ", description: "Prepaid credits",
      handler: () => resolve("paid"), modal: { ondismiss: () => resolve("closed") }, theme: { color: "#193A70" },
    });
    rz.on("payment.failed", () => resolve("failed"));
    rz.open();
  });
}
