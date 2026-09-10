-- Migration 63: a real, monotonic ordering column for execution_run_events.
--
-- Found live by this session's own B22 mega-chain e2e test: `start_run`
-- can write two events (`run_created` then `route_decided`) inside the
-- SAME transaction, so both get the SAME `now()` timestamp (transaction-
-- scoped, not statement-scoped) -- `ORDER BY created_at, id` then tie-
-- breaks on `id`, which is a random `gen_random_uuid()`, not anything
-- insertion-order-correlated. `get_run_events()` returned `route_decided`
-- before `run_created` on that run, silently wrong ordering, not a crash
-- -- exactly the kind of bug real chained-call testing (B22) exists to
-- catch that single-call tests cannot.
--
-- Fix: an actual monotonic sequence, independent of wall-clock time.
--
-- Next free number: 64 (63 is highest).

ALTER TABLE execution_run_events ADD COLUMN IF NOT EXISTS seq BIGSERIAL;

CREATE INDEX IF NOT EXISTS idx_execution_run_events_run_seq
    ON execution_run_events(execution_run_id, seq);
