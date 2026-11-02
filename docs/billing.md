# Billing: prepaid credits in rupees

Status (2026-11-02): the website side (`/pricing`) and the backend side (`backend/app/mcp_server/billing_routes.py`, `backend/app/services/wallet.py`) are built, and **production is taking real money**: the buy flag is on and the Razorpay keys in production are LIVE (founder decision, 2026-11-02). That is ahead of the go-live gates below: the refund/cancellation page is still unpublished, GST treatment is undecided, and the webhook registration and a first live payment have not been verified. "Live payments: current state" and the pre-flight checklist below are the list of what to confirm next. Several numbers are deliberately **not final** (see Open decisions); the site reads them from settings and says "not set" when they are absent.

## Plans (source: `kell_decks/4_kell_router_and_coding_agents.html`, slides 7-8)

These are the prices the founders chose. All in rupees, **before GST**. The credits below are how "extra usage" is bought on every plan.

| Plan | Price | For | Includes |
|---|---|---|---|
| **Starter** | ₹200 for 30 days | One person, light use | $1 of usage included. |
| **Pro** | ₹1,200 for 30 days | One person or a small student team | $6 of usage included; connect an assistant or coding tool; a recommended plan per task; a short cost note after each task. |
| **Plus** | ₹2,000 for 30 days | One person who uses it daily | $12 of usage included (twice Pro). The tier the page recommends. |
| **Teams** | ₹1,800 per seat a month | Three or more seats | Everything in Pro; usage shared across the team; per-person and monthly spending limits and one stop-all switch; members, invitations, usage by person, exportable record; a shared library that learns from the team's results. |
| **Enterprise** | Custom, by annual contract | Private code or compliance needs | Everything in Teams; a no-retention provider option under a signed agreement; runs inside the customer's systems with their sign-in; private tests on their own tasks; security review, DPA and support terms. |
| **Pilot** | ₹649 per seat for a month | One to ten seats; a one-seat pilot is the individual pilot, bought online only with a referral code | Assistant or coding tool connected to the kell MCP; recommendations; routine steps on lower-cost models with usage included; a kept record; a joint report (cost per solved task, with and without kell). Not included: the website assistant room and team admin pages. A team pilot (two or more seats) is invoiced directly. |

Extra usage on every plan: bought in advance as credits at the provider's cost plus a published margin (formula below).

**What is actually live, which the website must say plainly:**

- Buying credits in advance: built and tested, not yet switched on.
- Pro and Teams monthly/per-seat billing and GST invoices: **planned, not built**. Until they are, the page shows the plans with "Not yet available online" and a contact route. The one-seat pilot is bought online behind a referral code; a team pilot is invoiced directly.
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
  account. **Since 2026-11-02 the numbers live in `billing_plan_rows` (migration 167) and are edited in the admin dashboard's Billing
  page** (`/v1/admin/billing/plans`, audited). `lib/plans.ts` keeps the built-in copy: the seed of the table and what the site shows
  while billing is off. A price change applies to orders made after it; an order already started keeps the amount it was made with.
  The old `KELL_*_INCLUDED_USD` environment overrides are retired (the dashboard is the one knob). `GET /v1/billing/plan` shows the
  active higher tier before a lower or ended one. A renewal starts when the current period ends. Included usage is ordinary wallet
  credit, credited once at purchase, and does not expire when the period ends. The plan is a pass renewed by hand, not a subscription.
- **The one-seat pilot is the individual pilot, and it needs a referral code.** `{"plan": "pilot", "seats": 1, "referral_code": "..."}`
  is refused unless the code is an active row of `billing_referral_codes`; the code is recorded on the order. Two or more seats is the
  team pilot and needs no code (it is invoiced directly).
- **Orders.** `POST /v1/billing/orders` with `{"amount_inr": N}` (credit top-up) or `{"plan": "starter"|"pro"|"plus"}` or
  `{"plan": "pilot", "seats": 1..10, "team_name": "...", "referral_code": "..."}` (team name only when seats is 2 or more; code only
  needed for one seat). The server works out the amount. `GET /v1/billing/plan` returns `{plan, seats, team_name, active_until}` for
  the buyer's latest plan (a past date means it ended).
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

