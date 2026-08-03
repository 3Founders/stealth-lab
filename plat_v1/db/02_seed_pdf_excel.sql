-- The PDF -> Excel reference workflow. Idempotent: safe to re-run.
--
-- This exists so the platform is concrete rather than abstract, and because
-- it is the work that has customers. Six stages:
--
--   1 classify_document      pdf -> doc_type, page_count
--   2 detect_table_regions   pdf -> [(page, bbox)]
--   3 extract_cell_structure pdf + regions -> grid of cells
--   4 validate_types         grid -> typed grid + errors      [no model impl]
--   5 map_to_schema          typed grid + target -> rows
--   6 write_xlsx             rows -> file path                [no model impl]
--
-- Stages 4 and 6 having no model implementation is the point, not an
-- omission. Six stages at 97% accuracy each is 83% end to end; the chain only
-- holds because most stages are exact. When adding a stage, add its
-- deterministic implementation first and only add a model implementation if
-- the deterministic one measurably fails.
--
-- Cost estimates are what the router sorts on. The deterministic
-- implementations are 0 and the model ones are not, which is the whole
-- ordering: the cheap exact path runs first and the model is reached only by
-- escalation, when the cheap path has already failed its success criteria.

-- See 01_schema.sql for why this file is one explicit transaction.
BEGIN;

-- This guard resolves the table the way the INSERTs below will, which is NOT
-- what `current_schema()` answers. `current_schema()` is the first *existing*
-- schema on the path; an unqualified INSERT resolves by *visibility* across
-- the whole path. With search_path = plat_v1,public and an empty plat_v1 --
-- 01 not applied, or applied under a different DB_SCHEMA -- current_schema()
-- is 'plat_v1' and the guard would pass while every INSERT landed in
-- backend_v2's public.task_nodes.
--
-- Today that would happen to fail on a missing column, but only because
-- backend_v2's table lacks `kind`. That is a fact about their schema, not a
-- safety property of ours.
DO $guard$
DECLARE
    ns text;
BEGIN
    SELECT n.nspname INTO ns
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = to_regclass('task_nodes');

    IF ns IS NULL THEN
        RAISE EXCEPTION
            'task_nodes is not visible from search_path %; apply 01_schema.sql first',
            current_setting('search_path');
    END IF;
    IF ns <> current_schema() THEN
        RAISE EXCEPTION
            'unqualified task_nodes resolves to schema %, but this file would be '
            'seeding into %. Refusing: that is another application''s table. '
            'Run `python scripts/seed.py`.', ns, current_schema();
    END IF;
END $guard$;

-- ---------------------------------------------------------------------------
-- 1. classify_document
-- ---------------------------------------------------------------------------
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema, success_criteria)
VALUES (
  'classify_document',
  'Decide whether a PDF is born-digital tabular text or a scan, and count its pages.',
  'leaf',
  '{"type":"object",
    "properties":{"pdf_path":{"type":"string","description":"path to the source PDF"}},
    "required":["pdf_path"]}'::jsonb,
  '{"type":"object",
    "properties":{"doc_type":{"type":"string"},
                  "page_count":{"type":"integer"},
                  "text_density":{"type":"number"}},
    "required":["doc_type","page_count"]}'::jsonb,
  '{"required_keys":["doc_type","page_count"]}'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description,
      kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema,
      output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria;

-- ---------------------------------------------------------------------------
-- 2. detect_table_regions
-- ---------------------------------------------------------------------------
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema, success_criteria)
VALUES (
  'detect_table_regions',
  'Locate table regions in a PDF as (page, bounding box) pairs.',
  'leaf',
  '{"type":"object",
    "properties":{"pdf_path":{"type":"string"}},
    "required":["pdf_path"]}'::jsonb,
  '{"type":"object",
    "properties":{"regions":{"type":"array",
                             "items":{"type":"object",
                                      "properties":{"page":{"type":"integer"},
                                                    "bbox":{"type":"array","items":{"type":"number"}}},
                                      "required":["page","bbox"]}}},
    "required":["regions"]}'::jsonb,
  '{"min_count":{"regions":1}}'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description, kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema, output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria;

