-- 216: per-procedure billing (docs/procedure_billing.md).
--
-- A price lives on the procedure VERSION, never the family: a buyer paid for v4, and v5 has
-- different steps. NULL means "unpriced" (free); 0 is a deliberate free listing and is also
-- free. Bounds are 0..100000 paise (Rs 0..1000); the brief's band is Rs 100-200.
--
-- A licence is a one-off purchase of the right to run one pinned version. It is not a
-- subscription and not credits. UNIQUE (procedure_row_id, buyer_user_id) makes paying twice
-- for the same version impossible by construction. A refund flips status to 'refunded' and
-- keeps the row: the record of what happened is not deleted.
--
-- Additive and idempotent.

ALTER TABLE procedures
    ADD COLUMN IF NOT EXISTS price_paise BIGINT
        CHECK (price_paise IS NULL OR price_paise BETWEEN 0 AND 100000),
    ADD COLUMN IF NOT EXISTS price_currency TEXT NOT NULL DEFAULT 'INR',
    ADD COLUMN IF NOT EXISTS priced_by TEXT,
    ADD COLUMN IF NOT EXISTS priced_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS procedure_licences (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id      UUID NOT NULL,                 -- the family
    procedure_row_id  UUID NOT NULL,                 -- THE EXACT VERSION BOUGHT
    procedure_version INTEGER NOT NULL,
    buyer_user_id     UUID NOT NULL,                 -- the Supabase subject the run path uses
    seller_account_id UUID,                          -- who is paid; NULL = kell itself (v1: always NULL)
    amount_paise      BIGINT NOT NULL CHECK (amount_paise >= 0),
    currency          TEXT NOT NULL DEFAULT 'INR' CHECK (currency IN ('INR', 'USD')),
    payment_ref       TEXT UNIQUE,                   -- gateway payment id = the idempotency key
    order_id          TEXT,                          -- gateway order id, when the gateway gives one
    payment_link_id   TEXT UNIQUE,                   -- Razorpay Payment Link id (the hosted checkout)
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'paid', 'refunded', 'expired', 'failed')),
    granted_at        TIMESTAMPTZ,
    expires_at        TIMESTAMPTZ,                   -- NULL = perpetual (the v1 term)
    refunded_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (procedure_row_id, buyer_user_id)         -- one licence per version per buyer
);

CREATE INDEX IF NOT EXISTS idx_procedure_licences_buyer
    ON procedure_licences (buyer_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_procedure_licences_row
    ON procedure_licences (procedure_row_id, status);
CREATE INDEX IF NOT EXISTS idx_procedure_licences_payment
    ON procedure_licences (payment_ref) WHERE payment_ref IS NOT NULL;
