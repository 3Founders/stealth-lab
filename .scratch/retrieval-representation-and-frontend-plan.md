# Retrieval representation + relevance gate + human-facing display + frontendv1 UX

_core-b lane / branch `gate-2b` / audit done 2026-09-08 against the live repo + live DB._

## Part 1 — audit findings (verified against the repo, not prior notes)

### What already exists and MUST be reused (no parallel systems)

| Concern | Where it lives now | Verdict |
|---|---|---|
| Hybrid retrieval (vector + lexical + RRF) | `services/retrieval.py::HybridRetriever`, `fuse_rrf()` | Keep. RRF fusion is the only cross-signal combiner. |
| Non-compensatory applicability cascade | `services/applicability.py::check_hard_constraints` / `find_applicable_procedures` | Keep as a hard gate. Relevance gate is added AFTER it, never merged. |
| Capability / evidence ranking | `procedure_extraction/failure_handlers.capability_for_stream`, fused via `fuse_rrf` in `find_applicable_procedures` | Keep. Capability ≠ relevance. |
| Procedure search leg | `services/domain_search.py::_search_procedures` → `find_applicable_procedures` | Extend: pass the relevance gate + expose new display/label fields. |
| Blended solution search | `services/solution_search.py::search_solutions` (round-robin by rank position) | Keep interleave. Add display fields + relevance label passthrough. |
| Embedding provider chain (Voyage/Gemini), rate limit, spend, cache | `services/embeddings.py::Embedder` | Keep. No new provider. |
| Embedding provenance columns | `procedures.embedding_model_id/_dim/_provider/_input_type/_text_hash` (migration exists, live) | Extend with a retrieval-document version, not a new table. |
| LLM capability sentence (derived, versioned, validated) | `skill_ingestion.py::_abstract_capability` + `procedures.capability_statement` + `extractor_version` | This is the established "derived text, persisted + validated" precedent for `display_description`. |
| Re-embed backfill pattern | `scripts/backfill_procedure_embeddings.py` (`--replace-existing`, `--limit`, `--source-id`, per-row UPDATE, provenance stamps) | Extend this exact script. Do NOT write a new pipeline. `reembed_procedures_gemini.py` is an older one-off; leave it. |
| Search REST surface | `api/search.py` (`/v1/search`, `/v1/search/recommend`), `api/solutions.py` (`/v1/solutions/search`, `/v1/solutions/{id}`), `api/procedures.py` (`/v1/procedures/{id}`) | Add explicit fields to responses; no new endpoints except an optional `?debug=` surface for raw similarity. |
| Frontend typed client | `frontendv1/src/lib/api/{types,client}.ts` (single source) | Update in place. |

### The actual defects (confirmed)

1. **Impoverished embedding text.** Two call sites build it and they disagree:
   - `skill_ingestion.compile_skill_artifact`: `" ".join([capability_statement or description, "Workflow:", *steps])`
   - `scripts/backfill_procedure_embeddings.py::_embedding_text`: same shape, re-implemented.
   - `skill_ingestion.ingest_skill_md`: embeds **`parsed.description` only**.
   None include applicability/when-to-use, tools, dependencies, domain, constraints, or failure conditions — all of which are columns/`domain_payload` keys that exist on the row.
2. **No retrieval-document version.** `embedding_text_hash` records the hash of whatever text was embedded, but nothing records *which representation recipe* produced it, so a corpus embedded under two recipes is indistinguishable.
3. **Lexical leg is even thinner than semantic.** `retrieval.py::_lexical_search` builds `to_tsvector` from `name` only for `knowledge_nodes`; the procedure leg (`find_applicable_procedures`) has **no lexical leg at all** — it is pure vector + cost + capability. So Part 5's "lexical retrieval should also search meaningful procedure content" is currently unmet for procedures.
4. **No relevance gate.** `find_applicable_procedures` returns the top-`limit` fused survivors regardless of how weak the similarity is. `_similarity_score` is computed and passed through but never thresholded. Zero-result is possible only when the cascade or cold-start kills everything, never on "nothing is actually relevant."
5. **Raw machine names + raw similarity in UI.**
   - `search/page.tsx::BestWayCard` renders `Match {Math.round(similarity_score*100)}%`.
   - `search/page.tsx::evidence()` converts `capability.p_estimate` to a "verified success %" — that one is legitimate (real evidence) but is mixed in next to nothing that explains *why the result matched*.
   - `procedures/[id]/page.tsx` and `solutions/[id]/page.tsx` lead with `proc.name` / `sol.name` — the raw slug (e.g. `mcp-lazy-tool-schema-loading`).
   - No `display_name` / `display_description` anywhere in schema, API, or types.
