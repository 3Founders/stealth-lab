# Retrieval representation + relevance gate + human-facing display — final report

_core-b lane / branch `gate-2b` / 2026-09-08_

Plan: `.scratch/retrieval-representation-and-frontend-plan.md`.
Audit findings (Part 1): no architecture contradiction — every requirement mapped onto an
existing seam (RRF hybrid retrieval, the non-compensatory applicability cascade,
evidence-based capability ranking, the embedding-provenance columns, the LLM
capability-statement precedent). Nothing parallel was built.

## 1. Exact files changed

**Backend — new**
- `backend/app/services/retrieval_document.py` — canonical retrieval representation + presentation helpers
- `backend/app/services/procedure_display.py` — deterministic `display_name` / `display_description`
- `backend/app/services/relevance_gate.py` — the measured relevance gate
- `backend/scripts/eval_retrieval_quality.py` — `--generate` / `--measure` cutoff-sweep harness
- `backend/scripts/label_retrieval_eval_v1.py` — reproducible label encoding
- `backend/scripts/eval_representation_before_after.py` — old vs new representation, same model
- `backend/db/44_procedure_retrieval_representation.sql`
- `backend/db/45_procedure_engineering_fixture_flag.sql`
- `backend/tests/test_retrieval_document_offline.py`, `test_procedure_display_offline.py`,
  `test_capture_procedure_representation_offline.py`,
  `test_backfill_procedure_representation_offline.py`, `test_relevance_gate_offline.py`,
  `test_retrieval_quality_e2e.py`
- `backend/tests/data/retrieval_eval_v1.{jsonl,README.md,candidates.jsonl,report.json,before_after.json}`

**Backend — modified**
- `backend/app/services/procedures.py` — `capture_procedure` / `supersede_procedure` carry the new columns; representation contract (Part 18)
- `backend/app/services/skill_ingestion.py` — 3 divergent ad-hoc embedding-text builders → one `build_skill_retrieval_document`; display metadata on every ingest
- `backend/app/services/applicability.py` — procedure lexical leg (Part 5); visibility filter on candidate hydrate (Part 20); `goal_text` threading
- `backend/app/services/domain_search.py` — relevance gate wired after the cascade; new human-facing fields; `find_best_way` display fields; structured retrieval logging (Part 19)
- `backend/app/services/solution_search.py` — blended cards lead with display metadata + relevance/evidence fields
- `backend/app/services/procedure_graph_api.py` — `get_solution_view` / `get_procedure_detail` gain display + applicability + failure-mode fields
- `backend/app/services/embeddings.py` — `Embedder(provider=...)` one-off override (no fallback chain)
- `backend/scripts/backfill_procedure_embeddings.py` — `--representation` / `--display-metadata` / `--embed-missing` resumable modes, provider-batched, atomic per row, explicit failure log
- `backend/tests/test_supersede_procedure_offline.py`, `test_domain_search_offline.py`, `test_applicability_candidate_relevance_offline.py` — updated for the new columns / fake completeness / lexical leg
- `.gitignore` — `backend/scripts/.backfill_state/`, `backend/logs/`
- `backend/.env` (gitignored, not committed) — `USE_LOCAL_MODELS=true`

**Frontend — modified**
- `frontendv1/src/lib/api/types.ts` — display / relevance / evidence / failure-mode fields on every procedure-bearing shape
- `frontendv1/src/components/domain.tsx` — `RelevanceBadge`, `VerificationLabel`, `EvidenceLine`, `WhyMatched`, `GoodFor`, `FailureModes`; `ProvenanceBlock` accepts a string
- `frontendv1/src/app/search/page.tsx` — result card + BestWayCard redesigned; **`Match 82%` removed**
- `frontendv1/src/app/procedures/[id]/page.tsx`, `frontendv1/src/app/solutions/[id]/page.tsx` — title = display name, lead = display description, new When-to-use / Prerequisites / Failure-modes sections, slug demoted to `<code>`
- `frontendv1/src/webmcp/tools.ts` — adapted to the new verification shape; exposes `relevance`

## 2. Migrations added