**Rewritten 2026-11-01, revised 2026-11-02 (payment in the cards, the referral code, the individual pilot).** Everything below is built in `routing_kell_frontend` and tested against a fake `fetch`. The buy controls are behind `NEXT_PUBLIC_KELL_BUY_PLANS=on` (only the exact value `on` turns them on). **Production has the flag on and LIVE Razorpay keys (founder, 2026-11-02): it can take real money now** - see "Live payments: current state". The referral section and the individual pilot described below are in this working tree only: not committed, not deployed, and migrations 167/168 are not applied in production. The deployed build's billing routes are configured (`GET /v1/billing/wallet` answers 401, not 503).

`/pricing` (`app/pricing/page.tsx`), `/account/billing` (`app/account/billing/page.tsx`), logic in `lib/billing.ts`, the built-in plan copy (and the fallback numbers) in `lib/plans.ts`:

- **The ladder.** Starter ₹200, Pro ₹1,200, Plus ₹2,000, each 30 days, with $1, $6 and $12 of usage included. Plus is the recommended card: raised, a plain "Recommended" label (never "most popular"), and first on a phone. Each card shows the per-day figure ("about ₹7 / ₹40 / ₹67 a day"). It shows no usage line, no "lower-cost models" line and no task estimate: the included usage lives in the plan data only to compute the Plus-versus-Pro sentence. The sentence "Plus gives twice Pro's usage for ₹800 more" is computed from the data. No countdown, no scarcity, no crossed-out price, nothing pre-selected. Everything derived is in `lib/plans.ts` (`perDay`, `plusVersusPro`), so a changed price changes the sentences with it. **Numbers:** the page asks `GET /v1/billing/plans` once and merges the server's numbers over the built-in copy (`withRows` in `lib/plans.ts`); while billing is off it shows the built-in copy. A buyable plan the server does not return is not shown (it is not being sold). Card sentences never come from the server.
- **Teams and Enterprise** stay "contact us". The Enterprise "no-retention provider option" line was removed: there is no zero-retention or other security claim anywhere on the site (the data agreements are not signed). "Or bring your own key" and "Public or non-sensitive material only" are gone from Pro.
- **Buying (flag on): inside the plan's own card.** There is no separate payment block and no heading or form of its own: each of Starter, Pro and Plus carries its buy button and its states (`components/billing/PlanBuy.tsx`); the page loads the account's plan once and every card reads it. The run-dates line was removed (2026-11-02): a card no longer prints "Runs from today ... a 30-day pass paid once, not a subscription"; the 30-day period is still what the order and the server use. `POST /v1/billing/orders` with `{"plan": "starter"|"pro"|"plus"}` and nothing else, never an amount. States: billing off (503, no buy button), signed out ("Sign in to buy <plan>", back to `/pricing`), checkout closed, payment failed, the server's refusal sentence, waiting for confirmation (about a minute of looking at `GET /v1/billing/plan`), active ("<plan> active until <date>" and Renew; a renewal is confirmed only when `active_until` moves), and when the confirmation does not arrive the order id with a Contact link. A payment is not a plan: a "paid" message from Checkout alone never shows an active plan. A lower plan can be bought while a higher one runs; both run and the higher applies, and the page says so. With the flag off every card links to Contact and nothing can be bought. GST is stated once, under the page heading, whether or not purchases are on.
- **The pilot is behind a referral code (2026-11-02).** Above the plans, "Have a code?" states the code's exact effect in advance - one plan for one person: Pilot, ₹649 for 30 days, with $4 of usage included, which does not expire when the plan period ends (all read from the plan data, never typed) - and takes the code. Applying it asks the server (`POST /v1/billing/codes/check`, no session, limited per address); a valid code swaps the whole plans area - the ladder, the Plus sentence and the Teams/Enterprise cards - for exactly ONE card, the one-seat Pilot, bought in that card with the code on the order. Removing the code brings the ladder back. The code is kept in this browser (`lib/referral.ts`) so it survives a sign-in round trip; the server checks it again on the order, so the page cannot unlock anything on its own. There is no seat picker, no team-name field, no countdown, no crossed-out price and no claim that the code is a saving against another plan. The contact form's reason "pilot" is still shown as "Trying it with a team".
- **Referral codes (new, 2026-11-02).** `billing_referral_codes` holds two kinds: `issued` (made by the founders in the admin dashboard, `/v1/admin/billing/referral-codes`, audited) and `personal` (made once for a buying account when the payment webhook activates a plan, so the attribution is recorded). The launch code `FIRSTTRIAL` is seeded by migration 168 (stored upper-case; matching ignores case, so "Firsttrial" as typed works) and is managed in the dashboard like any other. The buyer sees their own code on `/account/billing` ("Your code to share: ... someone who uses it unlocks the one-person Pilot plan on the pricing page. No reward is paid for sharing it."). `GET /v1/billing/referral` serves it. An order records the code it was made with (`billing_orders.referral_code`, shown on the dashboard's Orders tab). **No reward is built** for a referrer: the GST/TDS questions in `docs/referral_legality_2026-11.md` are still open. A code an order was made with can be deactivated but not deleted (the audit trail); an unused one can be deleted.
- **Additional kell credits (new).** Four fixed packs (₹500 = $2.50, ₹1,000 = $5.25, ₹2,500 = $13.50, ₹5,000 = $28.00) for people with an **active plan**. With the flag on and billing on, the cards are drawn from `GET /v1/billing/wallet` (`packs`, `packs_need_plan`), not from constants; with `packs_need_plan` and no active plan they are disabled with "Available with an active plan" and a link to the plans. Buying is `POST /v1/billing/orders {"pack": "<id>"}` (nothing else), then Checkout and the same wait: credit appears only when the signed webhook arrives, so the page polls the wallet for about a minute and then gives the order id and a Contact link. The 403 for a person with no plan is shown with the server's own sentence. While billing is off, or the flag is off, the same four packs are shown as information from `PACK_INFO` in `lib/plans.ts` (a display-only copy; keep it in step with the server's list) with no buy button. The credit never expires and is spendable only on keळ (not transferable, not withdrawable). The component is `components/billing/CreditPacks.tsx`; it shows the balance in dollars and, on `/account/billing`, the credits added so far.
- **Removed from `/pricing`:** the separate "Buy ..." blocks under the cards, the "What credits are" block (its rules are in the credits block), and the "Where your data goes" block. The page is the plans, the additional credits and the legal links.
- **Removed: the free-form top-up** (presets and a custom amount) and the margin worked example. The server refuses `{"amount_inr": N}` unless the wallet says `freeform_topup` is true (off by default), because it undercut the plans and packs. `NEXT_PUBLIC_KELL_MARGIN_PCT` is no longer read by the site. The page never says a plan is always the better value than a pack: by the numbers Starter and Pro (₹200 per dollar of usage) cost more per dollar than the ₹1,000 to ₹5,000 packs (₹190.5 to ₹178.6), and only Plus (₹166.7) beats every pack. It says what is true: a bigger pack gives a little more credit per rupee.
- **Your plan** (`/account/billing`, flag on): which plan runs and until when, or "no plan yet", with a link to `/pricing`.
- **Reserved:** the empty, hidden "Have a code?" slot in each plan card's purchase controls (`data-slot="have-a-code"`) stays empty: the code section above the plans is where codes are applied. The legal briefing is `docs/referral_legality_2026-11.md`.
- **Not built, on purpose:** a harness page, per-call cost receipts and a usage page, image metering, the refund and cancellation text (`/legal/refunds` still says it is not published).

