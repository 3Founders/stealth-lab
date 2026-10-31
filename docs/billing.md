# Billing: prepaid credits in rupees

Status: the website side (`/pricing`) and the backend side (`backend/app/mcp_server/billing_routes.py`, `backend/app/services/wallet.py`) are built. Nothing takes real money until the environment below is set and the legal checklist is done. Several numbers are deliberately **not final** (see Open decisions); the site reads them from settings and says "not set" when they are absent.

## Plans (source: `kell_decks/4_kell_router_and_coding_agents.html`, slides 7-8)

These are the prices the founders chose. All in rupees, **before GST**. The credits below are how "extra usage" is bought on every plan.

| Plan | Price | For | Includes |
|---|---|---|---|
| **Starter** | ₹200 for 30 days | One person, light use | $1 of usage included. |
| **Pro** | ₹1,200 for 30 days | One person or a small student team | $6 of usage included; connect an assistant or coding tool; a recommended plan per task; a short cost note after each task. |
| **Plus** | ₹2,000 for 30 days | One person who uses it daily | $12 of usage included (twice Pro). The tier the page recommends. |
| **Teams** | ₹1,800 per seat a month | Three or more seats | Everything in Pro; usage shared across the team; per-person and monthly spending limits and one stop-all switch; members, invitations, usage by person, exportable record; a shared library that learns from the team's results. |
| **Enterprise** | Custom, by annual contract | Private code or compliance needs | Everything in Teams; a no-retention provider option under a signed agreement; runs inside the customer's systems with their sign-in; private tests on their own tasks; security review, DPA and support terms. |
| **Pilot** | ₹649 per seat for a month | Teams of 3 to 10 seats, MCP only | Assistant or coding tool connected to the kell MCP; recommendations; routine steps on lower-cost models with usage included; a kept record; a joint report (cost per solved task, with and without kell). Not included: the website assistant room and team admin pages. Invoiced directly. |

Extra usage on every plan: bought in advance as credits at the provider's cost plus a published margin (formula below).

**What is actually live, which the website must say plainly:**

- Buying credits in advance: built and tested, not yet switched on.
- Pro and Teams monthly/per-seat billing and GST invoices: **planned, not built**. Until they are, the page shows the plans with "Not yet available online" and a contact route; the pilot is invoiced directly.
- Teams admin pages: built but off in production.
- Enterprise items: planned; none is available today.

**Claims to keep off the site unless verified:** the deck says Pro is "about half the price of Claude Pro", Teams "about half a Claude Team seat" and Enterprise "no more than half" of the Claude plan. These are comparisons against a third party's list prices (the deck calls them approximate) and no measured saving backs them. Do not headline them; if shown at all, label as approximate India list prices with a date. Also: no savings percentages from the deck's measurements slide, and no pilot lead named.

## The credit plan

People want to pay us while the company is not yet incorporated, so the first version is the simplest one that is honest:

- **Prepaid credits, in INR.** A person tops up between ₹100 and ₹50,000. Credits are spent as the managed tier runs open models for them.
- **Pay-as-you-go, no subscription.** What a run uses is the provider's own charge for it, converted to rupees, plus a margin.
- **Credits buy keळ's own service only.** Non-transferable, never redeemable for cash.
- **India and INR only** to start.
- **Interim account.** Razorpay account in a founder's sole-proprietorship name, replaced by a company account once incorporation is confirmed.
- **Managed tier is open models only.** A person who brings their own provider key is billed by that provider and uses no credit. Nothing on the site mentions reselling any closed model.

## Accounts, plans and the order contract (migration 164)

- **Whose wallet.** A person's wallet account id is their user id. No organization row, no membership, nothing hidden
  (`app/services/accounts.py`). Migration 164 dropped the foreign keys to `organizations` on the five tables a wallet uses
  (`org_policies`, `provider_call_ledger`, `org_denials`, `org_wallet_credits`, `billing_orders`) and added a trigger that requires
  the id to be an organization or a user. Row-level security and every query by `organization_id` are unchanged.
- **Teams are real organizations.** A Pilot of two or more seats creates an organization named by the buyer, with the buyer as
  owner, a wallet-required policy and the included usage credited to it. The other seats join with the organization invites.
  A one-seat Pilot, and Pro, credit the buyer's own account.
