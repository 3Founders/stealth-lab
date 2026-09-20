-- Migration 94: correlate the canonical event log with OpenTelemetry traces,
-- and add the three retrieval event types it lacked.
--
-- execution_run_events (migrations 61/63/70/72) stays THE canonical,
-- append-only event log -- no second event system. The spec's event names map
-- onto it as: RunStarted=run_started, CandidateRetrieved=procedure_retrieved,
-- ClaimsRetrieved=claims_retrieved (new), CandidateReranked=candidates_reranked
-- (new), CandidateRejected=candidate_rejected (new), ProcedureSelected=
-- route_decided, ImplementationSelected=implementation_bound,
-- ExecutionStarted=node_started, ExecutionFinished=node_succeeded/node_failed,
-- VerificationCompleted=verification_completed, RunFailed=run_failed,
-- RunCompleted=run_finalized.
--
-- trace_id/span_id are optional correlation ids (NULL when tracing is off);
-- they are never authoritative. event_version lets payload shapes evolve.
--
-- Next free number: 95 (94 is highest).

ALTER TABLE execution_run_events
    ADD COLUMN IF NOT EXISTS event_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS trace_id      TEXT,
    ADD COLUMN IF NOT EXISTS span_id       TEXT;

CREATE INDEX IF NOT EXISTS idx_execution_run_events_trace
    ON execution_run_events(trace_id) WHERE trace_id IS NOT NULL;

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
        'artifact_recorded',
        -- migration 94
        'claims_retrieved', 'candidates_reranked', 'candidate_rejected'
    ));
