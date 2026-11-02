-- How many people may ever buy a plan (2026-11-02). NULL = no limit. The one-person First Trial is one per account, so
-- counting plan_entitlements for the plan counts people. Enforced when an order is created, with slots held for orders
-- started in the last hour that are not activated yet; the dashboard's Plans tab edits it and shows how many are sold.
ALTER TABLE billing_plan_rows ADD COLUMN IF NOT EXISTS max_redemptions INTEGER
    CHECK (max_redemptions IS NULL OR max_redemptions > 0);

-- Placeholder launch cap for the one-person First Trial; change it in the dashboard's Plans tab.
UPDATE billing_plan_rows SET max_redemptions = 100 WHERE plan = 'pilot' AND max_redemptions IS NULL;
