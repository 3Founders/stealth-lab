-- 161_invoicing.sql
--
-- Next free number: 160 (tool cards) was highest before this file.
--
-- What this adds: what the admin dashboard needs to run INR billing: GST tax invoices for payments, credit notes, and a way to
-- tie a gateway order to the payment that paid it.
--
--   * org_wallet_credits.order_id: the gateway order a credit came from, so reconciliation can say "this order was paid and
--     credited" or "this order has no credit". Nullable: credits made before this have no order id and are matched by amount
--     and time only. The webhook fills it from now on (services/wallet.py).
--   * seller_profile: ONE row, the seller's legal details that print on every invoice (legal name, GSTIN, state, address, SAC
--     code, rate). A tax invoice cannot be issued until it is complete; none of it is guessed here.
--   * invoice_counters / invoices: gap-free sequential numbers per financial year (GST requires a consecutive number of at most
--     16 characters), one invoice per payment, and credit notes that reverse one. Every invoice copies the seller and buyer
--     details as they were when it was issued, so editing the profile later never rewrites an old invoice.
--
-- Invoices and credit notes are append-only: a correction is a credit note plus a new invoice, never an edit. Like 154, 155 and
-- 157, RLS is ON with no policies and the Supabase API roles are revoked; only the application, after the admin check, writes.
-- Additive and idempotent.

ALTER TABLE org_wallet_credits ADD COLUMN IF NOT EXISTS order_id TEXT;
CREATE INDEX IF NOT EXISTS idx_wallet_credit_order ON org_wallet_credits (order_id);

CREATE TABLE IF NOT EXISTS seller_profile (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    legal_name   TEXT NOT NULL,
    trade_name   TEXT,
    gstin        TEXT CHECK (gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'),
    state_code   TEXT CHECK (state_code IS NULL OR state_code ~ '^[0-9]{2}$'),
    address      TEXT,
    sac_code     TEXT,
    gst_rate_pct NUMERIC(5, 2) NOT NULL DEFAULT 18 CHECK (gst_rate_pct >= 0 AND gst_rate_pct <= 40),
    invoice_prefix TEXT NOT NULL DEFAULT 'KEL' CHECK (invoice_prefix ~ '^[A-Z0-9]{1,4}$'),
    updated_by   TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoice_counters (
    fy      TEXT PRIMARY KEY CHECK (fy ~ '^[0-9]{4}$'),   -- 2026-27 is '2627'
    last_no INTEGER NOT NULL CHECK (last_no >= 0)
);

CREATE TABLE IF NOT EXISTS invoices (
    id                 UUID PRIMARY KEY,
    number             TEXT NOT NULL UNIQUE CHECK (char_length(number) <= 16),
    fy                 TEXT NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('invoice', 'credit_note')),
    organization_id    UUID,
    payment_ref        TEXT,
    credit_for         UUID REFERENCES invoices(id),
    seller_name        TEXT NOT NULL,
    seller_gstin       TEXT NOT NULL,
    seller_state_code  TEXT NOT NULL,
    seller_address     TEXT NOT NULL,
    buyer_name         TEXT NOT NULL,
    buyer_gstin        TEXT,
    buyer_state_code   TEXT NOT NULL,
    buyer_address      TEXT,
    place_of_supply    TEXT NOT NULL,
    description        TEXT NOT NULL,
    sac_code           TEXT NOT NULL,
    rate_pct           NUMERIC(5, 2) NOT NULL,
    taxable_paise      BIGINT NOT NULL CHECK (taxable_paise >= 0),
    cgst_paise         BIGINT NOT NULL DEFAULT 0 CHECK (cgst_paise >= 0),
    sgst_paise         BIGINT NOT NULL DEFAULT 0 CHECK (sgst_paise >= 0),
    igst_paise         BIGINT NOT NULL DEFAULT 0 CHECK (igst_paise >= 0),
    total_paise        BIGINT NOT NULL CHECK (total_paise > 0),
    issued_by          TEXT NOT NULL,
    issued_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (taxable_paise + cgst_paise + sgst_paise + igst_paise = total_paise),
    CHECK ((kind = 'credit_note') = (credit_for IS NOT NULL))
);
-- one tax invoice per payment (a re-issue after a credit note is a new payment-less correction, not a second invoice for the payment)
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_payment ON invoices (payment_ref) WHERE kind = 'invoice' AND payment_ref IS NOT NULL;
-- a credit note reverses an invoice once
CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_note_for ON invoices (credit_for) WHERE kind = 'credit_note';
CREATE INDEX IF NOT EXISTS idx_invoices_org ON invoices (organization_id, issued_at DESC);

CREATE OR REPLACE FUNCTION tg_invoices_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'invoices are append-only: issue a credit note instead' USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
DROP TRIGGER IF EXISTS tg_invoices_guard ON invoices;
CREATE TRIGGER tg_invoices_guard BEFORE UPDATE OR DELETE ON invoices
    FOR EACH ROW EXECUTE FUNCTION tg_invoices_guard();

ALTER TABLE seller_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoice_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['seller_profile', 'invoice_counters', 'invoices'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON %I FROM anon', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON %I FROM authenticated', t);
        END IF;
    END LOOP;
END
$$;