6. **No "why this matched" data.** `SolutionSearchHit` carries `native_score` / `native_rank` / `matched_by` but nothing human-readable about applicability or match reason.

### Architecture contradictions with the brief

**None.** Every requirement maps onto an existing seam. The one place the brief's model is looser than the repo: "solution" is not a table (it's a read-composition over a procedure row — `api/solutions.py` docstring). The solution detail page is addressed by `procedure_row_id`; display metadata therefore lives on `procedures` and is surfaced through `get_solution_view`. No new identity needed.

## Implementation plan (dependency order, each step a reviewable commit on `gate-2b`)

### S0. Rebase
`git fetch origin && git rebase origin/main` — picks up the frontend session's `ce8700c`, `b8e133e` (globals.css, layout.tsx, deleted `procedures/`+`tasks/` index pages) and the shared `thingstodo.md` commit. Fast-forward only.

### S1. `services/retrieval_document.py` — the canonical representation (no DB, no spend)
- `RETRIEVAL_DOCUMENT_VERSION = "procdoc_v1"` (bump on any recipe change).
- `build_procedure_retrieval_document(proc: Mapping) -> str`: deterministic, normalized, section-labelled plain text assembled from, when present:
  name / display_name · goal / capability_statement · applicability (`domain_payload.applies_when`, `scope`, `preconditions` rendered as prose) · meaningful steps (`steps[].goal|description`, deduped, order-stable) · tools (`domain_payload.tool_requirements`) · dependencies (`procedure_dependencies` refs passed in, or `domain_payload.dependencies`) · domain/category · constraints/invariants (`invariants`, `postconditions`) · failure conditions (`failure_conditions`) only when they materially bound applicability.
- Excludes: ids, timestamps, evidence refs, provenance bookkeeping, embedding sub-dict, audit fields. Whitespace/character normalization; stable key ordering; caps per section so one giant field can't dominate.
- `retrieval_document_sha256(text)` helper.
- Pure unit tests: determinism, stability under key reorder, absence of volatile tokens, section presence/omission, normalization. (`tests/test_retrieval_document.py`)

### S2. Migration `backend/db/44_procedure_retrieval_representation.sql` (additive, idempotent)
`ALTER TABLE procedures ADD COLUMN IF NOT EXISTS`:
- `retrieval_document TEXT` (inspectable; the exact text embedded)
- `retrieval_document_version TEXT`
- `retrieval_document_sha256 TEXT`
- `display_name TEXT`
- `display_description TEXT`
- `display_metadata_version TEXT`
Plus a GIN index on `to_tsvector('english', coalesce(retrieval_document,''))` for the lexical leg, and a partial index `WHERE retrieval_document_version IS DISTINCT FROM 'procdoc_v1'` for backfill resumability. Header states next free number = 45. No backfill in the migration itself (fresh-start rule).

