# Retrieval / procedural-memory release closure — FINAL REPORT

_Branch `gate-2b`. Start SHA `e476e96`. Re-measured against the live repo
+ live DB, 2026-09-08. Companion docs in `.scratch/retrieval-release-closure/`._

## 0. Release gate result

**Retrieval / procedural-memory release gate: NOT PASS.**

**Overall product launch: NOT ASSESSED BY THIS TASK.**

The retrieval representation, versioning, lexical leg, relevance-gate
machinery, human-facing display surfaces, access-control ordering and the
frontend are in good shape and measurably better than before. The gate
does **not** pass because four release-blocking items cannot be closed in
this environment and one architectural gap was newly measured:

1. The **production embedding model is not finalised** — the controlled
   benchmark is still completing (voyage row) and the choice depends on a
   billing decision only Chaitanya can make (§1 / `CHITANYA-SETUP.md`).
2. The corpus is therefore **not embedded in a finalised production
   space** — it is on `local:mxbai-embed-large`, chosen under duress when
   paid quotas were exhausted, not by measured decision (§6).
3. The **relevance threshold is model-specific** and must be re-derived
   after §1/§6 (§7).
4. **No human label validation** has been done; an independent-model
   cross-check shows only **~77 % same-relevant-class agreement**, so the
   absolute precision numbers are not yet release-grade (§3).
5. **Newly measured:** the semantic relevance gate does **not abstain**
   on "same tool / same objective, incompatible environment or wrong
   direction" queries — it surfaces a topically-relevant but
   constraint-violating procedure as an apparently-good match (§11).

Plus three non-blocking-but-named gaps: embedding spend is not recorded
in the `llm_spend` ledger; there is no code-level provider-policy /
data-classification gate on the embedding path; procedural-memory outcome
telemetry (retrieval → execution, false-reuse) is not instrumented.

---

## 1. Before / after retrieval representation

| | OLD | NEW (`procdoc_v1`) |
|---|---|---|
| text | `capability_statement\|goal + "Workflow:" + raw step goals` (3 divergent implementations) | `build_procedure_retrieval_document()` — one deterministic, normalized, section-labelled text: Name / Purpose / When to use / Domain / Steps / Tools / Depends on / Constraints / Fails when. Volatile ids/timestamps/evidence/provenance/audit fields excluded by construction. |
| versioned | no | `retrieval_document_version` + `retrieval_document_sha256` per row |
| coverage | n/a | **2478 / 2478** live active versions |

## 2. Before / after metrics (same model, same eval — isolates the representation)

`retrieval_eval_v1.before_after.json` (448 procedures, `local:mxbai-embed-large`, pure cosine, per-representation optimal gate):

| | OLD | NEW |
|---|---|---|
| optimal cutoff | 0.6568 | 0.6808 |
| **precision** | 0.469 | **0.653**  (+0.184, +39 % rel.) |
| recall | 0.670 | 0.545 |
| F1 | 0.551 | 0.594 |
| no-match zero-result rate | 1.00 | 1.00 |

The canonical document is materially more discriminating; the recall it
trades away is the "topically related but not useful" long tail.

## 3. Human-label validation

**Not completed** — no human reviewer available. `label-validation.md` +
`label-validation-sample.jsonl` (91 stratified query/candidate pairs, all
buckets, all grade bands, `human_label` BLANK, exact instructions).

Interim signal — independent second-model (`gpt-oss-120b`, different
family) re-graded the 91 pairs:

| agreement metric | value |
|---|---|
| exact 0/1/2/3 label | ~50 % |
| within ±1 | _(see label-validation.md)_ |
| **same relevant / not-relevant class** (label ≥ 2 — drives precision/recall) | **~77 %** |

Read: the model labels are **directionally usable** and fine for the
RELATIVE comparisons in this closure (representation A/B, model vs model —
labels held constant), but ~23 % of relevance calls are contestable, so
they are **not a sufficient basis for an absolute release precision
claim**. Named open item.

- Minimum records for human review: the **91** in the sample file.
- Full set for a strong claim: all **855** candidates.

## 4. Embedding model comparison  (`embedding-model-benchmark.md`)

Bounded index (448 eval procedures), threshold derived per model by the
documented rule:

| | local mxbai | gemini emb-001 | voyage-3-large |
|---|---|---|---|
| MRR | 0.623 | **0.634** | _measurement running_ |
| nDCG@10 | 0.726 | **0.741** | _running_ |
| Recall@10 | 0.722 | **0.734** | _running_ |
| F1 @ own threshold | 0.589 | **0.632** | _running_ |
| precision / recall @ threshold | 0.762 / 0.481 | 0.621 / 0.643 | _running_ |
| no-match FP rate | 0.00 | 0.00 | _running_ |
| embedding failures | 0 | 0 | _running_ |

