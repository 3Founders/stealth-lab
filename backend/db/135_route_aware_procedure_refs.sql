-- Migration 135 (storage layout v2): the economy tables on the control database still had PHYSICAL foreign keys
-- to `procedures(id)`, but Procedures are now homed on knowledge shards. Every submit_way therefore failed with
-- "procedure_submissions_procedure_row_id_fkey" (the way's row is on K00x, not in the control database's
-- `procedures`), and usage events / ledger rows for a sharded way would fail the same way.
-- Next free migration number: 136.
--
-- Exactly as migration 96 did for execution_plans and migration 133 for the Goal references: each becomes the
-- route-aware check `sl_check_ref` (the Procedure row exists locally OR is routed to a shard). The referenced
-- side's ON DELETE SET NULL is not carried over: Procedure rows are tombstoned, never hard-deleted, and
-- sl_refuse_delete_if_referenced already guards hand-run deletes.
--
-- Idempotent.

ALTER TABLE procedure_submissions DROP CONSTRAINT IF EXISTS procedure_submissions_procedure_row_id_fkey;
ALTER TABLE procedure_submissions DROP CONSTRAINT IF EXISTS procedure_submissions_parent_procedure_row_id_fkey;
ALTER TABLE procedure_usage_events DROP CONSTRAINT IF EXISTS procedure_usage_events_procedure_row_id_fkey;
ALTER TABLE credit_ledger_events DROP CONSTRAINT IF EXISTS credit_ledger_events_procedure_row_id_fkey;

DO $$
DECLARE spec RECORD;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('procedure_submissions',  'procedure_row_id',        'procedure', 'row'),
        ('procedure_submissions',  'parent_procedure_row_id', 'procedure', 'row'),
        ('procedure_usage_events', 'procedure_row_id',        'procedure', 'row'),
        ('credit_ledger_events',   'procedure_row_id',        'procedure', 'row')
    ) AS t(tbl, col, typ, mode) LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tg_ref_%s_%s ON %I', spec.tbl, spec.col, spec.tbl);
        EXECUTE format(
            'CREATE TRIGGER tg_ref_%s_%s BEFORE INSERT OR UPDATE OF %I ON %I FOR EACH ROW EXECUTE FUNCTION sl_check_ref(%L, %L, %L)',
            spec.tbl, spec.col, spec.col, spec.tbl, spec.col, spec.typ, spec.mode);
    END LOOP;
END $$;
