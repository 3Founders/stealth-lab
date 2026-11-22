-- 217: one-run purchases (docs/procedure_billing.md, revised 2026-11-22).
--
-- The product is: a person buys ONE output from ONE procedure version, runs it once, gets the
-- output and moves on. Not a licence, not unlimited runs, not a subscription.
--
-- A purchase is consumed when the run reaches `done`. A failed or cancelled run releases it, so a
-- failed attempt is never charged. `used_by_run_id` binds a purchase to the run producing the
-- output, so two parallel runs cannot both spend one purchase.
--
-- The old strict UNIQUE (procedure_row_id, buyer_user_id) is replaced by a PARTIAL unique index:
-- one active purchase at a time, but a consumed/refunded purchase does not block buying another
-- output later.
--
-- Additive and idempotent.

ALTER TABLE IF EXISTS procedure_licences RENAME TO procedure_purchases;

ALTER TABLE procedure_purchases
    ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS used_by_run_id UUID;

-- Drop any UNIQUE constraint that covers exactly (procedure_row_id, buyer_user_id), whatever its
-- generated name is. The partial index below is the one-active-purchase rule.
DO $$
DECLARE c TEXT;
BEGIN
    FOR c IN
        SELECT con.conname
          FROM pg_constraint con
         WHERE con.conrelid = 'procedure_purchases'::regclass
           AND con.contype = 'u'
           AND (
                SELECT array_agg(att.attname ORDER BY att.attname)
                  FROM unnest(con.conkey) AS k(attnum)
                  JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
               ) = ARRAY['buyer_user_id', 'procedure_row_id']::name[]
    LOOP
        EXECUTE format('ALTER TABLE procedure_purchases DROP CONSTRAINT %I', c);
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_procedure_purchase_active
    ON procedure_purchases (procedure_row_id, buyer_user_id)
    WHERE status IN ('pending', 'paid') AND consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_procedure_purchases_run
    ON procedure_purchases (used_by_run_id)
    WHERE used_by_run_id IS NOT NULL;