`gemini` > `local` on every ranking metric (modestly). `voyage` is the
tie-breaker and is still embedding at 3 RPM.

## 5. Final embedding model

**NOT FINALISED.** Currently `local:mxbai-embed-large` (2478 / 2478).
Decision tree in `embedding-model-benchmark.md` §Selection — it hinges on
whether Chaitanya provisions a paid Voyage or Gemini tier.

## 6. Final threshold

**NOT RE-DERIVED.** Current `RELEVANCE_GATE_MIN_SIMILARITY = 0.6839`
(local corpus). The per-model benchmark already shows the optimum shifts
(gemini 0.6829, local eval-index 0.6906) — it must be re-run against the
final model + fully re-embedded corpus via
`scripts/eval_retrieval_quality.py --measure`.

## 7–10. Recall@K / Precision@K / MRR / nDCG   — see §4 table.

## 11. No-match / abstention results  (`retrieval_abstention_v1.jsonl`, `test_retrieval_abstention_e2e.py`)

- Original: **7 / 7** no-match queries → zero.
- Expanded strict set (completely-unrelated, wrong-domain,
  superficial-wording-different-intent): **15 / 15** → zero.
- **Total genuine no-match evidence: 22 / 22 → zero.** ✅
- **Gap tier** (same-tool-different-objective, same-objective-
  incompatible-environment, family-cousin-do-not-reuse): **7 / ~12**
  surfaced a topically-relevant but constraint-violating procedure above
  the gate — e.g. "run the Playwright webapp suite against a **native iOS
  app**" → `webapp-testing` (0.77); "apply the **Oracle**-to-Postgres
  plan to a **MySQL** source" → the whole oracle-to-postgres family
  (0.78); "**downgrade** React 18 → 17" → `react18-legacy-context`;
  "isolate agents with **Docker** instead of worktrees" →
  `Parallel-agent git-worktree isolation`.

  A cosine-similarity relevance gate captures topic, not compatibility.
  The layer that *should* reject "incompatible environment" is the
  applicability cascade (preconditions / invariants / scope) — but the
  ingested skill corpus carries structured preconditions on only ~41 of
  2478 rows, so that gate is effectively empty. `test_retrieval_
  abstention_e2e.py::test_incompatible_environment_gap_is_measured` pins
  the current leak count (≤ 8) so it cannot regress; lowering it needs
  either richer structured preconditions or a compatibility-check step.
  **Release risk** — a consumer that treats "retrieved" as "safe to
  reuse" would misapply these.

## 12. Display-name coverage

| | live rows |
|---|---|
| non-empty `display_name` | 2478 / 2478 |
| non-empty `display_description` | 2478 / 2478 |
| `disp_v1` (deterministic, usable) | 1902 |
| `disp_v1_deslug_only` (flagged) | 576 → being reworked (§13-17) |

## 13–17. Display-name cleanup  (`display-name-*.jsonl`, S3 run `bwatrdbzi`)

Of the 576 `disp_v1_deslug_only`:
- **~537 are non-content test/eval fixtures** (`perf-*`, `staleness-eval-
  gap-*`, `pm-*`) whose `goal` is a placeholder (`"<name> goal"`). The
  pipeline pre-filter routes these to **HUMAN_REVIEW** — a model cannot
  and must not invent a description with no content. Their de-slugged
  names (`Perf Ce 19f99347`) are honest placeholders, not raw slugs.
- **~40 are real ingested skills with stub goals but real steps** (e.g.
  `build-zoom-bot`, `choose-zoom-approach`). These go through:
  generation (`deepseek-v3.1` on the existing General Compute provider) →
  deterministic validation (length, no id/uuid/hex/slug/db-term/hype,
  UTF-8) → independent judge (`gpt-oss-120b`) → PASS persists as
  `display_metadata_version='disp_v2_llm'` with provenance in
  `domain_payload.display_provenance`; REGENERATE retries once;
  HUMAN_REVIEW → queue.
- Verified working on the smoke sample — e.g. `build-zoom-bot` → "Build
  Zoom Bots Using SDKs And APIs", judge verdict PASS.

**Final counts:** _(from `display-name-report.md` when `bwatrdbzi`
completes — total affected 576 / generated N / accepted N / regenerated N
/ human-review N / unresolved N)._

`canonical_name` is never touched.

## 18. Embedding migration count / failures

Local corpus re-embed: **2478 / 2478**, **0 failures**, one coherent
space (`local:mxbai-embed-large` dim 1024), 0 duplicate active versions,
0 `import_pending_reembed` sentinels. Determinism check: 0 failures over
200 sampled; stored-sha vs recomputed-sha: **2 / 200 drift** (~1 %) — a
`--representation` re-run would resolve; within noise for the release
decision, flagged for cleanup.

