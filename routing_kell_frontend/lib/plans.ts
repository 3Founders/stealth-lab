/**
 * The plans the founders chose (kell_decks/4_kell_router_and_coding_agents.html, slides 7-8; docs/billing.md, "Plans"). Prices are in
 * rupees, before GST. None of them can be bought online yet: monthly and per-seat billing and GST invoices are planned, not built, so
 * every plan is shown with that label and a way to ask for it. Only prepaid credits (the top-up on /pricing) are built, and they are
 * for extra usage on any plan.
 *
 * Kept off on purpose: comparisons with another company's list prices (no measured saving backs them), any savings percentage, and
 * any claim about data handling beyond the data-path sentence on the page.
 */
export interface Plan {
  id: "pro" | "teams" | "enterprise" | "pilot";
  name: string;
  price: string;
  per: string;
  forWhom: string;
  includes: string[];
  notIncluded?: string[];
  /** The status label shown on the card. */
  status: string;
  cta: string;
}

export const PLANS: Plan[] = [
  {
    id: "pro", name: "Pro", price: "₹999", per: "a month", forWhom: "One person or a small student team.",
    includes: [
      "Connect an assistant or coding tool.",
      "A recommended plan for each task.",
      "Lower-cost models included (about $5 of provider cost), or bring your own key.",
      "A short cost note after each task.",
    ],
    notIncluded: ["Public or non-sensitive material only."],
    status: "Not available online yet", cta: "Ask for Pro",
  },
  {
    id: "teams", name: "Teams", price: "₹1,800", per: "per seat a month", forWhom: "Three or more seats.",
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
    id: "enterprise", name: "Enterprise", price: "Custom", per: "annual contract", forWhom: "Private code or compliance needs.",
    includes: [
      "Everything in Teams.",
      "A no-retention provider option under a signed agreement.",
      "Runs inside your systems with your sign-in.",
      "Private tests on your own tasks.",
      "Security review, a data processing agreement and support terms.",
    ],
    notIncluded: ["These are planned. None of them is available today."],
    status: "Planned", cta: "Talk to us",
  },
  {
    id: "pilot", name: "Pilot", price: "₹649", per: "per seat for 2 weeks", forWhom: "Teams of 3 to 10 seats, using the MCP only.",
    includes: [
      "An assistant or coding tool connected to the keळ MCP, with recommendations.",
      "Routine steps on lower-cost models, with usage included.",
      "A kept record of what ran.",
      "A joint report at the end: cost per solved task, with and without keळ.",
    ],
    notIncluded: ["The website assistant room and the team admin pages."],
    status: "Invoiced directly", cta: "Ask about a pilot",
  },
];
