# Per-procedure billing

> **Revision 2026-11-22 — one output, one purchase.**
>
> The product is a **one-run purchase**, not a licence and not a subscription: a person buys **one
> output** from one procedure version, runs it once, gets the output and moves on. Migration 217
> renames `procedure_licences` to `procedure_purchases` and adds `consumed_at` and `used_by_run_id`.
> A purchase is consumed when the run reaches `done`; a failed or cancelled run releases it, so a
> failed attempt is never charged. A used or refunded purchase does not block buying another output
> later. The mechanics below (Razorpay Payment Link, payment-id idempotency, refund reversal) still
> hold; the "licence to run this version unlimited" wording in §0–§9 is superseded by this note.

**A person pays ₹100–200 (or $1–2) to use one specific Way.** 2026-11-22. Written for the agent that
implements the backend; the frontend is implemented in the same change (see §7).

---

## 0. The one decision that shapes everything

**This is a one-off purchase, not a subscription.**

That single choice avoids the largest regulatory trap in this area. The RBI **Digital Payments E-mandate
Framework, 2026** (issued 21 Apr 2026, replacing eight older circulars) governs *recurring* mandates on cards,
UPI and PPIs. It does not apply to a single charge a person deliberately approves. Choosing one-off removes
mandates, pre-debit notices, and the ₹15,000 AFA tier from scope entirely.

**What we are selling is a licence to run one pinned procedure version, charged once.** Not credits, not a
seat, not a renewal.

---

## 1. What already exists (do not rebuild)

| Thing | Where | Reuse how |
|---|---|---|
| Wallet, paise-native, per-account | `db/164_wallet_accounts_and_plans.sql`, `services/wallet.py` | `credit_payment` / `record_order` are the pattern to follow |
| Razorpay gateway + signature verify | `wallet.verify_signature`, `billing_webhook_events` | **event-id dedupe already exists** — reuse it verbatim |
| Idempotency by `payment_ref` | `plan_entitlements.payment_ref` (documented as the idempotency key) | Same discipline for a purchase row |
| Refunds | `services/wallet_refunds.py` | A purchase needs a refund path |
| Procedure identity | `procedures(procedure_id, version)`, `hash_procedure_version` | **A purchase must pin the version**, never the family |
| Ranking / evidence | `product_model.BEST_VERIFIED_FLOOR` | Price never influences ranking |

**What is missing:** a price on a procedure, a purchase record, and a gate that refuses a run without an
entitlement. That is the whole backend.

---

## 2. Data model

### 2.1 Price lives on the procedure version, not the family

```sql
-- New migration; next free number at time of writing. Additive and idempotent, like every migration here.
ALTER TABLE procedures
  ADD COLUMN IF NOT EXISTS price_paise BIGINT
    CHECK (price_paise IS NULL OR price_paise BETWEEN 0 AND 100000),
  ADD COLUMN IF NOT EXISTS price_currency TEXT NOT NULL DEFAULT 'INR',
  ADD COLUMN IF NOT EXISTS priced_by TEXT,
  ADD COLUMN IF NOT EXISTS priced_at TIMESTAMPTZ;
```

Four rules:

1. **`NULL` means free.** Not `0`. A `0` price is a deliberate free listing and must be distinguishable from
   "nobody has set a price". This matches how the repo already treats `None` vs zero for `average_match_cost`
   and `latency_ms` (`procedure_graph_api._cost_estimate` explains that reasoning and it applies here).
2. **Which account receives the money.** A price without a payee is not a price — it is a donation to us.
   See §2.3.
3. **A price is a property of a version.** If v4 is ₹150 and v5 changes the steps, v5 gets its own price.
   Otherwise a buyer who paid for v4 could argue they bought v5. Pitfall #2 below.
4. **Bounds: ₹0–₹1,000 (0–100000 paise).** The brief is ₹100–200 / $1–2. Allowing up to ₹1,000 avoids a
   migration when someone prices a genuinely large Way; anything above that is a business decision with a
   support process, not a column default.

### 2.2 The purchase record

```sql
CREATE TABLE IF NOT EXISTS procedure_purchases (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id      UUID NOT NULL,          -- the family
    procedure_row_id  UUID NOT NULL,          -- THE EXACT VERSION BOUGHT
    procedure_version INTEGER NOT NULL,
    buyer_user_id     UUID NOT NULL,
    seller_account_id UUID,                   -- who is paid; NULL = kell itself
    amount_paise      BIGINT NOT NULL CHECK (amount_paise >= 0),
    currency          TEXT NOT NULL DEFAULT 'INR',
    payment_ref       TEXT UNIQUE,            -- gateway payment id = the idempotency key
    order_id          TEXT,                   -- gateway order id
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'paid', 'refunded', 'expired', 'failed')),
    granted_at        TIMESTAMPTZ,
    expires_at        TIMESTAMPTZ,            -- NULL = perpetual (see §4)
    refunded_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at       TIMESTAMPTZ,            -- 217: set when the run reaches done
    used_by_run_id    UUID                     -- 217: the one run producing the output
);

-- 217 + 223: one OPEN CHECKOUT at a time, but a buyer may hold several paid,
-- unconsumed purchases (a prepaid batch or an operator comp). Each is bound to
-- one run and consumed at done, so N paid rows = N runs.
CREATE UNIQUE INDEX uq_procedure_purchase_open_checkout
    ON procedure_purchases (procedure_row_id, buyer_user_id)
    WHERE status = 'pending';
```