## 19. Test results

Backend offline suite (HEAD `e476e96`, `DATABASE_URL` set — some `_e2e`
run): **2270 passed / 18 failed / 312 skipped**. Final count after this
closure's additions: _(from `blbg23aos`)_.

Merge baseline (`33d4c05`, Cline): 2227 / 27 / 301.

Live e2e: `test_retrieval_quality_e2e.py` **11 / 11** (incl. private-
procedure leak test + scope-filter test — access control before ranking
verified). `test_retrieval_abstention_e2e.py`: strict tier passes;
gap-tier documented.

Frontend: `tsc --noEmit` clean, `next build` green (17 routes) at last run
(another session has since added `ScopeBadge` to `procedures/[id]` — not
retrieval; re-verify at merge).

**Pre-existing failures (proven pre-date this workstream — same set fails
with the S3 delta stashed, and 16 of 18 were failing before commit
`9223b54`):**
- `test_local_agent_runner_offline.py` (14) + `test_behavioral_
  validation_offline.py` (2) + `test_gate3_experiment_offline.py` (1) —
  `tests/fake_embeddings.py` (commit `827d745`, ingestion lane)
  monkeypatches `Embedder._embed_via_chain`, a method that does not exist
  (`_embed_configured_provider` is the seam).
- `test_mcp_six_tool_surface_offline.py` (2) — `fake_find()` in the test
  doesn't accept `embedding_model_id`; predates this workstream.
- `test_migration_upgrade_e2e.py` (1) — applies migrations 01–34 then
  calls `capture_procedure`, which needs the `embedding_provider` columns
  from **migration 42** (`42_worker_ingestion_integrity.sql`, ingestion
  lane) — already broken before migration 44. Migration 44/45 add more
  columns to the same INSERT; the fix (advance the test's baseline or
  make `capture_procedure` schema-tolerant) belongs to that lane.

**Delta from this closure: 0 new failures** (the additions are new e2e
files that skip without `DATABASE_URL`).

## 20. Provider configuration status

`CHITANYA-SETUP.md` is the full inventory (actual repo var names, no
secret values). Blocking for a production embedding migration:
- a **paid tier** on the selected provider (Voyage payment method OR
  Gemini billing) — free tiers cannot complete a 2478-doc migration;
- an explicit **recorded decision** that the selected provider + General
  Compute are approved to receive StealthLab procedure text.

## 21. Unresolved risks

| # | risk | severity |
|---|---|---|
| R1 | production embedding model not selected/embedded; threshold not re-derived | **blocking** |
| R2 | eval labels not human-validated (~77 % independent-model same-class agreement) | **blocking** for an absolute precision claim |
| R3 | relevance gate does not abstain on incompatible-environment / wrong-direction queries; applicability cascade empty (41/2478 rows have preconditions) | **high** — confidently-wrong retrieval |
| R4 | embedding spend not recorded in `llm_spend`; no provider-policy/data-classification gate on the embedding or display-gen path | medium |
| R5 | procedural-memory outcome telemetry (retrieval→execution, false-reuse) not instrumented | medium |
| R6 | ~537 fixture rows in the searchable corpus have no product-quality display metadata (correctly flagged, not invented); the corpus is 2475/2478 fixtures — a real product/fixture split is unresolved | medium |
| R7 | 2/200 retrieval-document sha drift; migration ledger has cross-branch 39/40 number collisions (pre-existing) | low |
| R8 | branch not pushed; `origin/gate-2b` diverged; interleaved with another lane's Phase-1 commits | low (process) |

## 22. Explicit release recommendation

**Do not declare the retrieval / procedural-memory workstream release
ready.** The representation + versioning + gate machinery + frontend are
production-shaped and measurably better; ship them as an internal
milestone. Before a release claim, in order:

1. Chaitanya provisions a paid embedding tier and records provider
   approval (`CHITANYA-SETUP.md`).
2. Finish the benchmark (voyage row), select the model on measured
   evidence + operational fit, re-embed all 2478 active versions in that
   space with `backfill_procedure_embeddings.py --representation --force`
   (0 silent failures), re-derive the threshold with
   `eval_retrieval_quality.py --measure`.
3. Human-validate at least the 91-pair sample; re-run `--measure` on
   human-anchored labels.
4. Decide the incompatible-environment gap (R3): accept it with an
   explicit "retrieval ≠ authorization to reuse" contract enforced
   downstream, or add structured preconditions / a compatibility step.
5. Wire embedding + display-gen spend into `llm_spend`; add the
   provider-policy gate before enabling `PRIVATE_VISIBILITY_ENABLED`.
6. Finish the display-name rework (S3 `bwatrdbzi`), review the human
   queue.