| # | file | effect |
|---|---|---|
| 44 | `44_procedure_retrieval_representation.sql` | `procedures.retrieval_document`, `retrieval_document_version`, `retrieval_document_sha256`, `display_name`, `display_description`, `display_metadata_version`; GIN FTS index over `to_tsvector(retrieval_document)` for the lexical leg; partial index over rows the backfill still owes. Additive, idempotent, no data backfill. |
| 45 | `45_procedure_engineering_fixture_flag.sql` | `ADD COLUMN IF NOT EXISTS is_engineering_fixture BOOLEAN DEFAULT FALSE` + a small partial index, so the candidate filter can reference it safely on any deployment. **Filtering on it was evaluated and rejected** — it flags 2475/2478 live rows, so filtering empties product search; the relevance gate is the real defence. |

Both applied to the dev DB; `migrate.py --status` reports them applied with matching checksums. Migration 41 (another lane's, unapplied) untouched.

## 3. Old embedding representation

Three divergent formulas:
- `skill_ingestion.ingest_skill_md` → `parsed.description` (the bare one-line goal).
- `skill_ingestion.compile_skill_artifact` → `" ".join([capability_statement or description, "Workflow:", *raw_step_strings])`.
- `scripts/backfill_procedure_embeddings.py::_embedding_text` → a third hand re-implementation of the second.

None carried when-to-use text, tools, dependencies, domain, constraints, or failure conditions — all real columns / `domain_payload` keys.

## 4. New embedding representation

`build_procedure_retrieval_document(procedure)` → one deterministic, normalized,
section-labelled block, sections emitted only when present, in fixed order:

```
Name: <display_name or de-slugged name>
Purpose: <capability_statement or goal>
When to use: <domain_payload.applies_when> <trigger-shaped goal> <rendered preconditions>
Domain: <domain>
Steps: 1. <goal> 2. <goal> …   (deduped, order-stable, capped 40 steps / 4000 chars)
Tools: <sorted unique tool_requirements>
Depends on: <sorted unique dependency names, paths + SKILL.md stripped, de-slugged>
Constraints: <invariants; postconditions; compatibility prose>   (capped 1500)
Fails when: <failure conditions that name a triggering condition>  (capped 1200)
```

Normalization: NFC unicode; strip markdown emphasis, control chars, the replacement
char, and symbol/emoji/format categories; collapse whitespace. Excluded by construction:
row/procedure/family ids, all `t_*` timestamps, `evidence_refs`, `source_episode_ids`,
`verification_stats`, `embedding*`, `provenance`, `created_by`/`owner_id`/`approved_by`,
`domain_payload['source']` / `['embedding']` / `['resource_manifest']`.

Deterministic: identical procedure content → byte-identical output (10 offline tests pin
this — key-order invariance, section omission, no volatile tokens, caps, slug handling).

## 5. Version identifiers

| constant | value | meaning |
|---|---|---|
| `RETRIEVAL_DOCUMENT_VERSION` | `procdoc_v1` | current canonical recipe |
| `RETRIEVAL_DOCUMENT_IMPORT_VERSION` | `import_pending_reembed` | embedding supplied from elsewhere; owes a re-embed |
| `DISPLAY_METADATA_VERSION` | `disp_v1` | deterministic display metadata is usable |
| `DISPLAY_METADATA_FALLBACK_VERSION` | `disp_v1_deslug_only` | source too thin; de-slugged name only, flagged for repair |
| `RELEVANCE_GATE_VERSION` | `relgate_v1` | current gate parameters |

Stored per row: `procedures.retrieval_document_version`, `retrieval_document_sha256`,
`embedding_model_id`, `embedding_provider`, `embedding_input_type`, `embedding_text_hash`,
`display_metadata_version`. No ambiguity about which recipe produced any embedding.

## 6. How existing records were re-embedded

`python scripts/backfill_procedure_embeddings.py --representation --force`:
loads every live procedure → `build_procedure_retrieval_document` → embed in provider
batches of 64 (row-by-row retry on a batch failure) → validate dimension → one `UPDATE`
per row (vector + all provenance stamps + document + version + sha256, atomic) →
resumable (`WHERE retrieval_document_version <> 'procdoc_v1'`), content-unchanged rows
take a no-embed stamp-only fast path, failures logged to
`.backfill_state/procdoc_v1.failed.jsonl` leaving the old vector + old version intact.

**Provider:** the paid Gemini free tier (`RESOURCE_EXHAUSTED` on all 3 keys) and the
Voyage trial (`add a payment method`) both ran dry mid-migration. The repo's built-in
local-model path (`use_local_models`, `_embed_local`, config already defaulting to
`mxbai-embed-large` @ `http://localhost:11434/v1`) was used instead — 1024-dim, matches
`VECTOR(1024)`, free, unlimited, and it puts query + corpus in **one coherent space**.
`config.py`'s `embedding_provider_chain` default is left untouched (`"gemini,voyage"`);
`.env` carries `USE_LOCAL_MODELS=true`. If a paid Gemini/Voyage tier becomes available,
`--representation --force` re-runs the migration into that space and
`scripts/eval_retrieval_quality.py --measure` re-derives the two gate numbers from the
new report.

## 7. Records successfully migrated

| metric | count |
|---|---|
| live procedures | 2478 |
| `retrieval_document_version = procdoc_v1` | **2478 / 2478** |
| `embedding_model_id = local:mxbai-embed-large` | 2478 / 2478 |
| `embedding IS NOT NULL` | 2478 / 2478 |
| `display_name IS NOT NULL` | 2478 / 2478 |
| `display_metadata_version = disp_v1` | 1902 |
| `display_metadata_version = disp_v1_deslug_only` | 576 |

## 8. Failures

- **Representation re-embed (final local run): 0 failures.** (`{"selected":2478,"reembedded":2478,"failed":0}`.)
  The abandoned Gemini/Voyage attempts left 2247 stale records in the failure log; all of
  those rows were reprocessed successfully by the local run. The stale log was archived
  out of the tree and `.backfill_state/` is now gitignored.
- **Display metadata: 0 failures, 576 fallbacks.** All 576 are eval/test fixture rows
  (`pm-*`, `dr-e2e-*`, `Task API E2e Proc *`, …) whose `goal` is literally `"… goal"` or a
  checklist fragment. They still receive a de-slugged name (never a raw slug) and are
  listed in `.backfill_state/disp_v1.display_quality.jsonl` for source repair. The 1902
  real skills all produced clean `disp_v1` metadata.

## 9. Retrieval evaluation dataset size

`backend/tests/data/retrieval_eval_v1.jsonl` — **57 queries**, **855 retrieved candidates**,
each labelled 0–3, across 8 buckets: exact_match (15), paraphrase (10), vocab_mismatch (7),
technically_related_irrelevant (5), neighboring_domain (4), generic (4),
overlapping_terms_wrong_intent (5), no_match (7). Labels are model-assigned against the
written rubric (`retrieval_eval_v1.README.md`) and flagged for human review; the encoding
is `scripts/label_retrieval_eval_v1.py`, so a reviewer edits a label, re-runs `--measure`,
and the gate number moves — never by taste.

## 10. Retrieval metrics — before / after

**Representation A/B** (`retrieval_eval_v1.before_after.json`; 448 eval procedures re-embedded
in memory both ways, **same model** `local:mxbai-embed-large`, same eval, same cutoff sweep):

| at each representation's own best gate | OLD (goal + "Workflow:" + steps) | NEW (`procdoc_v1`) |
|---|---|---|
| cutoff | 0.6568 | 0.6808 |
| **precision** | **0.469** | **0.653**  (+0.184, +39% rel.) |
| recall | 0.670 | 0.545 |
| F1 | 0.551 | 0.594 |
| no-match zero-result rate | 1.00 | 1.00 |

The canonical document is materially more discriminating: precision rises 47% → 65% at the
optimal gate; the recall it trades away is exactly the "semantically related but
practically irrelevant" long tail the thin representation used to let through (defect #3).

**Gate on vs. gate off** (`retrieval_eval_v1.report.json`; full search path over the live
2478-procedure corpus): presenting everything above the fixture-noise floor (cutoff 0.55)
gives precision **0.28**; at the measured gate (0.6839) precision is **0.695**, P@3 0.766.

## 11. Relevance-gate methodology and measured threshold

`services/relevance_gate.py` operates on cosine similarity (`1 - (embedding <=> query)`,
the `_similarity_score` `find_applicable_procedures` already attaches). It is a **filter**
applied AFTER the applicability cascade and BEFORE presentation — it never re-ranks, and
zero results is a valid return. When `RELEVANCE_GATE_MIN_SIMILARITY` is `None` it fails
open (inert) — a threshold is never guessed.

Methodology: `scripts/eval_retrieval_quality.py --measure` sweeps the cutoff across the
observed similarity range in 40 steps; at each step it computes precision / recall / F1 /
P@3 / nDCG@10 (relevant := label ≥ 2) and the no-match bucket's zero-result rate.
**Selection rule (deterministic, documented): the cutoff with the highest F1 among those
whose no-match bucket returns zero results in ≥ 90 % of its queries; ties → the higher
(more precise) cutoff.**

Measured result → **`RELEVANCE_GATE_MIN_SIMILARITY = 0.6839`**
(precision 0.695, recall 0.525, F1 0.598, P@3 0.766; no-match zero-result rate 1.00 —
the fixture-junk similarity floor tops out at 0.53, so every cutoff ≥ 0.55 satisfies the
constraint and F1 is the tiebreaker). Strong-match band
**`RELEVANCE_LABEL_STRONG_SIMILARITY = 0.7317`** (precision first reaches 0.913). Both
numbers, the full sweep, and the corpus/model context are in
`retrieval_eval_v1.report.json` and the module CHANGELOG.

## 12. Previously-irrelevant results now rejected / lower-ranked

- **"isolate and fix a flaky failing test"** — the thin representation surfaced fixture
  rows `Task API E2e Proc *` (sim ≈ 0.72–0.73). Under the gate the bucket keeps far fewer
  results than a clean exact-match query, and the one wrong-intent query with a real
  answer ("review the meeting notes …" → `meeting-minutes`) still keeps it
  (`test_wrong_intent_gate_discriminates`).
- **"load test my API"** — no longer matches lazy tool-schema *loading* (shared word,
  different intent): every candidate scores below 0.62.
- **"read a very large CSV into pandas"** — every candidate scores 0.47–0.51, all below
  the gate → zero results (correct; `read_large_file` overlaps `structural-summary-before-
  full-read` only lexically).
- **all 7 no-match queries** ("capital of France", "book a flight", …) — top candidate
  ≤ 0.53, gate returns **zero** for every one (`test_no_match_returns_zero`).
- Old representation at its best gate admitted ~2× the irrelevant volume (precision 0.47
  vs 0.65) — see §10.

## 13. Frontend changes

- Search result card, in the user's priority order: **what it does** (display name) →
  **why it's relevant** (`Strong match` / `Relevant` chip, only when the measured gate
  produced a label) → **verified?** (`Verified` / `Community reported` / `Experimental`
  from real state) → **evidence** ("12 successful of 15 recorded executions" — a count,
  not a probability) → **Good for:** (applicability summary) → **Why this matched:**
  (deterministic query↔procedure word overlap). The raw **`Match 82%`** line is gone;
  raw similarity is not rendered anywhere in the product surface.
