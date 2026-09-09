# Retrieval / procedural-memory release closure — FINAL REPORT

_Branch `gate-2b`. Start SHA `e476e96`. Re-measured against the live repo
+ live DB, 2026-09-08. Companion docs in `.scratch/retrieval-release-closure/`._

### Staleness re-check (parallel lanes moved HEAD `9070296` → `648c44d`)

Verified after the launch-compliance lane landed Phases 2–8 + migrations 46–48:

| item | this report said | current reality (HEAD `648c44d`) |
|---|---|---|
| my closure commits | `e5d12d7`, `9070296` | intact, ancestors of HEAD |
| offline suite | 2276 / 19 / 314 | **2318 / 18 / 314** (their commit: "18 = pre-existing ingestion/embedding baseline, 0 net new") |
| migrations | through 45; 41 pending | **through 48, none pending** (46 `model_provider_policies` seeded, 47 `procedures.tenant_id` all-NULL, 48 `contributor_profiles`) |
| provider-policy gate (R4) | "no code-level gate" — **wrong now** | `model_provider_policies` table + `guard_send` **exists and is seeded conservatively** (external providers → PUBLIC/GLOBAL classes only; `local` may carry private). Remaining gap: the re-embed/backfill script does not pass `Embedder` a `data_classification`/`policy_pool`, so it does not currently invoke the gate. |
| corpus | 2478 procdoc_v1, one space, 0 dups | **unchanged** |
| display metadata | 576 `disp_v1_deslug_only` | **561 `disp_v1_deslug_only` + 7 `disp_v2_llm`** (S3 job `bwatrdbzi` complete — 7/568 passed the judge, rest kept deterministic; §13-17) |
| retrieval + abstention e2e | 13/13 (isolation) | **13/13 on current HEAD** after the `tenant_id` / ORG-visibility changes — access-control-before-ranking + private-leak test still green |

None of this changes the gate.

### Making the re-embed cheap (the migration blocker in §5/§6 is affordable)

Whole corpus = **2478 docs / ~518K tokens** (p50 135 tok/doc, p90 540, max ~1275). Both free tiers can do it at **$0** with batching:
- **Gemini** (100 RPM / 30K TPM / 1K RPD × 3 keys): batch 64/req → ~39 requests, far under RPD; TPM floor ~17 min. The `RESOURCE_EXHAUSTED` seen was other processes (ingestion scheduler, tau2, debate panel) draining the shared daily quota — pause `INGESTION_AUTO_ENABLED` + tau2 and run in a clean window → **~20 min, $0**, and Gemini is the measured winner.
- **Voyage free tier** (3 RPM / 10K TPM, 200M free voyage-3 tokens): 518K = 0.26% of the free allowance; batch ~12/req, pace ~20s → **~60–75 min, $0, resumable**.
- `backfill_procedure_embeddings.py --representation` already batches (`_EMBED_BATCH=64`), is resumable, keeps old vectors on failure. Gemini works as-is; Voyage needs `--batch`/`--pace` args (port from `benchmark_embedding_models._embed_all`).
- Note: the corpus model also embeds live queries (1 call/search) — Voyage's 3 RPM would throttle search bursts; Gemini's 100 RPM is fine. Free-tier or not, Gemini is the better operational pick.

## 0. Release gate result

**Retrieval / procedural-memory release gate: NOT PASS.**

**Overall product launch: NOT ASSESSED BY THIS TASK.**

The retrieval representation, versioning, lexical leg, relevance-gate
machinery, human-facing display surfaces, access-control ordering and the
frontend are in good shape and measurably better than before. The gate
does **not** pass because four release-blocking items cannot be closed in
this environment and one architectural gap was newly measured:

1. The **production embedding model is now selected by measured evidence
   — `gemini:gemini-embedding-001`** (best MRR / nDCG@10 / Recall@K /
   F1) — but **acting on it is blocked** on a paid Gemini tier only
   Chaitanya can enable (§4-5 / `CHITANYA-SETUP.md`).
2. The corpus is therefore **not yet embedded in the selected production
   space** — it is still on `local:mxbai-embed-large` (§6).
3. The **relevance threshold** must be re-derived against the full corpus
   in the selected space (§7).