### S3. Wire the builder into the write paths (half-gate: gate + writer together)
- `procedures.py::capture_procedure` + `supersede_procedure`: accept `retrieval_document`, `retrieval_document_version`, `retrieval_document_sha256`, `display_name`, `display_description`, `display_metadata_version`; add to `_SUPERSEDE_CARRY_COLUMNS`.
- `skill_ingestion.py`: replace both ad-hoc `embedding_text` constructions and the `ingest_skill_md` description-only embed with `build_procedure_retrieval_document(...)`; embed *that*; persist the doc + version + hash. Generate `display_name`/`display_description` via the **existing** `_abstract_capability` LLM path (extended prompt, same validation/persist/version discipline; deterministic fallback: title-case + de-slug the name, first sentence of goal). `display_metadata_version = "disp_v1"`.
- V0/ingestion contract (Part 18): a procedure cannot enter the searchable corpus with `embedding IS NOT NULL` but `retrieval_document_version` NULL, nor with a NULL `display_name`. Enforced in `capture_procedure` + a check in the ingestion outcome path; failure → explicit `rejected`/`error` outcome, never a silent partial row.

### S4. Extend the backfill — `scripts/backfill_procedure_embeddings.py`
- New `--representation` mode: select live rows where `retrieval_document_version IS DISTINCT FROM RETRIEVAL_DOCUMENT_VERSION`; build the doc; embed via `Embedder(rate_limit_pool=pool)`; validate dim; `UPDATE` embedding + all provenance stamps + `retrieval_document*` **atomically per row**; resumable (re-run skips done rows); `--limit` for worker-safe chunks; explicit failure list written to `backend/scripts/.backfill_state/procdoc_v1.failed.jsonl`; a row that fails to embed keeps its old vector and old version (never marked as `procdoc_v1`).
- Separate `--display-metadata` mode: same resumable shape for `display_name`/`display_description` using the ingestion LLM path; deterministic fallback recorded as `disp_v1_fallback` so the UI can still show something real (de-slugged) but data-quality reporting can find it.
- Tests with a `FakePool`/fake embedder: resumability, atomic write, failure recording, dim-mismatch abort, no-partial-state. (`tests/test_backfill_procedure_representation.py`)

### S5. Lexical leg for procedures (Part 5)
- `find_applicable_procedures` / `_fetch_candidate_pool`: add a lexical candidate leg over `to_tsvector('english', retrieval_document)` (falls back to `name || ' ' || goal` when the doc is absent), RRF-fused into the existing cost/relevance pre-filter via `fuse_rrf` — same primitive, no new formula. Keeps the "no embedding → no extra round trip" contract by only adding the leg when a query string is available.

### S6. Relevance gate — measured, not guessed (Parts 6, 7, 8)
- **Eval set** `backend/tests/data/retrieval_eval_v1.jsonl`: 40–60 real StealthLab queries across the required buckets (exact, paraphrase, vocab-mismatch, technically-related-but-irrelevant, neighbouring-domain, generic, overlapping-terms-different-intent, no-good-match). Each query → the top-K retrieved procedure ids with a 0–3 label (rubric documented in `backend/tests/data/retrieval_eval_v1.README.md`). **Labels are assigned by me against the written rubric and flagged for human review** — a background session has no human annotator; this is called out explicitly, not hidden.
- **Harness** `backend/scripts/eval_retrieval_quality.py`: runs the live retrieval path for each query, sweeps a cosine-similarity cutoff over the observed score range, computes precision / recall / F1 / P@3 / nDCG at each cutoff and the zero-result rate on the no-match bucket. Emits a report.
- **Gate**: `services/relevance_gate.py` with `RELEVANCE_GATE_VERSION` and a single documented cutoff = the value from the sweep that meets a stated rule (max F1 subject to no-match bucket returning zero ≥ 90% of the time; if two cutoffs tie, the higher/precision-favouring one). The number is written into the module with a comment citing the report file and the measured precision/recall at that point. Applied in `domain_search._search_procedures` (and thus `search_solutions`) AFTER the applicability cascade, BEFORE presentation. Fewer results / zero results is a first-class outcome.
- **Regression tests** `tests/test_retrieval_quality.py`: the 10 required assertions, written against desired behaviour, seeded with fixtures (not asserting current output).

