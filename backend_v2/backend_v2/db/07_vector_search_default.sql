-- Set HNSW search breadth as a database-level default.
-- Idempotent: safe to re-run.
--
-- app/db/session.py issues `SET hnsw.ef_search` when the pool opens each
-- connection. Verified against this Supabase instance, that is not enough:
-- through the session pooler, the first acquire reported 100 and the next
-- two reported the default 40. Supavisor multiplexes client connections
-- onto a rotating set of server backends, so a GUC set once per client
-- connection is not reliably the GUC in effect when a later query runs.
--
-- A database-level default applies to every backend regardless of who
-- pooled what, and survives the pooler entirely. The per-connection SET
-- stays as well, for deployments that talk to Postgres directly.
--
-- pgvector's default of 40 favours latency; recall is the axis that
-- matters here, since a node the search misses is a citation the answer
-- cannot make. Takes effect on new connections, not existing ones.

DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET hnsw.ef_search = 100', current_database());
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE
            'no privilege to ALTER DATABASE; hnsw.ef_search stays at the default. '
            'Vector search still works, with lower recall.';
    WHEN undefined_object THEN
        RAISE NOTICE
            'hnsw.ef_search is not a recognised setting -- the vector extension '
            'is probably not loaded in this database.';
END $$;
