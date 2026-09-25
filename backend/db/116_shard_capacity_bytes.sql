-- Migration 116: shard capacity in BYTES. Next free number: 117.
--
-- Knowledge shards are hosted databases with a hard storage limit (e.g. a 500 MB
-- Neon project). `capacity_rows` could not express that. With `capacity_bytes` the
-- capacity guard (app/services/shard_capacity.py; worker loop + `admin shard-capacity`)
-- marks a shard `full` before the provider refuses writes, so NEW objects roll over
-- to other shards while existing ones stay where they are.
-- Idempotent.

ALTER TABLE knowledge_shards ADD COLUMN IF NOT EXISTS capacity_bytes BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'knowledge_shards_capacity_bytes_chk') THEN
        ALTER TABLE knowledge_shards ADD CONSTRAINT knowledge_shards_capacity_bytes_chk
            CHECK (capacity_bytes IS NULL OR capacity_bytes > 0);
    END IF;
END $$;
