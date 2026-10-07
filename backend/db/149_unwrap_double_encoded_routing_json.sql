-- Migration 149: unwrap routing JSON that was stored as a JSON *string* inside jsonb (securityp1.md §5.1 item 2).
-- Next free number: 150.
--
-- WHY: the routing writers passed json.dumps() text through a pool whose jsonb codec encodes again, so
-- routing_decisions.constraints / candidates / ladder / predicted, routing_params.meta / diagnostics and
-- routing_evidence_items.features held '"{...}"' (a string), not '{...}'. SQL `->>` on such a value returns NULL, so
-- any SQL-side check written against these columns would silently FAIL OPEN (e.g. `constraints->>'_caller'`, the
-- instance owner). The Python readers unwrap both forms (app/routing/store.py `_json_value`). The writers now pass
-- objects, so this rewrites the old rows once and leaves no mix.
--
-- Only rows whose value is a JSON string that itself parses as JSON are touched (`jsonb_typeof = 'string'`);
-- a value is replaced by its own content, nothing else changes. Idempotent: a second run finds no string rows.
-- Runs on the control database and on each search member (they receive the control set).

DO $$
DECLARE
    spec RECORD;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('routing_decisions', 'constraints'), ('routing_decisions', 'candidates'),
        ('routing_decisions', 'ladder'), ('routing_decisions', 'predicted'),
        ('routing_params', 'meta'), ('routing_params', 'diagnostics'),
        ('routing_evidence_items', 'features')
    ) AS t(tbl, col)
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = spec.tbl AND column_name = spec.col) THEN
            -- two passes at most: a value can only have been encoded twice
            FOR i IN 1..2 LOOP
                EXECUTE format(
                    'UPDATE %I SET %I = (%I #>> ''{}'')::jsonb WHERE jsonb_typeof(%I) = ''string'' '
                    'AND left(ltrim(%I #>> ''{}''), 1) IN (''{'', ''['')',
                    spec.tbl, spec.col, spec.col, spec.col, spec.col);
            END LOOP;
        END IF;
    END LOOP;
END $$;
