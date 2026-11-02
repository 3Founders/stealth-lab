/**
 * The plans the founders chose (kell_decks/4_kell_router_and_coding_agents.html, slides 7-8; docs/billing.md, "Plans";
 * docs/pricing_page.md for the layout rules). Since 2026-11-02 the numbers live in the backend's billing_plan_rows table and are
 * edited in the admin dashboard's Billing page (db/167); this file is the built-in copy of those numbers - what the page shows
 * while the server does not answer (billing off) - and the card sentences, so a price is still never typed in a component. When
 * the server answers GET /v1/billing/plans the page overrides the numbers with it (withRows below). The server works out every
 * amount: the page sends a plan name (and, for a pilot, seats, a team name and a referral code), never a price.
 *
 * The ladder: three 30-day passes bought once and renewed by hand (not subscriptions), each with an amount of usage included, in dollars,
 * that the server credits at purchase. Plus is the plan the page recommends. The recommendation is a plain label, never a claim about what
 * other buyers do. Everything derived from the numbers below (the per-day figure, "twice Pro's usage for ₹800 more") is
 * computed here, so a change of price changes every sentence with it.
 *
 * The one-seat (individual) Pilot is NOT on the open ladder: an active referral code unlocks it, and the page then shows exactly
 * one plan card, this one. Two or more seats is the team pilot, invoiced directly. The code is checked by the server and the
 * order records it; nothing here can unlock a plan on its own.
 *
 * Kept off on purpose: comparisons with another company's list prices (no measured saving backs them), any savings percentage, and any
 * claim about data handling or security beyond the data-path sentence on the page. There is no bring-your-own-key plan for now.
 */
export type PlanId = "starter" | "pro" | "plus" | "pilot" | "teams" | "enterprise";
/** The plans that can be bought online (the server's plan names). */
export type BuyablePlanId = "starter" | "pro" | "plus" | "pilot";

export interface Plan {
  id: PlanId;
  name: string;
  price: string;
  /** The price in whole rupees, before GST (null for custom). The only numeric copy of the price; the page multiplies it by seats for the live total. */
  priceInr: number | null;
  per: string;
  forWhom: string;
  /** Days one payment covers (30 for every plan that can be bought online). */
  periodDays?: number;
  /** Dollars of usage included per payment. */
  includedUsd?: number;
  /** How many people may ever buy this plan (null/absent = no limit); `sold` is how many already have it, from the server. */
  maxRedemptions?: number | null;
  sold?: number;
  /** The plan the page recommends. */
  recommended?: boolean;
  includes: string[];
  notIncluded?: string[];
  /** The status label shown on a card that is not for sale online. */
  status: string;
  cta: string;
}

export const PLANS: Plan[] = [
  {
    id: "starter", name: "Starter", price: "₹200", priceInr: 200, per: "for 30 days", periodDays: 30, includedUsd: 1, forWhom: "To try it on real work.",
    includes: [
      "Connect an assistant or coding tool.",
      "A recommended plan for each task.",
      "A short cost note after each task.",
    ],
    status: "Not available online yet", cta: "Ask for Starter",
  },
  {
    id: "pro", name: "Pro", price: "₹1,200", priceInr: 1200, per: "for 30 days", periodDays: 30, includedUsd: 6, forWhom: "For regular use by one person.",
    includes: [
      "Connect an assistant or coding tool.",
      "A recommended plan for each task.",
      "A short cost note after each task.",
    ],
    status: "Not available online yet", cta: "Ask for Pro",
  },
  {
    id: "plus", name: "Plus", price: "₹2,000", priceInr: 2000, per: "for 30 days", periodDays: 30, includedUsd: 12, recommended: true, forWhom: "For heavy use by one person.",
    includes: [
      "Connect an assistant or coding tool.",
      "A recommended plan for each task.",
      "A short cost note after each task.",
    ],
    status: "Not available online yet", cta: "Ask for Plus",
  },
  {
    id: "pilot", name: "First Trial", price: "₹649", priceInr: 649, per: "for 30 days", periodDays: 30, includedUsd: 4,
    forWhom: "For one person, unlocked with a referral code.",
    includes: [
      "Assistant or coding tool connected to the keळ MCP.",
      "A recommended plan for each task.",
      "A kept record of what the assistant did.",
    ],
    status: "Unlocked with a referral code", cta: "Ask for First Trial",
  },
  {
    id: "teams", name: "Teams", price: "₹1,800", priceInr: 1800, per: "per seat a month", forWhom: "Three or more seats.",
    includes: [
      "Everything in Pro.",
      "Usage shared across the team, with per-person and monthly spending limits and one stop-all switch.",
      "Members, invitations, usage by person and an exportable record.",
      "A shared library that learns from the team's results.",
    ],
    notIncluded: ["The team admin pages are built but not switched on yet."],
    status: "Not available online yet", cta: "Ask for Teams",
  },
  {
    id: "enterprise", name: "Enterprise", price: "Custom", priceInr: null, per: "annual contract", forWhom: "Private code or compliance needs.",
    includes: [
      "Everything in Teams.",
      "Runs inside your systems with your sign-in.",
      "Private tests on your own tasks.",
      "A data processing agreement and support terms.",
    ],
    notIncluded: ["These are planned. None of them is available today."],
    status: "Planned", cta: "Talk to us",
  },
];

