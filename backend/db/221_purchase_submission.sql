-- 221: remember which Way a purchase was made from.
--
-- The payment link used to send the buyer back to whatever page the browser said it came from
-- (the Referer), which is how a purchase made from /route returned to /route. A purchase now knows
-- the goal submission it was made from, so the callback can be an explicit
-- /way/{submission_id}/paid page that offers the two ways to produce the output.
--
-- Nullable on purpose: older purchases and CLI-created ones have no submission, and the callback
-- falls back to the public site.
--
-- Additive and idempotent.

ALTER TABLE procedure_purchases ADD COLUMN IF NOT EXISTS submission_id UUID;

CREATE INDEX IF NOT EXISTS idx_procedure_purchases_submission
    ON procedure_purchases (submission_id)
    WHERE submission_id IS NOT NULL;