- Procedure & Solution detail pages: `<h1>` is the display name, lead paragraph the
  display description; new **When to use** / **Prerequisites** / **Known failure modes**
  sections; execution evidence (header) kept visually distinct from the "Why this works"
  claims; the machine slug is a small `<code>` label, "Procedure" / "Solution" stay as the
  technical framing.
- Procedure / Task / Claim stay distinct types (`TypeBadge`); a claim never renders as a
  recommendation.
- No query-time LLM call anywhere in the frontend or the API path.
- `tsc --noEmit` clean; `next build` (Next 16, Turbopack) green, 17 routes. The repo's
  eslint config is **pre-existingly broken** (circular structure on load) — unrelated to
  these files.

## 14. Tests run

| suite | result |
|---|---|
| `test_retrieval_document_offline.py` | 10 pass |
| `test_procedure_display_offline.py` | 9 pass |
| `test_capture_procedure_representation_offline.py` | 3 pass |
| `test_backfill_procedure_representation_offline.py` | 6 pass |
| `test_relevance_gate_offline.py` | 11 pass |
| `test_applicability_candidate_relevance_offline.py` (+3 lexical) | 6 pass |
| `test_supersede_procedure_offline.py` (updated) | pass |
| `test_domain_search_offline.py` (fake completed) | 25 pass (was 8 failing on a pre-existing stub gap) |
| `test_retrieval_quality_e2e.py` (live DB) | **11 pass** — the 10 required checks + the private-procedure leak test |
| **full backend offline suite** (`pytest tests`) | **2270 passed / 18 failed / 312 skipped** |
| frontendv1 `tsc --noEmit` | clean |
| frontendv1 `next build` | green, 17 routes |