Footer links: Terms (`/docs/legal/terms`), Privacy (`/docs/legal/privacy`), Refunds and cancellation (`/legal/refunds`, placeholder), Contact (`/contact`). Razorpay needs these live before going live.

Copy rules kept: no savings numbers or headline claims, no comparison with anyone's prices, no zero-retention, zero-egress, security or SOC 2 claim, sample numbers are never shown as measured, the B2B pilot lead is never named, no bring-your-own-key. The referral code's exact effect is stated before it is used, no reward is promised (none is built), and there is no crossed-out price or countdown.

## Endpoints (hosted MCP origin, website-session Bearer)

| Method and path | What |
|---|---|
| `GET /v1/billing/wallet` | Balance, last 50 credits, `usd_per_100_inr`, `min_inr`, `max_inr`. |
| `GET /v1/billing/plans` | The active plans' numbers from `billing_plan_rows` (`plan`, `inr`, `days`, `included_usd`). No session: the pricing page is public; it falls back to its built-in copy when this answers 503. |
| `POST /v1/billing/orders` `{"amount_inr": N}` | Creates a Razorpay order for Checkout; returns `order_id`, `amount_paise`, `key_id`, `credit_usd`. Records the order. |
| `POST /v1/billing/orders` `{"plan": ...}` | A plan order: the amount comes from `billing_plan_rows`; a one-seat Pilot also needs an active `referral_code`, recorded on the order. |
| `GET /v1/billing/plan` | The buyer's latest plan (website session). |
| `GET /v1/billing/referral` | The signed-in buyer's own personal referral code, if any (website session). No reward is built. |
| `POST /v1/billing/codes/check` `{"code": ...}` | `{"valid": true}`, or a 400 with the same sentence the order route would use. No session; limited per address (`KELL_CODE_CHECKS_PER_MIN`, default 10 a minute). |
| `POST /v1/billing/webhook` | Razorpay's `payment.captured` / `order.paid`. HMAC-SHA256 of the raw body with the webhook secret; matched to an organization by the order **we** recorded; `payment_ref` is unique so a repeat credits once. |

