# Website brief for the pricing and billing work (2026-11-01)

For the website lane (`routing_kell_frontend`). This is the one place that lists everything: what is decided, what the backend already does, what to build, and what NOT to build yet. It supersedes the older instructions in [pricing_page.md](pricing_page.md) where they differ; keep using that file for the layout rules (decoy layout, honest-pricing rules) and the Pilot card details. Numbers and reasoning: [unit_economics_2026-10.md](unit_economics_2026-10.md). Server behaviour: [billing.md](billing.md).

## 0. Where things stand

| Item | State |
|---|---|
| Backend: plans (Starter, Pro, Plus, Pilot), add-on credit packs, plan and wallet endpoints, webhook, reconcile job | built, tested, pushed to `main` |
| Migrations 164 applied on production; **165 (plan tiers) and 166 (credit packs) not yet applied** | pending (the founders run `scripts/migrate.py`) |
| Server code with all of this | **not deployed**, so billing still answers 503 |
| Razorpay | live keys are on Railway but have never taken a test payment |
| Refund and cancellation page | placeholder ("not published yet"); needs the founders' text |
| Referral and promo codes | **not built** (legal briefing: [referral_legality_2026-11.md](referral_legality_2026-11.md)) |

So: build against the contract below with a fake `fetch`, keep every buy button behind `NEXT_PUBLIC_KELL_BUY_PLANS=on` (off by default), and do not tell anyone the flag can go on. The founders will say when.

## 1. Plans (prices live only in `lib/plans.ts`; the server computes every amount)

| Plan | Price (before GST) | Period | Usage included | Notes |
|---|---|---|---|---|
| Starter | Rs 200 | 30 days | $1 | the low anchor |
| Pro | Rs 1,200 | 30 days | $6 | the step up |
| **Plus** | Rs 2,000 | 30 days | $12 (twice Pro) | **recommended** |
| Pilot | Rs 649 per seat | **30 days** | $4 per seat | one seat = an individual pilot (no team name); 2 to 10 seats = a team workspace with a required team name; one pilot per account (409) |
| Teams, Enterprise | contact us | | | not sold online |

Layout (from `pricing_page.md`): Plus carries a plain "Recommended" label and is first on a phone; each card shows the per-day figure ("about Rs 7 / Rs 40 / Rs 67 a day") and the usage in dollars; one computed sentence "Plus gives twice Pro's usage for Rs 800 more". No countdowns, no "only N left", no crossed-out prices, no pre-selected upsell.
Keep the Pilot card's visibility in ONE constant: it may become reachable only with a code (pending).
Change existing text: Pilot is "for a month" (not 2 weeks; use `period_days` for the end date); remove "or bring your own key" and "Public or non-sensitive material only" from `lib/plans.ts`.
You may show what the usage buys as an **estimate**: about 19 delegated tasks per $1, 115 per $6, 230 per $12 (average task about $0.05; ranges widely).

## 2. Additional kell credits (new)

Fixed packs a person adds on top of a plan. Never ask the server for a price you computed; read the list.

| Pack id | Price | Credit | Rs per credit-dollar |
|---|---|---|---|
| `credits_500` | Rs 500 | $2.50 | 200.0 |
| `credits_1000` | Rs 1,000 | $5.25 | 190.5 |
| `credits_2500` | Rs 2,500 | $13.50 | 185.2 |
| `credits_5000` | Rs 5,000 | $28.00 | 178.6 |

Rules to show: for people with an **active plan** (the server refuses others with 403 and the message "additional credits are for people with an active plan; buy a plan first"); the credit does not expire; it is spendable only on kell (not transferable, not withdrawable, no cash-out); a bigger pack gives a little more per rupee, and a plan is always the better value per dollar.
- `GET /v1/billing/wallet` (signed in) now also returns `packs` (id, `price_inr`, `credit_usd`, `inr_per_usd`) and `packs_need_plan`. Render the cards from this list, not from constants. If `packs_need_plan` is true and `GET /v1/billing/plan` shows no active plan, show the cards disabled with "Available with an active plan" and a link to the plans.
- Buy: `POST /v1/billing/orders {"pack": "credits_1000"}` returns `order_id`, `amount_paise`, `key_id`, `pack`, `credit_usd`; open Razorpay Checkout as for a plan; credit only appears when the signed webhook arrives, so show "waiting for confirmation" and poll the wallet (balance and credits list) for about a minute, then the order id and a Contact link.
- **Remove the free-form top-up block** ("Extra usage: prepaid credits" with presets and a custom amount). The server now refuses `{"amount_inr": N}` unless `freeform_topup` is true in the wallet response (off by default), because it is cheaper per dollar than the plans and packs.
Put the packs on `/pricing` (below the plans) and on `/account/billing` (next to the balance).

## 3. Billing wiring (the contract; behind the flag)

| Call | Body | Returns |
|---|---|---|
| `POST /v1/billing/orders` | `{"plan": "starter"\|"pro"\|"plus"}` or `{"plan": "pilot", "seats": 1..10, "team_name": "..." only when seats >= 2}` or `{"pack": "<pack id>"}` | `order_id`, `amount_paise`, `currency`, `key_id` plus `plan`/`seats`/`period_days` or `pack`/`credit_usd` |
| `GET /v1/billing/plan` | none | `{plan: "starter"\|"pro"\|"plus"\|"pilot"\|null, seats, team_name, active_until}` (active higher tier first; a past date means ended) |
| `GET /v1/billing/wallet` | none | balance, last credits, `usd_per_100_inr`, `packs`, `packs_need_plan`, `freeform_topup` |

Errors keep one shape (`error.message`): 503 billing off, 401 signed out, 400 bad request (show the message), 403 pack without a plan, 409 second pilot, 429 more than 10 payment attempts an hour. A "paid" message from Checkout alone never changes the page: wait for `GET /v1/billing/plan` or the wallet to change. A renewal is confirmed only when `active_until` moves. Orders never carry an amount.
States to cover: off (503, no pay button), signed out, closed, failed, waiting, active ("Active until <date>" plus Renew), already had a pilot, pack needs a plan.

## 4. Copy rules (non-negotiable)

- No savings numbers, no comparisons with Claude's or anyone's prices, no sample data presented as measured, never name the pilot lead.
- **Do not write "zero data retention" or any security or privacy guarantee.** The founders state the model providers are configured for zero retention, but the data agreements are not signed. You may say prices are before GST and that prompts and repository facts go to kell's server and then to the model provider.
- No bring-your-own-key anywhere (not offered for now). Same product for everyone (no feature differences by plan, only the usage included).
- The harness (kell's branded OpenCode) is free software and the plans pay for model usage; **do not build a harness page yet** (pending).

## 5. Pending: do NOT build yet

1. Referral and promo codes (only an empty, hidden "Have a code?" slot in the checkout summary).
2. Whether the Pilot price is public.
3. A harness download page.
4. Per-call cost receipts and a usage page; image metering.
5. Refund and cancellation page text (needs the founders).

## 6. Done means

`tsc` clean and the site's vitest suite passing with the counts pasted in the commit; with the flag off the page shows plans and packs as information and the old "not switched on" state; fake-fetch tests cover every state above, that no request carries a price, and that the pack list is rendered from the wallet response; `docs/billing.md` "What the website shows" updated.