Merge baseline (Cline, on `33d4c05`): **2227 passed / 27 failed / 301 skipped**.
After this work: **2270 / 18 / 312** — **+43 passing, −9 failing, zero new failures.**

The 18 remaining, none caused by this work:
- `test_local_agent_runner_offline.py` (14) + `test_behavioral_validation_offline.py` (2)
  + `test_gate3_experiment_offline.py` (1) — `tests/fake_embeddings.py` (commit 827d745)
  monkeypatches `Embedder._embed_via_chain`, which does not exist (`_embed_configured_provider`
  is the seam). Pre-existing.
- `test_mcp_six_tool_surface_offline.py` (2) — `fake_find()` in the test doesn't accept
  `embedding_model_id`; that kwarg predates this work. Pre-existing (present at session
  start before any change).
- `test_migration_upgrade_e2e.py::test_migration_upgrade_path_populated_v1_to_hardening`
  (1) — applies migrations 01..34 then calls `capture_procedure`, which needs the
  `embedding_provider` / `embedding_input_type` / `embedding_text_hash` columns from
  **migration 42** (`42_worker_ingestion_integrity.sql`, ingestion lane) — so it was
  already failing on migration 42 before migration 44. Migration 44 adds more columns to
  the same INSERT; the fix (advance the test's baseline, or make `capture_procedure`
  schema-tolerant) belongs to the ingestion lane that introduced the coupling.
