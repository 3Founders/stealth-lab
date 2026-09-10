-- Migration 71 (B27: external implementation hosting): a real
-- `execution_location` column on `implementations`.
--
-- B27's own text: "Externally hosted implementations are first-class:
-- Procedure -> Implementation (type=HTTP_API/MCP_TOOL, execution_
-- location=THIRD_PARTY_HOSTED) -> external provider." The `kind` column
-- (migration 33) already lets a row register as 'api'/'tool' ahead of a
-- real executor existing (its own documented intent) -- what was
-- missing is the explicit LOCATION distinction B27 names: registering
-- an Implementation as externally hosted is a real, storable fact even
-- before this codebase has a working adapter for it (B25's adapter
-- resolver stays the thing that decides whether it can actually be
-- invoked -- this column does not change that; a row here is data, not
-- a runnable claim, same discipline `kind`'s own comment already
-- established).
--
-- Deliberately NOT adding a new executor/adapter here -- confirmed live
-- (grep of implementations.py/providers.py): no real HTTP_API/MCP_TOOL
-- executor exists in this codebase, and building one on spec text alone
-- with no real external endpoint to test against would be exactly the
-- unmeasured, speculative machinery CLAUDE.md's "no vague
-- implementation" rule forbids. This migration closes the DATA-MODEL
-- half of B27 (first-class storage) honestly; the EXECUTION half stays
-- a documented, real gap (MCP_HARDENING_DEFERRED_ITEMS.md).
--
-- Next free number: 72 (71 is highest).

ALTER TABLE implementations
    ADD COLUMN IF NOT EXISTS execution_location TEXT NOT NULL DEFAULT 'stealth_hosted';

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'implementations_execution_location_chk') THEN
        ALTER TABLE implementations ADD CONSTRAINT implementations_execution_location_chk
            CHECK (execution_location IN ('stealth_hosted', 'user_hosted', 'third_party_hosted'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_implementations_execution_location
    ON implementations(execution_location) WHERE execution_location <> 'stealth_hosted';