export const planById = (id: PlanId): Plan => PLANS.find((p) => p.id === id)!;

/** The three passes, cheapest first. */
export const LADDER: Plan[] = (["starter", "pro", "plus"] as const).map(planById);

export const BUYABLE: BuyablePlanId[] = ["starter", "pro", "plus", "pilot"];
export const isBuyable = (id: string): id is BuyablePlanId => (BUYABLE as string[]).includes(id);

/** One plan as the server sells it now (GET /v1/billing/plans, from the dashboard-edited billing_plan_rows). */
export interface PlanRow { plan: string; inr: number; days: number; includedUsd: number; maxRedemptions?: number | null; sold?: number }

/**
 * The plans with the server's current numbers, or the built-in copy when it does not answer (`rows` null). A buyable plan the
 * server did not name is not being sold and is dropped; Teams and Enterprise are not server rows (contact us), so they stay.
 * Only numbers are overridden here: every card sentence is still written in this file.
 */
export function withRows(rows: PlanRow[] | null): Plan[] {
  if (!rows) return PLANS;
  const by = new Map(rows.map((r) => [r.plan, r]));
  return PLANS.flatMap((p) => {
    const row = by.get(p.id);
    if (!row) return isBuyable(p.id) ? [] : [p];
    const priceInr = row.inr;
    return [{ ...p, price: `₹${priceInr.toLocaleString("en-IN")}`, priceInr, periodDays: row.days, includedUsd: row.includedUsd,
              maxRedemptions: row.maxRedemptions ?? null, sold: row.sold ?? 0 }];
  });
}

/** "about ₹67 a day": the price spread over the days it covers. */
export function perDay(p: Plan): string | null {
  return p.priceInr && p.periodDays ? `about ₹${Math.round(p.priceInr / p.periodDays).toLocaleString("en-IN")} a day` : null;
}

/** "Plus gives twice Pro's usage for ₹800 more", worked out from the two plans (null if either lacks a number). */
export function plusVersusPro(plus: Plan = planById("plus"), pro: Plan = planById("pro")): string | null {
  if (!plus.priceInr || !pro.priceInr || !plus.includedUsd || !pro.includedUsd) return null;
  const ratio = plus.includedUsd / pro.includedUsd;
  const more = plus.priceInr - pro.priceInr;
  const times = ratio === 2 ? "twice" : ratio === 3 ? "three times" : `${Math.round(ratio * 10) / 10} times`;
  return `${plus.name} gives ${times} ${pro.name}'s usage for ₹${more.toLocaleString("en-IN")} more.`;
}

/**
 * The add-on credit packs, shown as INFORMATION only: while billing is off (the server answers 503) or purchases are switched off, the page
 * has no server list to draw, so it shows these. Once billing is on, the cards are drawn from the wallet answer (GET /v1/billing/wallet,
 * `packs`) and this list is not used for a purchase: the server holds every price. Keep it in step with the server's pack list.
 */
export const PACK_INFO: Array<{ id: string; priceInr: number; creditUsd: number }> = [
  { id: "credits_500", priceInr: 500, creditUsd: 2.5 },
  { id: "credits_1000", priceInr: 1000, creditUsd: 5.25 },
  { id: "credits_2500", priceInr: 2500, creditUsd: 13.5 },
  { id: "credits_5000", priceInr: 5000, creditUsd: 28 },
];

/** Rupees per dollar of credit, to one decimal ("178.6"). */
export const inrPerCreditUsd = (priceInr: number, creditUsd: number): string => (priceInr / creditUsd).toFixed(1);