-- ---------------------------------------------------------------------------
-- 3. extract_cell_structure
-- ---------------------------------------------------------------------------
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema, success_criteria)
VALUES (
  'extract_cell_structure',
  'Read the cells inside detected table regions into a header row and a grid of strings.',
  'leaf',
  '{"type":"object",
    "properties":{"pdf_path":{"type":"string"},
                  "regions":{"type":"array",
                             "items":{"type":"object",
                                      "properties":{"page":{"type":"integer"},
                                                    "bbox":{"type":"array","items":{"type":"number"}}},
                                      "required":["page","bbox"]}}},
    "required":["pdf_path","regions"]}'::jsonb,
  '{"type":"object",
    "properties":{"header":{"type":"array","items":{"type":"string"}},
                  "grid":{"type":"array","items":{"type":"array","items":{"type":"string"}}}},
    "required":["header","grid"]}'::jsonb,
  '{"non_empty":["header","grid"]}'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description, kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema, output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria;

-- ---------------------------------------------------------------------------
-- 4. validate_types -- deterministic only, deliberately
-- ---------------------------------------------------------------------------
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema, success_criteria)
VALUES (
  'validate_types',
  'Infer a type per column and coerce every cell to it. Deterministic: no model implementation.',
  'leaf',
  '{"type":"object",
    "properties":{"header":{"type":"array","items":{"type":"string"}},
                  "grid":{"type":"array","items":{"type":"array","items":{"type":"string"}}}},
    "required":["header","grid"]}'::jsonb,
  '{"type":"object",
    "properties":{"typed_grid":{"type":"array","items":{"type":"array"}},
                  "columns":{"type":"array",
                             "items":{"type":"object",
                                      "properties":{"name":{"type":"string"},
                                                    "type":{"type":"string"}},
                                      "required":["name","type"]}},
                  "errors":{"type":"array","items":{"type":"string"}}},
    "required":["typed_grid","columns","errors"]}'::jsonb,
  '{"max_count":{"errors":0}}'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description, kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema, output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria;

-- ---------------------------------------------------------------------------
-- 5. map_to_schema -- the one stage that genuinely needs reasoning
-- ---------------------------------------------------------------------------
-- cache_key is the load-bearing bit here. Without it this stage's
-- fingerprint would hash `typed_grid` -- the actual cell values -- and two
-- invoices from the same vendor would never share a cache entry, which is
-- exactly the case the layout cache exists for.
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema,
                        success_criteria, cache_key)
VALUES (
  'map_to_schema',
  'Map typed table columns onto a caller-supplied target schema, producing one object per row.',
  'leaf',
  '{"type":"object",
    "properties":{"typed_grid":{"type":"array","items":{"type":"array"}},
                  "columns":{"type":"array",
                             "items":{"type":"object",
                                      "properties":{"name":{"type":"string"},
                                                    "type":{"type":"string"}},
                                      "required":["name","type"]}},
                  "target_schema":{"type":"object","description":"JSON Schema, or a list of field names"}},
    "required":["typed_grid","columns","target_schema"]}'::jsonb,
  '{"type":"object",
    "properties":{"rows":{"type":"array","items":{"type":"object"}}},
    "required":["rows"]}'::jsonb,
  '{"non_empty":["rows"]}'::jsonb,
  '["columns","target_schema"]'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description, kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema, output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria, cache_key = EXCLUDED.cache_key;

-- ---------------------------------------------------------------------------
-- 6. write_xlsx -- deterministic only, deliberately
-- ---------------------------------------------------------------------------
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema, success_criteria)
VALUES (
  'write_xlsx',
  'Write mapped rows to an .xlsx workbook. Deterministic: no model implementation.',
  'leaf',
  '{"type":"object",
    "properties":{"rows":{"type":"array","items":{"type":"object"}}},
    "required":["rows"]}'::jsonb,
  '{"type":"object",
    "properties":{"path":{"type":"string"},"row_count":{"type":"integer"}},
    "required":["path","row_count"]}'::jsonb,
  '{"file_exists":["path"]}'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description, kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema, output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria;