All of them return 503 until the environment below is set. The website's origin must be in `DELEGATE_ALLOWED_ORIGINS` (same allow-list as rooms and chat).

## Environment

Backend (hosted MCP service):

| Variable | Meaning |
|---|---|
| `KELL_WALLETS=on` | Turns wallets on. A person's wallet account is their own user id (no hidden organization); its policy needs a positive balance. `KELL_PERSONAL_ORGS=on` still works as the old name. |
| `KELL_ORDERS_PER_HOUR` | Payment attempts one account may start an hour (default 10). |
| `KELL_CODE_CHECKS_PER_MIN` | Referral-code checks one address may make a minute (default 10). The check route needs no session, so this is the only brake on guessing. |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Razorpay API keys. Test-mode keys work with no other change. |
| `RAZORPAY_WEBHOOK_SECRET` | Needed for the webhook to credit anything. |
| `KELL_INR_PER_USD` | Rupees per dollar used for pricing (default 90, placeholder). |
| `KELL_MARGIN` | Margin as a fraction (default 0.5, placeholder, **not confirmed**). |
| `DELEGATE_ALLOWED_ORIGINS` | Must include the website origin. |

Retired: `KELL_STARTER_INCLUDED_USD`, `KELL_PRO_INCLUDED_USD`, `KELL_PLUS_INCLUDED_USD` and `KELL_PILOT_SEAT_USD`. The included usage is now a row in `billing_plan_rows`, edited in the admin dashboard.

Website (build-time, public):

| Variable | Meaning |
|---|---|
| `NEXT_PUBLIC_KELL_BUY_PLANS` | Only the exact value `on` puts the buy buttons in the plan cards (and lets the code section check a code). Unset or anything else: every card links to Contact and nothing can be bought. |
| `NEXT_PUBLIC_KELL_GST_MODE` | `inclusive` or `extra`. Unset: the page says GST treatment is being confirmed. |

The Razorpay webhook URL to register is `https://<mcp origin>/v1/billing/webhook`, with the events `payment.captured` and `order.paid`.

## Go-live steps

1. Apply migrations 165, 166, 167 and 168; set the backend variables in test mode; in the admin dashboard's Billing page check the four plan rows and that the seeded FIRSTTRIAL code is listed; place a ₹100 test order and a one-seat pilot order with that code from `/pricing`; confirm the webhook credits once, a repeat delivery credits nothing, and the order row shows the code.
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
- **Referral codes.** What is built is the referee-side access code described in `docs/referral_legality_2026-11.md`: a code unlocks the one-person Pilot at its list price, the buyer sees the exact effect before using it, and no reward is paid or promised to anyone. If a referrer reward is added later, the GST and s.194R/TDS questions in that briefing must be answered first.
- **Records.** Keep the order and payment records the webhook writes; they are the audit trail for refunds and tax.
