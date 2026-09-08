# Retrieval / embedding / procedural-memory — current state (re-measured)

_Reconstructed 2026-09-08 from the live repo + live DB, not from the prior report._

## Git

| | |
|---|---|
| branch | `gate-2b` |
| HEAD | `e476e96cce516e8593ffb9be8f045c13d764ae93` |
| working tree | clean except untracked non-mine probes (`backend/scripts/check_mcp_readonly.py`, `open_mcp_inspector.ps1`) and pre-existing untracked junk (`.codex/`, zips, `mcp_server_*.txt`, `ssh-key-*`, spec `.md`s) |
| ahead of `origin/main` (`33d4c05`) | 13 commits — 10 `core-b:` retrieval commits interleaved with 3 Cline Phase-1-auth commits (`33463dc`, `382d53b`, `e63ba85`, `3e47906`) |
| core-b commits | `9223b54`, `122466d` (already in origin/main via merge `33d4c05`); `289947f`, `902096f`, `030aaa2`, `3413423`, `5dd0830`, `ccf302a`, `b50b094`, `9e897be`, `e476e96` (local-only) |
| pushed? | no. `origin/gate-2b` is stale at `5be4f2e` and has diverged (never rebase-safe to overwrite); the integration path is a `gate-2b→main` merge done by the launch-compliance lane |

## Migrations

`migrate.py --status`: applied through 45 **except 41** (`41_phase1_security_boundaries.sql`, Cline's Phase-1, still `pending` — not this workstream's).

Retrieval-workstream migrations:
- **44** `44_procedure_retrieval_representation.sql` — applied, checksum matches. Adds `procedures.retrieval_document`, `retrieval_document_version`, `retrieval_document_sha256`, `display_name`, `display_description`, `display_metadata_version`; GIN FTS index over `to_tsvector(retrieval_document)`; partial "pending" index.
- **45** `45_procedure_engineering_fixture_flag.sql` — applied, checksum matches. `ADD COLUMN IF NOT EXISTS is_engineering_fixture BOOLEAN DEFAULT FALSE` + partial index.

**Pre-existing ledger hygiene issue (not introduced here):** `schema_migrations` contains cross-branch number collisions — `39_structured_skill_ingestion` + `39_procedures_engineering_fixture_flag`, and `40_ingested_artifact_extractor_identity` + `40_procedures_engineering_fixture_backfill`. The `*_engineering_fixture_*` files live on another branch; `migrate.py` on `gate-2b` does not list them but the ledger recorded them. This is why `is_engineering_fixture` already existed before migration 45.

## Procedure / version counts

| metric | count |
|---|---|
| `procedures` rows total | 2537 |
| **live active versions** (`t_invalid IS NULL`) | **2478** |
| live with `embedding` | 2478 / 2478 (100%) |
| live with `retrieval_document` | 2478 / 2478 |
| live `retrieval_document_version = 'procdoc_v1'` | **2478 / 2478** |
| live with any other retrieval-doc version | 0 |
| live `procedure_id`s with >1 live row (dup active) | **0** |
| `is_engineering_fixture = true` (live) | 2475 |
| `is_engineering_fixture = false` (live) | 3 |

The 3 non-fixture rows are the hand-curated procedures (`Defer tool-schema loading until needed`, `Parallel-agent git-worktree isolation`, `Structural-summary-before-full-read`); the 2475 fixtures are the bulk-ingested agent/coding skill set. Nothing on `gate-2b` filters `is_engineering_fixture`, so all 2478 are searchable.

## Embedding model / provider

| | |
|---|---|
| **entire live corpus** | `embedding_model_id = local:mxbai-embed-large`, `embedding_provider = local`, dim 1024 — **2478 / 2478, one coherent space** |
| config `embedding_provider_chain` default | `"gemini,voyage"` (unchanged) |
| `.env` `USE_LOCAL_MODELS` | `true` → `_configured_provider()` returns `local` → query side also `local:mxbai-embed-large` (query + corpus share one space) |
| providers supported in `embeddings.py` | exactly **gemini**, **voyage**, **local** (no openai/general_compute embedding path) |
| live provider probe (1 call each, 2026-09-08) | `local` OK 16.2 s cold / `voyage:voyage-3-large` OK 4.1 s / `gemini:gemini-embedding-001` OK 11.8 s (key 1/3 429, rotated) |
| keys present in `.env` (presence only) | `VOYAGE_API_KEY` (`pa-` prefix), `GEMINI_API_KEY` + `GEMINI_API_KEYS` (3, free-tier — bulk hit `RESOURCE_EXHAUSTED`), `OPENAI_API_KEY` empty, `GOOGLE_API_KEY` empty, `GENERAL_COMPUTE_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` empty |

Prior bulk re-embed history: paid Gemini free-tier exhausted on all 3 keys mid-run; Voyage `pa-` key returned "add a payment method" on bulk. Local Ollama `mxbai-embed-large` completed all 2478 with 0 failures.

## Retrieval-document version

