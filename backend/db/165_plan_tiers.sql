-- 165: the plan ladder. Migration 164 allowed two plan names ('pro', 'pilot'); the founders' pricing is a ladder of three
-- 30-day tiers plus the pilot:
--
--   starter  Rs 200   $1 of usage included     the low anchor
--   pro      Rs 1,200 $6                        the step up (was Rs 999 / $5)
--   plus     Rs 2,000 $12                       the tier the page recommends
--   pilot    Rs 649 per seat, 14 days, 1 to 10 seats, $4 per seat (unchanged)
--
-- Only the CHECK constraints on the two plan columns change; no rows are touched, and every row already stored ('pro',
-- 'pilot') stays valid. Additive and idempotent: the constraints are dropped by looking them up and re-created with fixed names.

DO $$
DECLARE
    c TEXT;
BEGIN
    FOR c IN
        SELECT con.conname
          FROM pg_constraint con
          JOIN pg_class rel ON rel.oid = con.conrelid
         WHERE rel.relname IN ('billing_orders', 'plan_entitlements') AND con.contype = 'c'
           AND pg_get_constraintdef(con.oid) ILIKE '%plan%' AND pg_get_constraintdef(con.oid) ILIKE '%pilot%'
           AND pg_get_constraintdef(con.oid) NOT ILIKE '%seats%'
    LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',
                       CASE WHEN c LIKE 'billing_orders%' THEN 'billing_orders' ELSE 'plan_entitlements' END, c);
    END LOOP;
END $$;

ALTER TABLE billing_orders DROP CONSTRAINT IF EXISTS billing_orders_plan_tiers_check;
ALTER TABLE billing_orders ADD CONSTRAINT billing_orders_plan_tiers_check
    CHECK (plan IS NULL OR plan IN ('starter', 'pro', 'plus', 'pilot'));
ALTER TABLE plan_entitlements DROP CONSTRAINT IF EXISTS plan_entitlements_plan_tiers_check;
ALTER TABLE plan_entitlements ADD CONSTRAINT plan_entitlements_plan_tiers_check
    CHECK (plan IN ('starter', 'pro', 'plus', 'pilot'));
