-- Run AFTER 08a_graph_workflow_execution_type.sql, as a separate
-- execution -- see that file's header for why.
--
-- A promoted decomposition is a *sequence* of task nodes with
-- dependencies between them, not one registered skill (local_skill) or
-- one external call (remote_http). Running it means walking that
-- subgraph via ExecutionHarness in dependency order, threading each
-- step's output into the next.
--
-- Idempotent: safe to re-run.

ALTER TABLE agents ADD COLUMN IF NOT EXISTS workflow_task_ids JSONB;

ALTER TABLE agents DROP CONSTRAINT IF EXISTS agent_execution_config_present;
ALTER TABLE agents ADD CONSTRAINT agent_execution_config_present CHECK (
    (execution_mode = 'local_skill' AND skill_ref IS NOT NULL)
    OR (execution_mode = 'remote_http' AND remote_config IS NOT NULL)
    OR (execution_mode = 'graph_workflow' AND workflow_task_ids IS NOT NULL)
);