- **Who pays by default.** A person in exactly one team pays through the team; alone, through their own wallet; in several teams
  they must name one (`org_id` / `X-Stealthlab-Org`).
- **Plans.** A ladder of three 30-day tiers: Starter Rs 200 ($1 included), Pro Rs 1,200 ($6) and Plus Rs 2,000 ($12; the tier the page
  recommends: twice Pro's usage for Rs 800 more). Pilot Rs 649 per seat for 30 days, 1 to 10 seats, $4 per seat included, one per
  account. `GET /v1/billing/plan` shows the active higher tier before a lower or ended one. A renewal starts when the current period ends. Included usage is ordinary wallet credit, credited once at purchase, and does not
  expire when the period ends. The plan is a pass renewed by hand, not a subscription.
- **Orders.** `POST /v1/billing/orders` with `{"amount_inr": N}` (credit top-up) or `{"plan": "starter"|"pro"|"plus"}` or
  `{"plan": "pilot", "seats": 1..10, "team_name": "..."}` (team name only when seats is 2 or more). The server works out the amount.
  `GET /v1/billing/plan` returns `{plan, seats, team_name, active_until}` for the buyer's latest plan (a past date means it ended).
- **Activation** happens only in the signed webhook, from the order we recorded: idempotent steps with the entitlement row written last,
  so a retry after a failure completes the work. A redelivered event id is acknowledged without work (`billing_webhook_events`).
- **Not built yet (from the 2026-10-31 review, docs/pricing_page.md and the research notes):** refund and dispute events (a refund is manual in the
  Razorpay dashboard for now and does not reverse credit or end a plan), a scheduled run of the reconcile job (`python -m app.services.billing_reconcile --apply` fetches captured payments whose webhook never
  arrived and activates them; it exists but nothing runs it yet), GST receipts, expiry of included usage, and scheduling the abandoned-reservation sweep (`ingestion.admin` settles reservations only
  when someone runs it).

## The credit formula

```
credit_usd = (paise / 100) / KELL_INR_PER_USD / (1 + KELL_MARGIN)
```

The buyer pays the provider's dollar cost converted to rupees, plus the margin. Example, with the **placeholder** defaults (`KELL_INR_PER_USD=90`, `KELL_MARGIN=0.5`): ₹100 buys `100 / 90 / 1.5 = $0.74` of provider usage. The defaults are placeholders and not advice.

The server derives the balance from `provider_call_ledger` (credits minus settled or held spend); nothing is stored that can drift from what was spent.

## What the website shows

**Update 2026-10-31:** the credit top-up is back on `/pricing` (commit `b7507da0` had removed it), now that Razorpay has approved the account. It is the component `components/billing/TopUp.tsx`, below the plan cards; the bullets below describe it. Plan purchases (Pilot, Pro) are separate and are not part of this block. The page also shows a "By paying you accept the Terms and the Refund policy" line above the Pay button (no pre-ticked box) and a footer row of Terms, Privacy, Refunds and cancellation, and Contact. Margin and GST come from `NEXT_PUBLIC_KELL_MARGIN_PCT` and `NEXT_PUBLIC_KELL_GST_MODE` and the page works when both are unset (it says "plus a margin" with no number, and that GST treatment is being confirmed). While the server answers 503 the page shows the plans and "Buying credits isn't switched on yet", with no Pay button. If Checkout reports success and the balance has not changed after about a minute (24 looks, 2.5 s apart), the page says the confirmation hasn't arrived and links to Contact with the order id. `/legal/refunds` still says it is not published; it stays that way until the founders supply the text.

**Plan purchases and a person’s billing page (2026-10-31).** Built to the contract in `docs/pricing_page.md`, Phase 2, and tested against a fake `fetch` until the backend is live. The Pilot and Pro buttons on the cards go straight to their payment blocks (`#buy-pilot`, `#buy-pro`); Teams and Enterprise stay “contact us”. The cards carry no status caption or “not included” line. While the server has no plan endpoint (`GET /v1/billing/plan` answers 404) or has billing off (503), each block says plans aren’t switched on yet and nothing is charged. `NEXT_PUBLIC_KELL_BUY_PLANS=off` hides the blocks and sends the cards back to Contact. `components/billing/BuyPlan.tsx` serves both plans. **Pro** (one person) is a 30-day pass paid once and renewed by hand, not a subscription: the order is `{"plan":"pro"}`; an active Pro shows the end date and a Renew button; an ended one says so and can be bought again; it says Pro is for public or non-sensitive material only. **Pilot** (a team) has a team name (2 to 60 characters), a seat picker of 3 to 10, the live total (price from `lib/plans.ts` times seats, before GST), the start date and the end date 30 days later, what is and is not included, and “Start the pilot”; the order is `{"plan":"pilot","seats":N,"team_name":"..."}`. Bounds are checked before any request, neither request carries an amount, and one pilot per account: an active pilot shows “active until” with the team and seats, and an ended one points to Contact instead of selling a second. After Checkout says paid the page shows “waiting for confirmation” and looks at `GET /v1/billing/plan` for about a minute; the plan shows as active only when the server reports it (for a renewal, only when the end date moves). `lib/plans.ts` is the only place prices live (`priceInr`). **`/account/billing`** is a person’s own page, linked from the account index: their plan (Pro, when purchases are on), prepaid credit with the balance, the list of top-ups so far, and the legal links; signed out it sends them to sign in and back to the same page. Not built: Teams and Enterprise stay “contact us”, and there are no invoices or receipts yet (GST invoices are planned).


`routing_kell_frontend/app/pricing/page.tsx` (route `/pricing`, linked from the header, the footer and the account page), logic in `lib/billing.ts`:

- **Plans** (data in `lib/plans.ts`, kept in step with the Plans table above): Pro, Teams, Enterprise and Pilot with the founders' prices in rupees before GST. Every card is labelled "Not available online yet" (Enterprise: "Planned"; Pilot: "Invoiced directly") and links to `/contact` to ask; there is **no subscription checkout**. The deck's comparisons with another company's prices and its savings percentages are not shown.
- The rule in words, with a worked example labelled "Illustration, not a quote". With no margin configured the example has no number in it.
- What credits are and are not, the top-up range, the data path, GST wording.
- Signed-in people: the balance in rupees (balance in dollars divided by the dollars one rupee buys, from the server's own rate), preset amounts (₹100, ₹500, ₹1,000, ₹5,000) or a custom whole-rupee amount, and a Razorpay Checkout button.
- **Billing off:** when the server answers 503 the page shows "Buying credits isn't switched on yet" and no pay button.
- **A payment is not a credit.** When Checkout reports success the page says it is waiting for confirmation and looks for the new credit a few times. Credits are added only by the signed webhook; a forged success page changes no balance.

Footer links: Terms (`/docs/legal/terms`) and Privacy (`/docs/legal/privacy`) already existed in the legal section; **Refunds and cancellation** (`/legal/refunds`) is a placeholder that says it is not published; Contact (`/contact`). Razorpay needs these live before going live.

Copy rules kept: no savings numbers or headline claims, no zero-egress or SOC 2 claim (the page says there is no SOC 2 report or penetration test yet), the data path is stated (prompt and repo facts go to our server, the task goes to the model provider), sample numbers are never shown as measured, the B2B pilot lead is never named.

## Endpoints (hosted MCP origin, website-session Bearer)

| Method and path | What |
|---|---|
| `GET /v1/billing/wallet` | Balance, last 50 credits, `usd_per_100_inr`, `min_inr`, `max_inr`. |
| `POST /v1/billing/orders` `{"amount_inr": N}` | Creates a Razorpay order for Checkout; returns `order_id`, `amount_paise`, `key_id`, `credit_usd`. Records the order. |
| `POST /v1/billing/webhook` | Razorpay's `payment.captured` / `order.paid`. HMAC-SHA256 of the raw body with the webhook secret; matched to an organization by the order **we** recorded; `payment_ref` is unique so a repeat credits once. |

All three return 503 until the environment below is set. The website's origin must be in `DELEGATE_ALLOWED_ORIGINS` (same allow-list as rooms and chat).

## Environment

Backend (hosted MCP service):

| Variable | Meaning |
|---|---|
| `KELL_WALLETS=on` | Turns wallets on. A person's wallet account is their own user id (no hidden organization); its policy needs a positive balance. `KELL_PERSONAL_ORGS=on` still works as the old name. |
| `KELL_STARTER_INCLUDED_USD`, `KELL_PRO_INCLUDED_USD`, `KELL_PLUS_INCLUDED_USD`, `KELL_PILOT_SEAT_USD` | Usage included with a plan, in dollars: Starter (default 1), Pro (6), Plus (12) and per Pilot seat (4). Fixed amounts, independent of the margin. |
| `KELL_ORDERS_PER_HOUR` | Payment attempts one account may start an hour (default 10). |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Razorpay API keys. Test-mode keys work with no other change. |
| `RAZORPAY_WEBHOOK_SECRET` | Needed for the webhook to credit anything. |
| `KELL_INR_PER_USD` | Rupees per dollar used for pricing (default 90, placeholder). |
| `KELL_MARGIN` | Margin as a fraction (default 0.5, placeholder, **not confirmed**). |
| `DELEGATE_ALLOWED_ORIGINS` | Must include the website origin. |

Website (build-time, public):

| Variable | Meaning |
|---|---|
| `NEXT_PUBLIC_KELL_BUY_PLANS` | Unset or anything but `off`: the Pilot and Pro purchase blocks are on `/pricing` and Pro is on `/account/billing`. `off` hides them. |
| `NEXT_PUBLIC_KELL_MARGIN_PCT` | The margin as a percent (50 means 50%), for the page text and worked example. Keep it equal to `KELL_MARGIN × 100`. Unset: the page says "plus a margin". |
| `NEXT_PUBLIC_KELL_GST_MODE` | `inclusive` or `extra`. Unset: the page says GST treatment is being confirmed. |

The Razorpay webhook URL to register is `https://<mcp origin>/v1/billing/webhook`, with the events `payment.captured` and `order.paid`.

## Go-live steps

1. Set the backend variables in test mode; place a ₹100 test order from `/pricing`; confirm the webhook credits once and a repeat delivery credits nothing.
2. Confirm the margin and GST decisions below; set the website variables to match; redeploy both.
3. Publish Terms, Privacy and Refund/Cancellation (lawyer-reviewed) at the footer links.
4. Switch Razorpay to live keys on the interim account.

## Open decisions

- **Margin.** 50% is a placeholder. The founders have not confirmed it. The page and the server must use the same number.
- **GST.** Inclusive in the amount or added on top: undecided. The page says so until set. Whether GST registration is required at the expected volume is part of the tax checklist.
- **Whose PAN and bank account.** Which founder's sole proprietorship holds the interim Razorpay account, and the plan and date to move it to the company.
- **Refund policy.** What is refundable (unused credit only? a window?) and how a cancellation works.
- **Exchange rate.** A fixed `KELL_INR_PER_USD` is simple but drifts from the market; decide how often it is reviewed.

## Legal checklist (to take to a lawyer; none of this is advice)

- **Pre-incorporation liability.** Contracts and payments before the company exists are the founder's personal ones. Decide how that is disclosed and how obligations move to the company.
- **Prepaid payment instruments.** Credits that only buy our own service, are non-transferable and cannot be cashed out are meant to fit a closed system. Confirm with counsel that the design (balance size, expiry, no cash-out, no transfer) stays inside the RBI rules for closed-system instruments.
- **GST.** Registration threshold, rate and place of supply for a digital service; invoice requirements; how to treat prepaid credit (when supply happens).
- **DPDP Act and data processing agreements.** Notice and consent, a grievance contact, children's data, cross-border transfer to model providers, and DPAs with each provider and with Razorpay. Drafts are in `legal/dpdp`.
- **Provider resale terms.** Check that each model provider's terms allow us to run their models for third parties and to charge for it; the managed tier is open models only, and each provider's terms still need to be read.
- **Razorpay requirements.** Terms, Privacy, Refund/Cancellation and Contact pages live and reachable; business description matches what is sold; KYC for the account holder.
- **Records.** Keep the order and payment records the webhook writes; they are the audit trail for refunds and tax.
