-- Migration 31 (Lane CORE-A): seed grounded_hybrid_v1 as a real,
-- enabled+approved, global-scope procedure_extractors row -- closes
-- f7262a5's BLOCKER 1.
--
-- Root cause, confirmed by reading procedure_extraction/__init__.py,
-- registry.py, strategies.py, and mcp_server/server.py directly:
-- GroundedHybridExtractor and its real client (server.py's OpenAI
-- client, passed into extract_procedure as `client=client`) were
-- already correctly wired end to end. The only missing piece was data,
-- not code -- select_extractor() requires enabled=TRUE AND
-- review_state='approved' (registry.py), and migration 20 seeds only
-- deterministic_v1 that way. No llm-kind row has ever existed, so
-- every extraction -- even with a real client passed in -- fell
-- through _select_strategy()'s `row is None` branch to
-- DeterministicExtractor, whose capability_statement is goal_text
-- verbatim (deliberate, per strategies.py's own docstring). That is
-- exactly why a task description naming a file got rejected by
-- V4_capability_abstraction: the literal goal text contains the
-- filename V4 scans for.
--
-- Idempotent, same convention as every other migration here.
INSERT INTO procedure_extractors (name, description, kind, version, config, scope, review_state, enabled)
VALUES (
    'grounded_hybrid_v1',
    'Default LLM-backed extractor: one bounded call abstracts capability_statement '
    'and step phrasing off a compressed tool-call summary; everything else '
    '(preconditions, scope, slots, failure_conditions) stays derived from real '
    'project_state, never invented. Degrades to deterministic_v1 on no client / '
    'API failure / unparseable response / ABSTAIN.',
    'llm', '1',
    '{"model": "gemma-4-31B-it", "temperature": 0.2}',
    '{}',
    'approved', TRUE
)
ON CONFLICT (name, version) DO NOTHING;
