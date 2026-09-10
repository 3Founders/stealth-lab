-- Migration 72 (B7): the ExecutionRecorder's own `record_artifact()`
-- operation, closing the last of B7's 8 named recorder operations
-- (start_run/append_event/record_node_transition/record_child_run/
-- record_artifact/record_verification/record_outcome/finalize_run).
--
-- 'artifact_recorded' is not in B8's 18 named event types, but B8's own
-- text says "At minimum" -- additive, same as the pre-existing 8 types
-- migration 61 added beyond that list.
--
-- Next free number: 73 (72 is highest).

ALTER TABLE execution_run_events DROP CONSTRAINT IF EXISTS execution_run_events_type_chk;

ALTER TABLE execution_run_events ADD CONSTRAINT execution_run_events_type_chk
    CHECK (event_type IN (
        'run_created', 'run_claimed', 'run_paused', 'run_finalized',
        'route_decided',
        'node_claimed', 'node_succeeded', 'node_failed',
        'run_started', 'procedure_retrieved', 'applicability_checked',
        'plan_created', 'implementation_bound', 'node_started',
        'tool_called', 'tool_result', 'knowledge_requested',
        'child_run_created', 'node_waiting', 'child_run_completed',
        'node_resumed', 'verification_started', 'verification_completed',
        'run_failed',
        'artifact_recorded'
    ));