### S7. API contract (Part 16)
- `domain_search._search_procedures` result dict + `SearchResponse` / `SolutionSearchHit` gain: `display_name`, `display_description`, `applicability_summary` (prose from `domain_payload.applies_when` + rendered preconditions), `relevance_label` (`strong` | `relevant` | none — derived from gate margin, documented bands), `relevance_reason` (deterministic: which query terms / applicability facets overlapped), `verification_state` (already present), `evidence_summary` (`{p_estimate, evidence_count, success_count}` from capability), `provenance` (already), `scope`. Raw `similarity_score` stays but is renamed in intent to debug-only and only emitted when `?debug=1`.
- `get_solution_view` + `SolutionDetail` + `ProcedureDetail`: add `display_name`, `display_description`, `applicability_summary`, `failure_modes` (from `failure_conditions`). `api/procedures.py::/v1/procedures/{id}` returns them.
- `types.ts` updated to match exactly; nothing invented.

### S8. Frontend (Parts 12–15, 17)
- `search/page.tsx`:
  - `BestWayCard` + `ResultRow` lead with `display_name`, then `display_description`, then a `relevance_label` chip (`Strong match` / `Relevant`) ONLY when backed by the gate, then `Verified`/`Experimental`/`Community reported` from `verification_state`+`provenance`, then evidence (`12 successful executions`), then `applicability_summary` under "Good for:", then `relevance_reason` under "Why this matched:". **Remove** the `Match 82%` line (raw similarity moves behind a `?debug` toggle).
  - Keep `TypeBadge` distinguishing Procedure / Task / Claim (Part 17); the claim leg still renders as a proposition, never as a recommendation.
- `procedures/[id]/page.tsx`: `<h1>` = `display_name`; `display_description` lead paragraph; sections: When to use (`applicability_summary`), Prerequisites (`preconditions`), Procedure (steps), Verification (`verification_state` + evidence), Known failure modes (`failure_modes`), Provenance/source, Scope, Version/history. Raw `name` shown as a small secondary `<code>` label.
- `solutions/[id]/page.tsx`: same — title = `display_name`, lead = `display_description`, evidence clearly separated from claims, provenance retained, raw slug demoted.
- `components/domain.tsx`: add `RelevanceLabel`, `EvidenceLine`, `WhyMatched` presentational components; extend `StatusBadge` mapping to `experimental` / `community_reported` only where state supports it.
- No query-time LLM calls anywhere in the frontend or API path.

### S9. Observability (Part 19)
Structured `log.info` (existing `log` loggers) + reuse of the embedding usage-log mechanism for: retrieval-document build (version, section count, char len — no contents), embedding gen (model, version), backfill progress/failures, relevance-gate decisions (query hash, cutoff, kept/dropped counts — no private text), zero-result searches, eval metrics. Nothing logs secrets or private procedure bodies.

### S10. Security / access (Part 20)
Access filtering already precedes ranking in `find_applicable_procedures` (`access_scope` → `visibility_predicate`) and in the claim/task legs (`scope_predicates`). The relevance gate runs on already-scoped survivors only. `relevance_reason` is built from the *query* terms and the *matched procedure's own* facets — and only for procedures that passed the visibility filter — so it cannot leak a private row's metadata. Add a test asserting a private procedure never appears in results or in any explanation field for an out-of-scope viewer.

### S11. Test / lint / build (Part 22)
`backend`: `python -m pytest tests -q` (offline, DATABASE_URL unset) + the new suites. Live retrieval eval run with the real DB + keys for the before/after numbers. `frontendv1`: `npm run lint`, `npm run typecheck`/`tsc`, `npm run build`. Record counts in every commit message.

## Cost / risk notes
- Re-embed ~2400 short docs: Voyage/Gemini, well under $1, ~10–20 min paced.
- `display_name`/`display_description` via the ingestion LLM path for ~2478 rows: this is the real spend/time item (local `gemma-4-31B-it` per the code, or the configured provider). Deterministic fallback keeps the UI honest if a row can't be done.
- Data migration is additive + reversible (new nullable columns; old `embedding` kept on failure).
