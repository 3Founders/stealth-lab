-- What a run cost, and what a Way measurably costs to run (docs/blinkit/next_pass_plan.md).
--
-- Why: every model call already records tokens and cost in `llm_spend`, but nothing says WHICH Way run made the call.
-- So a run happens, its spend is recorded, and it is unattributable — you cannot ask "what did run X cost". Without
-- that, none of the pricing layer works: no measured floor, no realised margin, no learned output cap
-- (docs/blinkit/measurement.md, docs/blinkit/caps.md).
--
-- What this adds:
--
--   llm_spend.run_id, llm_spend.step_order     attribute one call to the run and the step that made it
--   tool_gateway_calls.run_id                  the same for a tool call (the locator cost of a `tool` step)
--   way_runs.cost_usd / tokens_in / tokens_out the run's total, written once when it finishes
--   procedures.measured_runs / measured_cost_paise  what the Way has been observed to cost, so a price has a floor
--
-- Every column is nullable or defaulted, and there is no backfill: a row that predates this migration is honestly
-- "no run to attribute to", not a guess. No existing caller changes behaviour.

ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS run_id uuid;
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS step_order integer;

-- Partial: most llm_spend rows (ingestion, search, debate) will never belong to a Way run, and a full index would
-- pay for all of them to serve a query that only ever looks at runs.
CREATE INDEX IF NOT EXISTS idx_llm_spend_run ON llm_spend (run_id, step_order) WHERE run_id IS NOT NULL;

ALTER TABLE tool_gateway_calls ADD COLUMN IF NOT EXISTS run_id uuid;
CREATE INDEX IF NOT EXISTS idx_tool_gateway_calls_run ON tool_gateway_calls (run_id) WHERE run_id IS NOT NULL;

ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS cost_usd   numeric(12, 6);
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS tokens_in  integer;
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS tokens_out integer;

-- What a Way has been measured to cost, over its successful runs. `measured_cost_paise` is the MEAN, kept on the
-- version row so a page or a price check never has to aggregate the ledger. A Way with `measured_runs = 0` has never
-- run and cannot be priced (procedure_billing.set_price refuses it) — publishable, not sellable.
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS measured_runs       integer NOT NULL DEFAULT 0;
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS measured_cost_paise bigint;

COMMENT ON COLUMN procedures.measured_runs IS
    'Successful runs whose cost was attributed. 0 = never measured, so the Way cannot be priced above free.';
COMMENT ON COLUMN procedures.measured_cost_paise IS
    'Mean cost in paise over the measured runs. The floor a price must clear, not the price itself.';
