-- Migration 97: a Procedure carries its source locator; every step carries its own.
--
-- Product decision: there is no separate global Implementation object in the knowledge ontology. Concrete
-- execution bindings (tool / model / MCP tool / adapter / sandbox / command / runtime / SLM artifact /
-- locator / parameters / verifier / resource requirements) live on the procedure STEPS (steps[].binding),
-- and provenance lives on the procedure (source_locator) and on each step (steps[].source_locator).
--
-- steps stay a JSONB list (no new table); shape is validated by app/services/source_locators.py.
-- The `implementations` tables are NOT dropped here (60 modules still read them); they are deprecated as
-- knowledge and are folded into step bindings by `admin fold-implementations` (see docs).

ALTER TABLE procedures ADD COLUMN IF NOT EXISTS source_locator JSONB;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'procedures_source_locator_chk') THEN
        ALTER TABLE procedures ADD CONSTRAINT procedures_source_locator_chk
            CHECK (source_locator IS NULL OR jsonb_typeof(source_locator) = 'object');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_procedures_source_locator_uri
    ON procedures ((source_locator ->> 'uri')) WHERE source_locator IS NOT NULL AND t_invalid IS NULL;