The rule that matters: **one purchase produces one output.** A double-click cannot charge twice
because `create_purchase` returns the active paid purchase before it opens a new link; and a run
cannot spend two purchases because `bind_purchase` claims exactly one row. Migration 223 relaxed
the old "one active purchase" index so an operator can comp a batch of runs without inventing a
credit system; the purchase record is still the accounting unit.

### 2.3 Where the money goes — the finding that determines scope

**Razorpay Route (split payments to third-party sellers) requires domestic turnover > ₹40 lakh or export
turnover > ₹5 lakh, plus a Payer-Payee Transparency declaration** (Razorpay docs, FAQ, post the Sep-2025 RBI
PA guidelines). A new contributor will not meet that.

So for v1:

- **`seller_account_id` is `NULL` for every Way, and we are the merchant of record.** The buyer pays kell;
  the contributor is credited internally (`seller_pending_paise`), and payouts happen out of band until Route
  eligibility exists.
- **Do not build split settlements now.** Building it before eligibility is a dead code path that will drift.
  Record what is owed; settle later.
- **Never show a contributor "earnings you can withdraw" until an actual payout rail exists.** That is the
  Polsia mistake — a balance the person cannot move.

**This is the single most likely thing to be cut in review.** If the reviewer objects, the correct answer is
not "add Route" — it is "v1 is kell-sold; contributor payouts are Phase 2 and gated on eligibility".

---

## 3. The gate

**A run is refused without a licence, and that refusal is a named error, not a silent fallback.**

Follow the house pattern:`app/services/v0_gate.py` owns its validation and its error messages; do the same.

```
def check_entitlement(procedure_row_id, buyer_user_id, licences) -> None
    raises ProcedureNotPurchased(price_paise, currency) when:
        procedure.price_paise is not None and > 0
        and no licence with status='paid' and (expires_at is null or expires_at > now())
```

Where it is enforced — **all three, or it does not count**:

1. `POST /v1/goals/submissions/{id}/runs` (the website/desktop run path) — `app/submissions/api.start_run`
2. `app/execution/way_runs.start`
3. Any MCP tool that executes a stored procedure

`app/submissions/api.start_run` already refuses a non-approved Way with a clear message
(`"this Way is {status}; only an approved Way runs"`). Add the purchase check **in the same place**, so a
refusal reads the same way to the person.

### The half-gate rule applies

`CLAUDE.md` hard rule 6: *"An enforcement trigger lands in the same change that wires its writer."* The gate
and the purchase writer ship together. A gate with no writer locks everyone out; a writer with no gate takes
money and gives nothing.

---

## 4. Term and refunds

- **Term: perpetual licence to that one version.** Not 30 days. A 30-day licence on a document the person has
  already read is a retention gimmick, and it creates an angry renewal question every month.
  `expires_at` stays in the schema (`NULL` today) so a time-limited listing is possible without a migration.
- **Upgrade is a new purchase.** v4 → v5 is a separate ₹150. Do not auto-charge an upgrade. This is why the
  price is per version.
- **Refund:** reuse `wallet_refunds.py`. A refund reverses the licence to `status='refunded'`, and the gate
  then refuses future runs. **A refund must not require deleting the licence row** — the record of what
  happened stays.

---

## 5. Price and the ₹100–200 / $1–2 band

The brief's band is right, and the research supports why:

- Microtransaction literature finds low per-transaction value keeps *perceived risk* low, which is what makes
  an unplanned purchase happen (Chaudhary & DaSouza, MDPI 2024). The purchase must be small enough that a
  mistaken one is not worth regretting.
- **Price is not a signal of quality here. Do not price by evidence tier.** A ₹150 Way with a bad Wilson
  bound is still ₹150. The moment price reflects the score, we have invented pay-to-rank, and the ranking
  claim dies.

**Recommended defaults:**

| Band | Value |
|---|---|
| Floor | ₹100 / $1 |
| Default | ₹150 / $1.75 |
| Ceiling | ₹200 / $2 |

Store **paise only**; display converts. `wallet.rates()` already holds the INR/USD rate — use it, do not
hardcode a second rate.

---

## 6. Tax — state it, do not pretend it is solved

