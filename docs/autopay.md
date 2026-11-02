# Auto-renew with Razorpay Subscriptions (2026-11-02)

Status: **built and tested, off until enabled in the Razorpay dashboard.** Nothing is charged unless a buyer ticks the box
and authorises the mandate in Checkout. The one-person First Trial is not offered with auto-renew.

## What is built

- **The opt-in** on the Starter, Pro and Plus cards: "Renew automatically every 30 days. Your bank authorises a recurring
  payment; cancel from Billing before the next charge." Unchecked by default. The First Trial has no checkbox (it is one
  per account, so a second charge would be refused by the entitlement index and need manual review).
- **The server** (`app/services/subscriptions.py`): `POST /v1/billing/orders {"plan": "pro", "autopay": true}` makes, once
  per amount/period, a Razorpay Plan (`period: "daily"`, `interval: <period_days>`, so exactly 30 days; Razorpay's minimum
  daily interval is 7) and a Subscription (`total_count: 120` - about 9.9 years, inside the 10-year ceiling; `quantity: 1`;
  `customer_notify: true`). It records `billing_subscriptions` with the **exact amount** every charge must be and returns
  `subscription_id`; Checkout then authorises the mandate (card, UPI Autopay or eMandate).
- **Activation** only in the signed webhook: `subscription.charged` (and `subscription.authenticated` when it carries a
  payment) calls `plans.activate` (idempotent by payment id), after checking the currency is INR and the amount equals the
  recorded `amount_paise`. A mismatch is logged for manual review and **not** credited. Razorpay's `authenticated` payload
  often carries no payment entity (their docs sample) and may arrive after `charged`, so it only marks the row and never
  downgrades an active one. `subscription.pending/halted/cancelled/completed/paused/resumed` update the row's status.
- **The buyer's view** on `/account/billing`: "Auto-renew: <plan> every 30 days, next charge about <date>. Cancel
  auto-renew." Cancelling stops future charges; the plan already paid for keeps running to its end. Refunds remain manual.
- **Endpoints:** `POST /v1/billing/orders` with `autopay: true`; `GET /v1/billing/subscription`;
  `POST /v1/billing/subscription/cancel`. Migration `169_autopay.sql` adds the Razorpay-plan cache columns and
  `billing_subscriptions`.

## Razorpay dashboard steps (founders)

1. **Enable Subscriptions**: Dashboard -> Subscriptions -> Settings -> enable Card, UPI and eMandate. Cards and UPI allow
   recurring charges up to Rs 15,000; every kell plan is Rs 2,000 or less. Also enable **Flash Checkout**
   (Account & Settings -> Checkout Features), which Razorpay requires for Subscriptions.
2. **Webhooks**: add these events to the existing endpoint `https://mcp.kell.org.in/v1/billing/webhook` (same
   `RAZORPAY_WEBHOOK_SECRET`): `subscription.authenticated`, `subscription.charged`, `subscription.cancelled`,
   `subscription.pending`, `subscription.halted`, `subscription.completed`. The existing `payment.captured` and
   `order.paid` stay.
3. **Test in test mode**: tick the box on Pro, authorise with a test card or UPI, confirm the plan appears within about a
   minute, the mandate shows on `/account/billing`, Cancel works, and a subsequent test charge activates the next period.
4. **Live**: switch to live keys and repeat once with a small plan, then cancel the test mandate.

## Rules and limits

- **Razorpay plans are immutable.** A dashboard edit to a plan's price or period makes a new Razorpay plan on the next
  sign-up; **existing mandates keep charging the old amount** until cancelled and re-subscribed. Decide what buyers are
  told before changing a price while mandates run.
- **The amount check is exact.** A charge at another amount (for example after a manual Razorpay change) is logged and
  not credited; reconcile by hand.
- **Cancellation** is immediate (`cancel_at_cycle_end: false`): Razorpay cancels the mandate and it cannot be reactivated.
  The plan already paid for keeps running to its end; no refund is automatic. Razorpay also auto-generates its own
  invoice for every cycle; that is not the GST tax invoice (ours stays manual for now).
- **RBI/e-mandate:** `customer_notify: true` means Razorpay handles the mandate communication and pre-debit notification.
  The terms should describe auto-renew, the 30-day cycle, the cancellation path and that refunds are manual.
- **Not built:** refunds (manual), proration, changing a plan on a running mandate, a retry UI (Razorpay retries; the
  status is shown on the account page).

## Go-live checklist

- [ ] Subscriptions enabled in the Razorpay dashboard (test, then live).
- [ ] Webhook events above registered and recent deliveries show 200.
- [ ] One test mandate authorised; plan activated from `subscription.authenticated`/`charged`; amount check passed.
- [ ] Cancel from `/account/billing`; no further charge; plan still active to its end.
- [ ] Price-edit policy decided (existing mandates keep the old amount).