-- ---------------------------------------------------------------------------
-- The composite that ties the six together
-- ---------------------------------------------------------------------------
-- Declaring the workflow as a task node with an interface is what makes
-- `POST /v1/run` able to reach it: a prompt matches this one node, and the
-- typechecker verifies that the six stages behind it actually satisfy the
-- contract it advertises before any of them run.
INSERT INTO task_nodes (name, description, kind, input_schema, output_schema, success_criteria)
VALUES (
  'pdf_to_excel',
  'Turn the tables inside a PDF into an Excel workbook matching a target schema. '
  'Extract tables from a PDF, type the cells, map them onto the requested fields, '
  'and write an xlsx spreadsheet.',
  'composite',
  '{"type":"object",
    "properties":{"pdf_path":{"type":"string","description":"path to the source PDF"},
                  "target_schema":{"type":"object","description":"the fields the spreadsheet should have"}},
    "required":["pdf_path","target_schema"]}'::jsonb,
  '{"type":"object",
    "properties":{"path":{"type":"string"},"row_count":{"type":"integer"}},
    "required":["path","row_count"]}'::jsonb,
  '{"file_exists":["path"]}'::jsonb
)
ON CONFLICT (name) WHERE t_invalid IS NULL DO UPDATE
  SET description = EXCLUDED.description, kind = EXCLUDED.kind,
      input_schema = EXCLUDED.input_schema, output_schema = EXCLUDED.output_schema,
      success_criteria = EXCLUDED.success_criteria;

INSERT INTO task_edges (edge_type, source_id, source_table, target_id, target_table, properties)
SELECT 'DECOMPOSES_TO', parent.id, 'task_nodes', child.id, 'task_nodes',
       jsonb_build_object('position', stage.stage_index)
FROM (VALUES
        ('classify_document', 1),
        ('detect_table_regions', 2),
        ('extract_cell_structure', 3),
        ('validate_types', 4),
        ('map_to_schema', 5),
        ('write_xlsx', 6)
     ) AS stage(child_name, stage_index)
JOIN task_nodes parent ON parent.name = 'pdf_to_excel' AND parent.t_invalid IS NULL
JOIN task_nodes child  ON child.name = stage.child_name AND child.t_invalid IS NULL
ON CONFLICT (edge_type, source_id, target_id) WHERE t_invalid IS NULL
DO UPDATE SET properties = EXCLUDED.properties;