S3's own additions were verified not to introduce any of these by re-running the affected
subset with the S3 delta stashed (identical failure set).

## 15. Remaining issues

1. **Embedding model is `mxbai-embed-large` (local), not a frontier API model.** Forced by
   both paid quotas being exhausted. Retrieval recall (0.53) is bounded by it. Re-running
   `--representation --force` + `eval_retrieval_quality.py --measure` on a paid Gemini or
   Voyage tier will raise recall and re-derive the two gate numbers — the pipeline is
   provider-agnostic and the procedure is documented in the module CHANGELOG.
2. **576 procedures on `disp_v1_deslug_only`** — all eval/test fixtures with unusable
   `goal` text. They render a de-slugged name (never a raw slug) and are listed in
   `.backfill_state/disp_v1.display_quality.jsonl`. Fixing them means repairing the
   fixture source, then `--display-metadata`.
3. **`is_engineering_fixture` flags 2475/2478 rows.** The flag as populated does not mean
   "test junk" — it marks the whole bulk-ingested skill set — so it cannot be used to trim
   product search. If a real product/fixture split is wanted it needs its own curation
   pass; the relevance gate is the interim defence.
4. **eslint is broken repo-wide** (circular config on load) — pre-existing, unrelated.
5. Labels in `retrieval_eval_v1.jsonl` are model-assigned; a human review pass + `--measure`
   re-run is the intended next step and will only sharpen the gate.

## Acceptance criteria (Part 23)

A ✅ every searchable procedure has a canonical representation (2478/2478 `procdoc_v1`).
B ✅ substantially more signal — precision 0.47 → 0.65 at the optimal gate, same model.
C ✅ embeddings explicitly versioned (`retrieval_document_version` + model/provider/hash stamps).
D ✅ existing procedures safely re-embedded (2478/2478, 0 failures, old vector kept on failure).
E ✅ backfill resumable + idempotent (`WHERE version <> current`; sha fast-path; tested).
F ✅ hybrid semantic + lexical RRF intact — lexical leg now also covers the canonical doc.
G ✅ applicability remains a real gate (`test_applicability_still_gates`, e2e).
H ✅ measured gate prevents weak matches (`test_wrong_intent_gate_discriminates`, §12).
I ✅ threshold justified by evaluation data, not guessed (§11, `report.json`).
J ✅ no-match queries return zero (`test_no_match_returns_zero`, 7/7).
K ✅ regression tests demonstrate improved precision (§10, `before_after.json`).
L ✅ stable procedure identity/version semantics intact (`name` unchanged; supersede carry).
M ✅ every frontend-visible procedure has a display name (2478/2478).
N ✅ every one has a human-facing description (2478/2478; fixtures flagged, never a raw slug).
O ✅ frontend no longer presents raw similarity % as relevance (`Match 82%` removed).
P ✅ cards explain capability/applicability/verification/evidence/provenance from real data.
Q ✅ detail pages use human language first, ontology second.
R ✅ no private data leaks through search or explanation (`test_private_procedure_never_leaks…`).
S ✅ no fallback implementation added.
T ✅ no second retrieval system / vector store / registry / ontology.
U ✅ existing production architecture remains the source of truth.
