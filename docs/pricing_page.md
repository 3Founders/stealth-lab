# Pricing page: what to change in the website (2026-10-31)

For the Frontend session. Owner: the founders. Read [billing.md](billing.md) first (plans, endpoints, env vars, the legal
checklist) and [pricing_research_2026-10.md](pricing_research_2026-10.md) for why the numbers are open. This page says
what to build in `routing_kell_frontend` and in what order.

## Where we are

- `/pricing` (`app/pricing/page.tsx`) shows four plan cards from `lib/plans.ts`: Pro ₹999 a month, Teams ₹1,800 per seat a
  month, Enterprise custom, Pilot ₹649 per seat for two weeks. Prices are in rupees, before GST. Every card links to
  `/contact`. There is **no Pay button anywhere**: commit `b7507da0` removed the credit top-up block.
- `lib/billing.ts` still has `billingApi().wallet()`, `billingApi().order(amountInr)`, `loadCheckout()` and `openCheckout()`.
  Nothing calls them. The old top-up UI is recoverable with
  `git show b7507da0^:routing_kell_frontend/app/pricing/page.tsx`.
- Razorpay approved the account. The hosted server still answers `503` on `/v1/billing/*` until the founders set the
  Razorpay keys on Railway, so the page must keep working in that state.
- The live `/legal/refunds` page says "not published yet".

## The rules that apply to everything below

- Prices come from `lib/plans.ts` and the config values below. Do not scatter rupee amounts through components.
- **Nothing a browser sends sets a price.** The page sends a plan name and a seat count or an amount to top up; the server
  works out what to charge.
- **A payment is not a credit and not a plan.** When Razorpay's window reports success, show "waiting for confirmation" and
  look for the new balance or plan a few times. Only the server's signed webhook changes anything.
- Show **no savings numbers**, no comparisons with Claude's or anyone else's prices, no sample data as measured, no zero-egress or
  SOC 2 claim, and never name the B2B pilot lead. Keep the existing data-path and no-SOC-2 wording.
- Keep "prices in rupees, before GST" until the GST decision is made (see Config).
- Credits and plans are for the open-model tier only. Do not mention reselling Claude or Codex.
- Tool and image allowances are **not** priced yet (usage is not metered against the wallet). Do not promise a number of
  images or tool calls in a plan.

## Config (set at build time, never hardcode)

| Variable | Meaning | If unset |
|---|---|---|
| `NEXT_PUBLIC_KELL_MARGIN_PCT` | Margin on extra usage, as a percent; must equal the server's `KELL_MARGIN × 100` | the page says "plus a margin" and shows no worked example number |
| `NEXT_PUBLIC_KELL_GST_MODE` | `inclusive`, `extra` or unset | the page says GST treatment is being confirmed |

Both are open founder decisions (margin; GST). Build for them being unset.

## Phase 1: credit top-up (no backend change)

1. **Bring back the credit top-up**, headed "Extra usage: prepaid credits", below the plan cards.
   - Signed out: a "Sign in to add credit" link that returns to `/pricing`.
   - Signed in: the balance in rupees (as the removed block computed it from `wallet()`), presets ₹100, ₹500, ₹1,000, ₹5,000
     and a custom whole-rupee amount between the server's `min_inr` and `max_inr` (₹100 to ₹50,000), then a Pay button that calls
     `order()` and `openCheckout()`.
   - A short rule in words: "You pay the model provider's cost converted to rupees, plus a margin" and the worked example
     labelled "Illustration, not a quote". With no margin configured the example shows no number.
   - What credits are: buy kell's own service only; non-transferable; never redeemable for cash. State the top-up range.
2. **States**, each with its own copy and a test:
   - Billing off (`503`): "Buying credits isn't switched on yet." No Pay button.
   - Signed out (`401`), checkout closed, payment failed, server error with the server's message.
   - After success: "waiting for confirmation", then the new balance once the wallet shows it; if it does not arrive in
     about a minute, say so and link to `/contact` with the order id.
3. **Footer and checkout links:** Terms (`/docs/legal/terms`), Privacy (`/docs/legal/privacy`), Refunds and cancellation
   (`/legal/refunds`), Contact (`/contact`). Add a line above the Pay button: "By paying you accept the Terms and the Refund
   policy", with both linked. No pre-ticked box.
4. **Refund and cancellation page** (`/legal/refunds`): replace the placeholder with a real page **only when the founders
   give you the policy text**. Until then keep "not published yet" and do not invent terms. Razorpay needs this page live;
   tell the founders it is the blocker.