`RETRIEVAL_DOCUMENT_VERSION = "procdoc_v1"` (`app/services/retrieval_document.py`).
`RETRIEVAL_DOCUMENT_IMPORT_VERSION = "import_pending_reembed"` (sentinel for vectors built outside the recipe). 0 live rows carry the sentinel.

## Display-metadata coverage

| `display_metadata_version` | live rows |
|---|---|
| `disp_v1` (deterministic, usable) | **1902** |
| `disp_v1_deslug_only` (source too thin → de-slugged name only, flagged) | **576** |
| null / missing | 0 |

Every live row has non-empty `display_name` **and** `display_description` (2478 / 2478). The 576 `disp_v1_deslug_only` split into two kinds:
- **real skills with stub goals** (e.g. `build-zoom-bot` goal "Use when building bots.", `choose-zoom-approach` goal "Use when choosing architecture.") — have real `steps` / applicability, so a content-grounded regen is possible → S3 target.
- **pure fixtures** (`perf-3585d9a0-A` goal "perf-3585d9a0-A goal", `pm-*`, `dr-e2e-*`) — no real content → HUMAN_REVIEW / permanent flag.

## Retrieval evaluation set

| | |
|---|---|
| file | `backend/tests/data/retrieval_eval_v1.jsonl` |
| queries | **57** across 8 buckets: exact_match 15, paraphrase 10, vocab_mismatch 7, technically_related_irrelevant 5, neighboring_domain 4, generic 4, overlapping_terms_wrong_intent 5, no_match 7 |
| labelled candidates | **855** (label dist: 0→488, 1→167, 2→129, 3→71) |
| label provenance | **model-assigned** (Claude, against the rubric in `retrieval_eval_v1.README.md`), encoded reproducibly in `scripts/label_retrieval_eval_v1.py`, flagged "pending human review". **No human validation pass has been done.** |
| candidate similarity scores in `.candidates.jsonl` | from the last **local:mxbai-embed-large** full-search run |
| harness | `scripts/eval_retrieval_quality.py` (`--generate` / `--measure`), `scripts/eval_representation_before_after.py` |

## Current measured retrieval metrics (local:mxbai-embed-large corpus)

From `retrieval_eval_v1.report.json` (full search path, live 2478-corpus):

| at selected cutoff **0.6839** | |
|---|---|
| precision | 0.6954 |
| recall | 0.5250 |
| F1 | 0.5983 |
| P@3 | 0.7662 |
| nDCG@10 | 0.5788 |
| no-match zero-result rate | 1.00 (7/7) |
| strong-label cutoff | 0.7317 |

Representation A/B (`retrieval_eval_v1.before_after.json`, 448 procedures, same model, pure-cosine): OLD 0.469 P / 0.670 R / F1 0.551 → NEW **0.653 P** / 0.545 R / F1 0.594. Recall@K / MRR / nDCG@K per-K breakdown **not yet measured** — the harness reports aggregate precision/recall/F1/P@3/nDCG@10 only. S1 adds the full @K + MRR table.

## Current relevance threshold

`RELEVANCE_GATE_MIN_SIMILARITY = 0.6839`, `RELEVANCE_LABEL_STRONG_SIMILARITY = 0.7317`, `RELEVANCE_GATE_VERSION = "relgate_v1"`.
Derivation rule (documented in the module): sweep the cosine cutoff over the observed range in 40 steps; pick the highest-F1 cutoff among those whose no-match bucket returns zero results in ≥ 90 % of its queries; ties → higher cutoff. Derived from the **local:mxbai** distribution — must be re-derived for whatever model S1 selects.

## Test status

- Offline suite baseline being re-run now (background job `bjhnw36t8`); prior recorded: **2270 passed / 18 failed / 312 skipped** vs merge baseline 2227 / 27 / 301.
- Live e2e (`test_retrieval_quality_e2e.py`): 11/11 at last run (10 required checks + private-procedure leak test).
- frontend: `tsc --noEmit` clean, `next build` green (17 routes) at last run.

## Unresolved items entering this closure

1. Embedding model is **local mxbai** — forced by both paid quotas dying mid-run; not yet a measured production decision. (S1)
2. Eval labels are model-assigned, never human-validated. (S2)
3. 576 `disp_v1_deslug_only` display names — mix of real-skill-stub-goal and pure-fixture. (S3)
4. Full Recall@K / Precision@K / MRR / per-K nDCG table not yet produced. (S1)
5. Threshold `0.6839` is model-specific and must be re-derived after S1. (S7)
6. No-match / abstention evidence is only 7 queries. (S8)
7. `is_engineering_fixture` flags 2475/2478 — evaluated + rejected as a search filter (would empty the corpus); noted, not acted on.
8. Migration ledger has cross-branch 39/40 number collisions — pre-existing hygiene, out of scope here beyond noting.
9. Production provider keys / billing / policy for the selected embedding + display-gen models not yet verified for production volume. (S10-S12)
10. `origin/gate-2b` diverged; not pushed.
