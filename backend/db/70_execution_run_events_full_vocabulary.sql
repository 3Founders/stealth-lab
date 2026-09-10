-- Migration 70 (B7/B8 rigor pass): expand execution_run_events.event_type
-- to the spec's full named vocabulary (B8: "At minimum: run_started,
-- route_decided, procedure_retrieved, applicability_checked,
-- plan_created, implementation_bound, node_started, tool_called,
-- tool_result, knowledge_requested, child_run_created, node_waiting,
-- child_run_completed, node_resumed, verification_started,
-- verification_completed, run_failed, run_finalized").
--
-- "At minimum" means additive, not exclusive -- this codebase's own
-- pre-existing extra types (run_created, run_claimed, run_paused,
-- node_claimed, node_succeeded, node_failed) stay valid; migration 61
-- already covered those. This migration widens the CHECK to add every
-- spec-named type migration 61 did not yet have, so real call sites
-- (added in this same pass, app/execution/recorder.py) can use them.
--
-- Next free number: 71 (70 is highest).

ALTER TABLE execution_run_events DROP CONSTRAINT IF EXISTS execution_run_events_type_chk;

ALTER TABLE execution_run_events ADD CONSTRAINT execution_run_events_type_chk
    CHECK (event_type IN (
        -- pre-existing (migration 61)
        'run_created', 'run_claimed', 'run_paused', 'run_finalized',
        'route_decided',
        'node_claimed', 'node_succeeded', 'node_failed',
        -- B8's named vocabulary, added here
        'run_started', 'procedure_retrieved', 'applicability_checked',
        'plan_created', 'implementation_bound', 'node_started',
        'tool_called', 'tool_result', 'knowledge_requested',
        'child_run_created', 'node_waiting', 'child_run_completed',
        'node_resumed', 'verification_started', 'verification_completed',
        'run_failed'
    ));