5. **Tests (vitest):** the order request shape (amount only, no price field), amount validation against `min_inr` and
   `max_inr`, every state above, and that a "paid" message from Checkout alone never shows a changed balance.

## Phase 2: the Pilot (and Pro) purchase. The founders want the Pilot to work

The Pilot is the first thing customers will pay for, so build it in parallel with Phase 1, not after. The backend plan orders
are being built at the same time from the contract below. **The contract is fixed: build to it.** Test with a fake `fetch`
until the backend is live, and keep the buy buttons behind `NEXT_PUBLIC_KELL_BUY_PLANS=on` (off by default; the founders turn
it on when the backend answers). With the flag off the cards look as they do now.

### Contract (server side, being built; paths and fields are final)

| Method and path | Body | Returns |
|---|---|---|
| `POST /v1/billing/orders` | `{"plan": "pilot", "seats": 3..10}` or `{"plan": "pro"}` | `order_id`, `amount_paise`, `key_id`, `plan`, `seats`, `period_days` (14 for pilot, 30 for pro) |
| `GET /v1/billing/plan` | none | `{"plan": "pilot"\|"pro"\|null, "seats": N, "active_until": "ISO date or null"}` |

The server computes the amount (Pilot: ₹649 per seat times seats; Pro: ₹999). The page never sends an amount for a plan. The
existing credit order (`{"amount_inr": N}`) keeps working unchanged. Errors use the same shape as today: `503` billing off,
`401` signed out, `400` with `error.message` for a bad seat count or an unknown plan.

### Pilot card (build this first)

1. A seat picker limited to **3 to 10** (a stepper or a select; no free typing). Below 3 or above 10 never sends a request.
2. The total, live: `₹649 × seats`, and "before GST" (or the GST sentence from the config).
3. The two dates: starts today, ends in 14 days (`period_days`), shown as plain dates.
4. A **"Start the pilot"** button. One payment covers the whole team. Signed out: "Sign in to start the pilot", returning to
   `/pricing`.
5. What the pilot includes and does not include, from `lib/plans.ts` (MCP only; not the website assistant room or the team
   admin pages). Do not state a dollar figure for the included usage; the founders have not set it.
6. The line "By paying you accept the Terms and the Refund policy", both linked.
7. After Checkout success: "Waiting for confirmation", then poll `GET /v1/billing/plan` for up to about a minute. When it
   returns `plan: "pilot"`, show "Pilot active until <date> for <seats> seats" and what to do next: connect your tool at
   `/docs` (link to the setup guide) and contact us for the joint report. If it does not arrive, say so and link to `/contact`
   with the order id.
8. If the person already has an active pilot, show "Active until <date>" instead of the buy button (no second pilot from the
   same account).

### Pro card (second)

A "Buy Pro (30 days)" button. This is a **one-month pass, paid once and renewed by hand**, not a subscription. Label it
plainly: "30 days of Pro. Renew when it ends." Active Pro shows "Active until <date>" and "Renew". Same states and
confirmation flow as the Pilot. Pro is for public or non-sensitive material only: say so next to the button.

### Teams and Enterprise

Stay "contact us", with their existing status labels.

### Labels

Remove "Not available online yet" (Pro) and "Invoiced directly" (Pilot) only when `NEXT_PUBLIC_KELL_BUY_PLANS=on`. Keep them
on Teams and Enterprise.

### Tests (vitest)

Seat bounds (2 and 11 are refused before any request), that the request carries no price, the total and dates, every state
(off, signed out, closed, failed, waiting, active, already active, renew), and that a "paid" message from Checkout alone never
shows an active plan.

## Open decisions the page depends on (founders, not Frontend)

| Decision | Effect on the page |
|---|---|
| Margin on extra usage | `NEXT_PUBLIC_KELL_MARGIN_PCT` and the worked example |
| Prices with or without GST | `NEXT_PUBLIC_KELL_GST_MODE` and the sentence under the prices |
| Refund policy text | the `/legal/refunds` page |
| Included usage per seat for Pro and Pilot | the "included" wording on the cards; do not state a dollar figure until decided (the server needs it too) |
| Whether Pro stays a one-month pass or becomes recurring | the Phase 2 button label |

## Done means

- `tsc` clean and the site's vitest suite passing, with the counts pasted in the commit.
- With billing off (today's server), the page shows the plans and the "not switched on" message, and no Pay button.
- With billing on in test mode, a ₹100 test payment raises the balance once, and a repeated webhook does not.
- With `NEXT_PUBLIC_KELL_BUY_PLANS=on` and the backend live, a test Pilot payment for 3 seats shows the pilot active for 14 days.
- `docs/billing.md`'s "What the website shows" section is updated to match.