The GST flyer's own test decides OIDAR: **a PDF automatically delivered by the system is OIDAR; one emailed
manually is not**; a course with a live tutor is not OIDAR. A Way that kell runs automatically is squarely the
"automated" side.

Consequences to record in the docs, and to raise with counsel before charging:

- Selling a licence to run a procedure is plausibly **OIDAR at 18%** for a buyer outside India, and a domestic
  supply for a buyer inside it.
- **An e-commerce operator must register under GST regardless of turnover** (§24(x) CGST). So "we are small"
  is not an exemption once we take payment on behalf of others — a second reason contributor payout is not v1.
- **Do not put a hard-coded tax line in the UI until the answer is confirmed.** Show the price, mark tax
  "included" or "as applicable" per the ruling, and make it a config value, not a constant.

---

## 7. What the frontend does (implemented in this change)

On the Way page (`/way/[id]` and `desktop/components/WayPage.tsx`), between **Output** and **What it does**:

- A **price block**: the amount, what it buys ("run this version, unlimited, forever"), and the version it
  pins.
- A **Buy** button, and after purchase a **Licensed** state instead of a button.
- **Free Ways show no price block at all** — no "₹0" badge. Free is the default state of the world.
- **The buy panel must say what happens on refusal**: nothing is charged until the gateway confirms.

### 7.1 The contract the backend must satisfy (the frontend is already coded to this)

The Way page is **public** (`docs/procedure_outputs.md` §4 of this change): readable with no account.

**`GET /v1/goals/submissions/{id}`** adds two fields to its payload:

```jsonc
{
  "id": "…",
  "goal_name": "Refund a captured payment",
  "price_paise": 15000,          // null/absent = free; 0 also means free
  "price_currency": "INR",       // "INR" | "USD"
  "licence_status": "none"       // "none" | "purchased" | "refunded"
}
```

- `price_paise: null` **or** absent → the page renders no price block at all.
- `licence_status` is **about the caller**. A signed-out reader is always `"none"`.
- A stranger asking for a Way that is not published gets **404**, not 401 (already implemented).

**`POST /v1/procedures/{procedure_id}/purchase`** — body `{"procedure_row_id": "<the exact version row id>"}`.

| Answer | Body | Frontend behaviour |
|---|---|---|
| `200` | `{"checkout_url": "https://…"}` | redirects the person to the gateway |
| `200` | `{"redirect_url": "https://…"}` | same |
| `401` | — | sends the person to sign-in and back to this Way |
| `404` / `501` | — | *"Buying is not open yet. You have not been charged."* |
| `4xx/5xx` | `{"error": "…"}` | shows the message under *"not charged"* |

**Today the endpoint returns 404**, so the page honestly says buying is not open. That is the correct
behaviour until the backend lands — do not "fix" it by making the button optimistic.


---

## 8. Pitfalls, each from something measured or already suffered here

1. **Charging before the run succeeds.** Polsia's 84.6% churn and 2.2/5 Trustpilot trace to "failed tasks
   consuming credits". Charge for the **licence**, deliver the right to run, and refund if the Way cannot run
   at all on first use. Never meter a run that failed.
2. **Pricing the family instead of the version.** A buyer paid for v4. v5 has different steps. Pinning is the
   whole point of `plans.py` invariant #2 — apply the same discipline to money.
3. **`0` vs `NULL`.** Six existing wallet rows with `amount_paise = 0` (`plan_included`, `signup_bonus`,
   `manual`) blocked a naive `<> 0` on paise during migration 215. Do not repeat it: `NULL` = unpriced.
4. **Ranking pollution.** A paid Way must not rank higher. Price stays out of `product_model` entirely.
5. **Refund with no reversal.** A refund that leaves the licence `paid` gives the person their money and the
   product.
6. **Silent free access.** If `check_entitlement` is written but never called from `way_runs.start`, every
   run succeeds and the gate is theatre. Test it by **asserting the refusal**, not the success.
7. **A balance the person cannot move.** Do not display contributor earnings until a payout rail exists.
8. **Hard-coded tax.** See §6.

---

## 9. Acceptance — numbers, not prose

1. `pytest -k "purchase or entitlement"` — **all pass**, including a test that **asserts the refusal** for
   an unpurchased paid Way on all three run paths (§3).
2. A purchase is idempotent: replaying the same `payment_ref` creates **one** licence, not two.
3. A refund flips `status` to `refunded` and the next run **is refused**.
4. A free Way (`price_paise IS NULL`) runs with **no licence row present**.
5. The frontend `tsc --noEmit` is clean and the existing suite still passes (currently **721**, website).

---

## 10. One sentence

**Sell a one-off licence to run one pinned procedure version — ₹100–200, paid once, kell as merchant of
record until a payout rail exists — because the licence is the only thing whose price the buyer can check
against the version they actually get.**
