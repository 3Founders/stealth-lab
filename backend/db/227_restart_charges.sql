-- Charge a restart for the steps it actually re-runs.
--
-- Supersedes the first attempt at restarts (db/226, `way_runs.restarts`): re-opening the purchase for free meant a
-- person could redo the expensive half of a Way repeatedly for one payment. The owner's rule, 2026-11-23:
-- "however many steps he retries, he should be charged accordingly."
--
-- How the charge is computed: the Way's price is split per step (cost_estimate.per_step_prices); a restart from step
-- N is charged for steps N..end only. So a restart of the last step costs the last step, and a restart of the whole
-- run costs the whole price again.
--
-- The charge is recorded, not charged again through the gateway: the buyer already holds a paid purchase for this
-- output, and a restart is a redo of part of it. `restart_charges` is the ledger that makes the amount visible,
-- auditable, and collectable (a settlement run can invoice the sum). Keeping it as rows rather than a counter means
-- every line item exists, which is what a dispute needs.
--
-- Additive and idempotent.

CREATE TABLE IF NOT EXISTS restart_charges (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             uuid NOT NULL,
    purchase_id        uuid,
    buyer_user_id      uuid,
    from_step          integer NOT NULL CHECK (from_step >= 1),
    steps_charged      integer NOT NULL CHECK (steps_charged >= 0),
    amount_paise       bigint  NOT NULL CHECK (amount_paise >= 0),   -- the authoritative figure
    estimate_low_paise bigint,                                       -- what the estimate said, kept to audit drift
    estimate_high_paise bigint,
    edit_summary       text NOT NULL DEFAULT '',                     -- what the person changed, never the full text
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_restart_charges_run ON restart_charges (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_restart_charges_buyer ON restart_charges (buyer_user_id, created_at DESC)
    WHERE buyer_user_id IS NOT NULL;

COMMENT ON TABLE restart_charges IS
    'One row per restart, charged for the steps it re-runs. The purchase is re-opened so the redo can proceed; this '
    'table records what that redo cost.';
COMMENT ON COLUMN restart_charges.amount_paise IS
    'Steps from `from_step` to the end, at the Way price split per step (cost_estimate.suffix_from).';

-- A way to read the charge back on the run without a join, for the page.
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS charged_paise bigint NOT NULL DEFAULT 0;
COMMENT ON COLUMN way_runs.charged_paise IS
    'Total charged for this run including restarts. The price of the first attempt is the purchase; this adds the redo.';
