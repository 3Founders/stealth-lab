-- 197_tool_call_audit_append_only.sql
--
-- Next free number: 196 (tool approvals) was highest before this file.
--
-- What this adds: receipts are append-only for the application role (review #29). A BEFORE UPDATE
-- OR DELETE trigger raises, so a stray UPDATE/DELETE cannot rewrite history through the normal
-- connection; the hash chain in scripts/verify_audit_chain.py still catches an owner who disables
-- the trigger. `audit_demo.py --tamper` disables it inside one transaction, proves the chain fails,
-- and rolls back, which is why the demo can still show the proof without weakening this guard.
--
-- Access: application only, like 190. Idempotent.

CREATE OR REPLACE FUNCTION tool_call_audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tool_call_audit is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

DROP TRIGGER IF EXISTS tool_call_audit_no_update ON tool_call_audit;
CREATE TRIGGER tool_call_audit_no_update
    BEFORE UPDATE ON tool_call_audit
    FOR EACH ROW EXECUTE FUNCTION tool_call_audit_append_only();

DROP TRIGGER IF EXISTS tool_call_audit_no_delete ON tool_call_audit;
CREATE TRIGGER tool_call_audit_no_delete
    BEFORE DELETE ON tool_call_audit
    FOR EACH ROW EXECUTE FUNCTION tool_call_audit_append_only();