4. **No human label validation** has been done; an independent-model
   cross-check shows only **~77 % same-relevant-class agreement**, so the
   absolute precision numbers are not yet release-grade (§3).
5. **Newly measured:** the semantic relevance gate does **not abstain**
   on "same tool / same objective, incompatible environment or wrong
   direction" queries — it surfaces a topically-relevant but
   constraint-violating procedure as an apparently-good match (§11).

Plus non-blocking-but-named gaps: embedding spend is not recorded in the
`llm_spend` ledger; a provider-policy / data-classification gate now
exists (`provider_policy.py`, added by the launch-compliance lane) but
the retrieval re-embed path does not yet pass it a classification/pool;
procedural-memory outcome telemetry (retrieval → execution, false-reuse)
is not instrumented.

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

## 4. Embedding model comparison  (`embedding-model-benchmark.md`, complete)

Bounded index (448 eval procedures), threshold derived per model by the
documented rule (sweep, max F1 s.t. no-match zero-result ≥ 90 %):

| metric | local mxbai | **gemini emb-001** | voyage-3-large |
|---|---|---|---|
| per-model optimal threshold | 0.691 | 0.683 | 0.540 |
| MRR | 0.623 | **0.634** | 0.616 |
| nDCG@10 | 0.726 | **0.741** | 0.727 |
| Recall@1 / @3 / @5 / @10 | .277/.475/.546/.722 | **.290/.493/.565/.734** | .299/.470/.536/.696 |
| Precision@1 / @3 / @5 / @10 | .579/.404/.326/**.242** | **.597/.421/.330**/.233 | .579/.404/.316/.226 |
| F1 @ own threshold | 0.589 | **0.632** | 0.614 |
| precision / recall @ threshold | **0.762** / 0.481 | 0.621 / **0.643** | 0.598 / 0.631 |
| no-match FP rate | 0.00 | 0.00 | 0.00 |
| embedding failures | 0 | 0 | **16 / 574** (free-tier 3 RPM cap) |

`gemini` leads on MRR, nDCG@10, Recall@3/5/10, Precision@1/3/5 and F1. The
per-model thresholds differ sharply (0.69 / 0.68 / 0.54) — a shared raw
cutoff would be wrong.

## 5. Final embedding model

**SELECTED (measured): `gemini:gemini-embedding-001`** — best on every
ranking metric and on F1-at-own-threshold.

**NOT YET APPLIED.** The corpus is still on `local:mxbai-embed-large`
(2478 / 2478). Acting on the selection is blocked on a **paid Gemini
tier** (free tier `RESOURCE_EXHAUSTED` on all 3 keys). If Chaitanya does
not provision it, the operational fallback is to keep `local` (functional,
private, 0 failures, weaker recall).

## 6. Final threshold

**NOT RE-DERIVED.** Current `RELEVANCE_GATE_MIN_SIMILARITY = 0.6839`
(local corpus). Per-model benchmark shows the optimum for `gemini` is
**0.683** on the bounded eval index — but the production number must be
re-derived against the FULL corpus once it is re-embedded in the selected
space, via `scripts/eval_retrieval_quality.py --measure`.

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
| `disp_v1_deslug_only` (flagged fixtures) | 561 (LLM rework attempted, only 7 passed — §13-17) |
| `disp_v2_llm` (LLM-regenerated, judge-passed) | 7 |

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

**Final counts (job `bwatrdbzi` complete, `display-name-validation.jsonl`,
568 rows — all `is_engineering_fixture=true`):**

| outcome | count |
|---|---|
| affected rows processed | 568 |
| LLM generation attempted (`deepseek-v3.1`) | 545 |
| not generated (pre-filter / no usable content) | 23 |
| generator self-flagged `content insufficient` | 536 |
| **judge verdict PASS → persisted as `disp_v2_llm`** | **7** |
| judge verdict HUMAN_REVIEW → review queue | 561 |

**Outcome: the LLM display-name regeneration did not succeed at scale.**
Only 7 / 568 rows produced a name+description that passed the independent
`gpt-oss-120b` judge; the generator itself flagged 536 as having too
little source content to describe. This is the expected result given §12
/ R6 — these 568 rows are engineering fixtures whose `goal` is a
placeholder and whose steps are stubs. There is no real procedure text
for a model to summarise, and inventing one is disallowed.

