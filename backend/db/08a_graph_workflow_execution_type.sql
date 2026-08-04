-- Split from what's now 08b specifically because Postgres will not allow
-- a newly added enum value to be used (e.g. in a CHECK constraint) in
-- the same transaction that added it. This only surfaces when a whole
-- script runs as one transaction, which many SQL editors (Supabase's
-- included) do by default, even though psql's own default (autocommit
-- per statement) does not, which is why this wasn't caught earlier.
--
-- Run this file FIRST, as its own separate execution, then run
-- 08b_graph_workflow_execution_rest.sql as a separate execution after.
-- Running them back to back in the same "Run" click, if your SQL editor
-- treats the whole pasted script as one transaction, will still fail
-- with the same error -- they must be two genuinely separate executions.
--
-- Idempotent: safe to re-run.

ALTER TYPE agent_execution_mode ADD VALUE IF NOT EXISTS 'graph_workflow';
