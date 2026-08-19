-- Ticket 10 (memory-substrate map): state is a read-time projection over
-- the claim graph, no separate state table. This index is what makes
-- that projection cheap -- "state at T = facts whose validity interval
-- contains T" is a range-containment query, and btree cannot serve that
-- efficiently; GiST can.
--
-- Verified directly against this database before writing this file, not
-- assumed: `SELECT provolatile FROM pg_proc WHERE proname = 'tstzrange'`
-- returned 'i' (IMMUTABLE) on this Postgres 16 instance, so the direct
-- expression-index form below is valid -- the ticket's own documented
-- fallback (a GENERATED ALWAYS AS ... STORED range column) is not needed
-- here.
--
-- Does NOT replace ticket 03's plain btree on (properties->>'subject')
-- (db/13_claim_subject_index.sql) -- that one serves "all claims about
-- subject X regardless of time"; this one serves time-scoped projection.
-- Both are wanted, per the ticket's own explicit note.
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE INDEX IF NOT EXISTS idx_claim_subject_validity ON knowledge_nodes
    USING gist ((properties->>'subject'), tstzrange(t_valid, t_invalid))
    WHERE node_type = 'claim';

-- Deliberately NO exclusion constraint (ticket 10's own explicit
-- rejection): a subject legitimately carries many simultaneous claims
-- under different predicates, and even keyed correctly, an exclusion
-- constraint would make the real, required case -- both `p` and `not p`
-- supported, preserved, and flagged as a conflict -- unrepresentable at
-- the database level. Contradiction detection stays in application code
-- (claims.py's relate_claims(), already the right mechanism).