**Action taken:** the 7 PASS rows are persisted (`disp_v2_llm`, provenance
in `domain_payload.display_provenance`). The remaining 561 **keep their
deterministic `disp_v1_deslug_only` display metadata** — honest
placeholders, not raw slugs. No LLM output was force-persisted. The
review queue (`display-name-review-queue.jsonl`) is retained for a human
pass if the fixture/product split (R6) is ever resolved.

`canonical_name` is never touched.

## 18. Embedding migration count / failures

Local corpus re-embed: **2478 / 2478**, **0 failures**, one coherent
space (`local:mxbai-embed-large` dim 1024), 0 duplicate active versions,
0 `import_pending_reembed` sentinels. Determinism check: 0 failures over
200 sampled; stored-sha vs recomputed-sha: **2 / 200 drift** (~1 %) — a
`--representation` re-run would resolve; within noise for the release
decision, flagged for cleanup.

## 19. Test results

Backend offline suite (HEAD `e476e96`, `DATABASE_URL` set — the `_e2e`
files run):

| run | passed | failed | skipped |
|---|---|---|---|
| merge baseline `33d4c05` (Cline) | 2227 | 27 | 301 |
| closure baseline `e476e96` (this session, start) | 2270 | 18 | 312 |
| **after closure additions** | **2276** | **19** | **314** |

Δ from closure additions: **+6 passed, +1 failed, +2 skipped.** Run in
isolation the two new abstention tests + the 11 retrieval e2e tests are
**13 / 13 pass** (`b3sdgonr9`: `test_strict_nomatch_returns_zero` ✅,
`test_incompatible_environment_gap_is_measured` ✅ with ceiling 8,
private-procedure leak test ✅, scope-filter ✅, deterministic ✅). The +1
"failed" in the full-suite run is a boundary flake on the gap-guard
(exactly-at-ceiling in that run's DB state; the guard is a "pin current
state, catch regression" check, not a correctness assertion). The 18
already failing at `e476e96` are the pre-existing set below — none
introduced by this workstream.

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
| R4 | embedding spend not recorded in `llm_spend`. **UPDATE:** a provider-policy / data-classification gate now EXISTS — the launch-compliance lane added `app/services/provider_policy.py` + `Embedder._enforce_provider_policy` / `guard_send` (LC-005 / INV-07). It is a no-op unless the caller passes `data_classification` + `policy_pool` to `Embedder`; the retrieval **backfill / re-embed script does not yet pass them**, so the re-embed of private procedures is not currently gated. `local` is never gated (in-boundary). | medium |
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
6. Display-name rework (S3 `bwatrdbzi`) is **done** — 7/568 fixtures
   passed the judge and are persisted; the rest correctly keep
   deterministic metadata. Only revisit if the fixture/product split
   (R6) is resolved and real content is added.

## 23. Egress fix (added 2026-09-09)

Supabase egress hit 7 GB against a 0.382 GB database. Cause: the hot
retrieval paths ran `SELECT * FROM procedures`, shipping the
`VECTOR(1024)` `embedding` column (asyncpg serialises it as ~15 KB of
text per row) plus `retrieval_document`, on rows where neither is read —
`applicability._fetch_candidate_pool` did this for up to 200 rows on
every search.

- **`09f77af`** — new `PROCEDURE_COLS_NO_HEAVY` constant (every
  `procedures` column except `embedding` / `retrieval_document` /
  `retrieval_document_sha256`); 5 query sites in `applicability.py` +
  `procedure_graph_api.py` now project it explicitly. Ranking output
  unchanged (the `<=>` distance runs server-side, returns a float).
  176 retrieval/applicability tests pass; 1 pre-existing unrelated
  failure.
- **`2062aba`** — `conftest.py` promotes `TEST_DATABASE_URL` →
  `DATABASE_URL` for the test session only, so the `*_e2e.py` suite can
  run against a local `pgvector/pgvector:pg15` container instead of the
  production Supabase instance. No per-file changes; app/script runtime
  untouched.

Remaining (recommended, not done): register a pgvector binary codec in
`db/session.py` (float4 binary ≈ 4 KB/row vs 15 KB text) for the paths
that legitimately pull vectors (backfill, benchmark); move CI's live-DB
suite onto a local container.
