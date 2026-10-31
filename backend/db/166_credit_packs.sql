-- 166: additional kell credits (fixed packs).
--
-- A pack order is a billing order for a fixed amount of credit-dollars. billing_orders learns which pack it is for and how many
-- dollars it credits, so the payment webhook (and the reconcile job) credit exactly what WE recorded, never a field from the
-- webhook body. Nothing else changes: the credit itself is an ordinary org_wallet_credits row (source 'razorpay').
--
-- Additive and idempotent. No rows are touched.

ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS pack      TEXT;
ALTER TABLE billing_orders ADD COLUMN IF NOT EXISTS pack_usd  NUMERIC(14, 6) CHECK (pack_usd IS NULL OR pack_usd > 0);
