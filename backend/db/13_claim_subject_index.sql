-- Ticket 03 (memory-substrate map): exactly one expression index, matching
-- the actual real access pattern (episode_links-driven "why do we believe
-- this," not general triple-store joins), confirmed no index existed on
-- properties or node_type anywhere in the schema before this -- so
-- neither JSONB-properties nor real-columns had a querying advantage to
-- begin with when this decision was made.
CREATE INDEX IF NOT EXISTS idx_kn_claim_subject
    ON knowledge_nodes ((properties->>'subject'))
    WHERE node_type = 'claim';
