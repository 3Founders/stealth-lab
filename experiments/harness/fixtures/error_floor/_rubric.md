# Extraction error-floor rubric (Band 3 prep, board MEASURE item 4)

Measures observation-extraction precision/recall against hand-written
agreed-correct observations ("gold") over 42 curated trace excerpts.
This file is the CONTRACT: the grader (`error_floor.py`) implements exactly
the rules below and nothing else. Any rule change is a fixture+rubric+test
change together, never a silent tweak.

## What counts as one excerpt

One excerpt = ONE real-schema trace event (`trace_event`: at minimum
`event_id`, `tool_name`, `tool_input`) plus its `gold` list: every
observation a careful human agrees the event warrants, across ALL
observation types (an Edit event can carry both a mechanical
`file_touched` AND a semantic label — both are gold if both are agreed).
The model extractor's NONE contract ("too generic to say anything
semantic -> emit nothing") governs ONLY the semantic layer: a bare `ls`
still executes a command mechanically and keeps its
`command_executed` gold; it just warrants no label. Every excerpt
carries `notes`; excerpts whose correct answer admits two readings are
EXCLUDED from the set entirely (authorship rule).

## Observation types (closed set)

`file_touched`, `commit_made`, `test_run`, `command_executed`
(deterministic taxonomy, ticket 04) + `semantic_label` (model extractor).
A fixture using an unknown type fails validation; new types extend this
rubric deliberately.

## Match rules

Gold g matches prediction p iff:

1. `g.observation_type == p.observation_type`, AND
2. their CANONICAL KEYS are equal after normalization:
   - `file_touched` -> key = `properties.file_path`;
     normalize: backslashes -> forward slashes, strip leading `./`.
   - `commit_made` / `test_run` / `command_executed` ->
     key = `properties.command`; normalize: collapse whitespace runs to
     single spaces, strip ends.
   - `semantic_label` -> text = `label`; tokenize lowercase `[a-z0-9]+`,
     drop STOPWORDS {a, an, the, was, were, is, are, been, be, to, of,
     in, on, for, and, with, by, at, from}; match iff Jaccard(token sets)
     >= 0.5 (`SEMANTIC_JACCARD_THRESHOLD`).

Matching is ONE-TO-ONE greedy in gold-list order, first unused matching
prediction wins (deterministic; lists are tiny so optimality is not worth
the opacity). A prediction already consumed can never match twice:
duplicate predictions cost precision, not recall.

## Confusion accounting

- TP = matched pairs.
- FP = unmatched predictions (counted under the PREDICTION's type).
- FN = unmatched golds (counted under the GOLD's type).
- precision = TP/(TP+FP); recall = TP/(TP+FN); f1 = harmonic mean.
  Zero denominators yield `null`, rendered `-` — never a fake 0.0 or 1.0.

Reason codes on every FP/FN so the floor is diagnosable, not just countable:

- FN `missing_no_candidate`      — no prediction of that type existed.
- FN `key_mismatch_same_type`    — right type, wrong key.
- FN `type_mismatch_in_predictions` — a prediction carried the SAME
  normalized key under a DIFFERENT type (extractor chose the wrong label).
- FP `extra_no_gold`             — nothing in the gold corresponds.
- FP `type_mismatch_vs_gold`     — its key matches a gold of another type
  (mirror of the FN case above).

## Scoreboard contract

`scoreboard.format_error_floor()` prints overall + per-type rates WITH
numerator/denominator (house rule), fed by the detail JSON
`run_error_floor.py` writes. Every future extractor change re-runs:

    backend\.venv\Scripts\python.exe experiments\harness\run_error_floor.py \
        --adapter app.services.observations:extract_deterministic_observations

(the harness itself never imports `backend/**` — lane rule — the adapter
string is resolved in the CALLER's environment; without backend on the
path, use `--predictions preds.jsonl` or the offline demo baseline
`--adapter demo_extractor:deterministic_v1_demo`).

## Known deliberate gaps in the demo baseline

The demo adapter mirrors deterministic_v1's published rules INCLUDING
quirks, so the instrument demonstrates non-trivial readings out of the
box: compound `cd x && git commit` and `git -c ... commit` miss
commit_made (prefix rule too narrow); `pip install pytest-cov` false-ly
counts as test_run (substring marker); NotebookEdit touches no key.
These five divergences ARE pinned by test — if the mirror drifts from
those numbers, either the mirror or the rubric changed, and the suite says so.
