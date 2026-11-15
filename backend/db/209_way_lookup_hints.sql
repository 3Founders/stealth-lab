-- Which steps of a Way may need a find_ways lookup when a run reaches them (app/execution/way_lookups.py,
-- docs/way_executor.md "Lookups"). JEV judges each reasoning step once per version of a Way -- is it a reusable task,
-- and is it narrower than the Way's own goal? -- and every later run reads the answer here for free. Keyed by the
-- Way's row (one version) and a hash of its expanded plan, so a changed sub-Way gets judged again.
CREATE TABLE IF NOT EXISTS way_lookup_hints (
    procedure_row_id  uuid NOT NULL REFERENCES procedures (id) ON DELETE CASCADE,
    plan_hash         text NOT NULL,
    hints             jsonb NOT NULL,                  -- {step path: {lookup, kind, confidence, relation, why}}
    judge             text NOT NULL DEFAULT '',        -- the judge chain that answered
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (procedure_row_id, plan_hash)
);
