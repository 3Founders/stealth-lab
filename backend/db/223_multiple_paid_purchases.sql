-- 223: allow more than one paid, unconsumed purchase per buyer/version.
--
-- Why: a person may prepay for several outputs -- an operator comp for a test batch,
-- or a future "buy N" flow. The gate already binds one purchase to one run and
-- consumes it when the run reaches done, so N paid rows = N runs. The old partial
-- unique index covered both 'pending' and 'paid' and made that impossible.
--
-- What stays true: at most one OPEN CHECKOUT per (version, buyer). Paying twice by
-- accident is still prevented by create_purchase returning the active paid purchase
-- before it opens a new link.
--
-- Additive and idempotent.

DROP INDEX IF EXISTS uq_procedure_purchase_active;

CREATE UNIQUE INDEX IF NOT EXISTS uq_procedure_purchase_open_checkout
    ON procedure_purchases (procedure_row_id, buyer_user_id)
    WHERE status = 'pending';
