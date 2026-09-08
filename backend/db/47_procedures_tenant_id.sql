-- Next free migration number: 48.
--
-- Phase 2 completion (launch compliance): the ORGANIZATION visibility
-- boundary. access.py's visibility_predicate() emits
--   (visibility = 'org' AND tenant_id IN (<caller org uuids>))
-- for an org-member scope. `procedures` was the one visibility-scoped
-- table without a `tenant_id` column (migration 29's RLS backstop added
-- it to knowledge_nodes / claims / task_nodes / edges / evidence but not
-- procedures), so that clause could not be applied there.
--
-- Additive, nullable, NO backfill (fresh-start rule): an existing row is
-- 'public' or 'private' and carries no tenant. A row only becomes
-- ORGANIZATION-scoped when a future write path sets visibility='org' +
-- tenant_id together. Existing global ('public') ownership is untouched.

ALTER TABLE procedures ADD COLUMN IF NOT EXISTS tenant_id UUID;

CREATE INDEX IF NOT EXISTS idx_procedures_tenant
    ON procedures (tenant_id) WHERE tenant_id IS NOT NULL;
