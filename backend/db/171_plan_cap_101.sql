-- The founders set the First Trial launch cap to 101 people (2026-11-02). Change it afterwards in the dashboard's
-- Plans tab (Billing -> Plans -> Edit -> Limit); NULL means no limit.
UPDATE billing_plan_rows SET max_redemptions = 101 WHERE plan = 'pilot' AND max_redemptions = 100;