-- ---------------------------------------------------------------------------
-- Implementations
-- ---------------------------------------------------------------------------
INSERT INTO implementations (task_node_id, name, kind, spec, cost_estimate, latency_estimate_ms)
SELECT t.id, impl.name, impl.kind, impl.spec::jsonb, impl.cost, impl.latency
FROM (VALUES
  -- 1. classify_document
  ('classify_document', 'pdfplumber_density', 'python',
   '{"ref":"tables:classify_document"}', 0.0, 400),
  ('classify_document', 'model_fallback', 'model',
   '{"model":"claude-opus-5",
     "attach_documents":["pdf_path"],
     "system":"Classify the attached PDF. doc_type is digital_table when its text is selectable and laid out in a table, and scanned when the page is an image.",
     "output_schema":{"type":"object",
                      "properties":{"doc_type":{"type":"string"},
                                    "page_count":{"type":"integer"},
                                    "text_density":{"type":"number"}},
                      "required":["doc_type","page_count","text_density"]}}', 0.06, 15000),

  -- 2. detect_table_regions
  ('detect_table_regions', 'pdfplumber_ruled_lines', 'python',
   '{"ref":"tables:detect_table_regions"}', 0.0, 900),
  ('detect_table_regions', 'model_fallback', 'model',
   '{"model":"claude-opus-5",
     "attach_documents":["pdf_path"],
     "system":"Locate every table in the attached PDF. Report one region per table as a 1-indexed page number and a [x0, top, x1, bottom] bounding box in PDF points, origin top-left.",
     "output_schema":{"type":"object",
                      "properties":{"regions":{"type":"array",
                                               "items":{"type":"object",
                                                        "properties":{"page":{"type":"integer"},
                                                                      "bbox":{"type":"array","items":{"type":"number"}}},
                                                        "required":["page","bbox"]}}},
                      "required":["regions"]}}', 0.08, 25000),

  -- 3. extract_cell_structure
  ('extract_cell_structure', 'pdfplumber_cells', 'python',
   '{"ref":"tables:extract_cell_structure"}', 0.0, 1500),
  ('extract_cell_structure', 'model_fallback', 'model',
   '{"model":"claude-opus-5",
     "attach_documents":["pdf_path"],
     "system":"Transcribe the table in the attached PDF. Return the header row and every data row as strings, exactly as printed. Do not reformat numbers, do not fill blanks, and do not skip rows.",
     "output_schema":{"type":"object",
                      "properties":{"header":{"type":"array","items":{"type":"string"}},
                                    "grid":{"type":"array","items":{"type":"array","items":{"type":"string"}}}},
                      "required":["header","grid"]}}', 0.12, 40000),

  -- 4. validate_types -- python only. There is no model implementation and
  --    there should not be one: this stage is what makes the chain exact.
  ('validate_types', 'column_type_inference', 'python',
   '{"ref":"tables:validate_types"}', 0.0, 50),

  -- 5. map_to_schema -- three implementations, cheapest first.
  --
  --    cached_replay   replays the mapping a previous run of this layout
  --                    already paid for. Fails in microseconds when there
  --                    is none, which is the first-run path.
  --    template_match  free and exact when the column names line up.
  --    model_mapping   decides the mapping when neither of the above can,
  --                    then names cached_replay as what to cache. That last
  --                    part is what makes the second invoice from the same
  --                    vendor cost nothing.
  ('map_to_schema', 'cached_replay', 'python',
   '{"ref":"tables:apply_cached_mapping"}', 0.0, 5),
  ('map_to_schema', 'template_match', 'python',
   '{"ref":"tables:map_to_schema_template"}', 0.0, 20),
  ('map_to_schema', 'model_mapping', 'model',
   '{"model":"claude-opus-5",
     "postprocess":"tables:apply_column_mapping",
     "cache_as":"cached_replay",
     "first_layout_requires_review":true,
     "system":"Map source table columns onto the requested target fields. Return the 0-based index of the source column that supplies each target field. Every required target field must be mapped; if a field genuinely has no source column, omit it rather than guessing.",
     "output_schema":{"type":"object",
                      "properties":{"mapping":{"type":"array",
                                               "items":{"type":"object",
                                                        "properties":{"target_field":{"type":"string"},
                                                                      "source_column":{"type":"integer"}},
                                                        "required":["target_field","source_column"]}},
                                    "notes":{"type":"string"}},
                      "required":["mapping","notes"]}}', 0.04, 18000),

  -- 6. write_xlsx -- python only, same reason as stage 4.
  ('write_xlsx', 'openpyxl', 'python',
   '{"ref":"tables:write_xlsx"}', 0.0, 120)
) AS impl(task_name, name, kind, spec, cost, latency)
JOIN task_nodes t ON t.name = impl.task_name AND t.t_invalid IS NULL
ON CONFLICT (task_node_id, name) WHERE t_invalid IS NULL DO UPDATE
  SET kind = EXCLUDED.kind,
      spec = EXCLUDED.spec,
      cost_estimate = EXCLUDED.cost_estimate,
      latency_estimate_ms = EXCLUDED.latency_estimate_ms,
      enabled = TRUE;

COMMIT;
