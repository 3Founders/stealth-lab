# Autonomous run — reconstructed state (spec §2)

_Written at the start of the 60-source MCP-evaluation master run. Grounded in the
actual repo + a live Supabase read, not in the prompt's summary._

## Git

| | |
|---|---|
| Working branch (this run) | `feat/corpus-mcp-eval`, cut from `origin/main` |
| Starting SHA | `3d95baf` (= `origin/main` at run start) |
| Historical baseline | `a5dace6` (`v1-baseline-2026-09-02`) — NOT reset to |
| Latest release tag | `v1-final-2026-09-03.2` → `1d491c1` |
| `origin/core-a/ingestion-testing` | `91e8f63` (`v1-final-2026-09-03.1`) — **has not advanced**; the prompt's "current HEAD `7968efe`" for that branch is not on the remote (local-only on the peer's machine, or stale) |

`diff a5dace6..HEAD` = 218 files / ~38k insertions — the whole V1 line.

Recent `main` history (newest first): `3d95baf` seed demo procedures+impls (finding D) ·
`b135444` dangling-pin repair (A) · `46ce2ef` MCP accepts row key (C) · `5dc9845`
migration 38 `candidates.no_action_justified` (B) · `1f234e0` `/procedure-graph` viewer ·
`1d491c1` (tag `.2`) · `f711ca1`/`45caee5`/`1a6e9d2` Final-V1 eval Bug #7/#8 ·
`b8e133e`/`ce8700c` frontend (concurrent session).

## Production features present

- **Claim graph** — `claim_graph_api.get_claim_graph_overview` (relation + pgvector
  similarity edges), `/claim-graph` + `/claim-graph/data` MCP routes, `claim_graph_page.py`.
  The `KeyError: 'relation'` issue the prompt (§4) names is **already fixed**:
  `test_claim_graph_overview_e2e.py:97-112` selects `kind == 'relation'` before reading
  `relation`; similarity edges carry only `weight` (`final-v1.md §6`, commit `bbf6ff0`).
- **Procedure/task graph** — `procedure_task_graph_api.get_procedure_task_overview`,
  `/procedure-graph` + `/procedure-graph/data` (this session's earlier work, on `main`).
- **Ingestion** — `app/services/skill_ingestion.py` (`ingest_skill_md`: parse → dedup →
  embed → `capture_procedure` provenance=`prior_library`), `ingestion_sources/*`
  (SKILL.md / AGENTS.md / CLAUDE.md / CI / runbook adapters), `app/services/ingestion_jobs.py`,
  `scripts/run_ingestion.py` (traces → observations). No MCP tool wraps bulk ingestion.
- **Procedure extraction** — `app/services/procedure_extraction/*` (grounded_hybrid_v1),
  `capture_procedure`, `derive.precondition_with_claim`.
- **Claims** — `knowledge_nodes` node_type=claim, `capture_claim` (embeds), `relate_claims`
  (SUPERSEDES/CONTRADICTS + `propagate_claim_change` → `mark_procedure_stale`), `link_claims`.
- **Retrieval** — `applicability.find_applicable_procedures` (pgvector + hard constraints),
  `retrieve_precedent` MCP tool, `domain_search` / `solution_search`.
- **MCP** — 29 `@server.tool()` (retrieve_precedent, find_best_way, reproduce_procedure,
  search_procedures, get_procedure, check_procedure, check_applicability, report_execution,
  submit_procedure, decide_procedure, decompose_task/decide_decomposition, propose_synthesis/
  submit_approval, resolve_implementation + 3 registry reads, find_problem/inspect_problem/
  list_problem_solutions/compare_solutions/inspect_evaluation/find_best_solution,
  inspect_run/resume_execution_run/retry_run_node, detect_conflict_trigger, get_claim_graph).
  Streamable-HTTP on :8765, bearer-token gated; custom routes unauthenticated loopback.
- **Execution** — `app/execution/` durable_run / durable_graph / implementation_registry /
  implementation_executor; providers: `deterministic` (SubprocessSandboxExecutor — note:
  `network_access=False` is NOT enforced), `tool`. `find_best_way` tier-2 + `reproduce_procedure`
  route through `run_graph_durably`.
- **Implementation providers** — registry (`register`/`activate`/`verify`), `implementation_tasks`
  link, descriptor projection (`impl-descriptor/1`). This session seeded 2 real impls
  (`pg-migrate-runner` deterministic verified, `release-cutter` tool) — finding D.
- **Product model** — Problem / Benchmark / Solution / Evaluation
  (`app/services/product_model.py`, `db/35_product_model.sql`, `/v1/problems*` REST, 6 MCP
  tools). Leaderboard eligibility now excludes stale-backed solutions (Bug #7);
  benchmark/eval reads inherit the Problem's scope (Bug #8).
- **Evaluation** — `complete_evaluation` recomputes metrics from real execution lineage;
  `evaluations_comparable`; Wilson-lower banding.
- **DB** — Supabase Postgres `wckeklqxmiglivfolujn` (ap-south-1), session pooler, via
  `backend/.env` `DATABASE_URL`. 38 migrations applied (`38_candidates_no_action_justified.sql`
  added this session). No local Postgres in this sandbox; `test_migration_upgrade_e2e`
  needs a throwaway PG17 cluster it cannot get here (CI runs it).

## Existing `.scratch/` corpus work — VERIFIED, and it is a DIFFERENT source set

The prompt (§5) says "the current branch already contains a corpus wave ... 50-source
registry, 29 admitted" and to preserve it. Verified:

- **`.scratch/corpus_wave/`** (peer session, dated 2026-09-02): a 50-source set **S01–S50**
  = agent skills, AGENTS.md, TDD/superpowers, SWE-bench, `github/gh-aw`, HTML-slide skills,
  OpenAlex/S2. 49 resolved / 1 dead / 29 admitted / 6 license-blocked. **Phase 9 ran against
  live Supabase**: 28 procedures (`prior_library`, `verification_state=candidate`), 170
  `task_nodes` (`created_by=corpus_wave`), 15 embedded claims anchored to one document
  episode. **0 implementations, 0 verified** — the wave's own `final_report.md` states this
  was deliberate: "Registering runnable Implementations ... is the production execution
  architecture's job, not the ingestion-proof wave's."
- **`.scratch/better_ways_corpus/phase1/`**: an 18-artifact ("S01–S18") narrower gate
  (source resolution + one-procedure extraction only, nothing written to canonical tables).

**Neither is the 60-seed set in this prompt.** The 60 seeds here — Gortex, TokenPilot,
Hermes Agent, CIL, CodeGraph MCP, RouteLLM, Ralph Loop, InfiAgent, arXiv:26xx.xxxxx,
Octocode, code-graph-rag, awesome-claude-code-toolkit, etc. — are **net-new** and mostly
un-resolved and un-ingested. The families (context / tool / repo / deterministic /
long-running / verification / routing / parallelism / recovery) do not map 1:1 onto the
prior waves.

## Known failures / incomplete phases

- `test_ingestion_admin_endpoint_e2e` — documented pre-existing red (`final-v1.md` KNOWN
  LIMITATIONS #4): trace→observation→claim drain returns cleanly with no claim when the
  observation has no resolvable task/episode anchor. Not on the corpus path.
- Broad `-k e2e` sweep on the long-lived shared Supabase: ~7 residue/ordering flakes
  (`domain_search`, `solution_search` incl. a latent `coroutine raised StopIteration`,
  `procedure_extraction ...superseded`, `state_delta`), reproduce against clean `91e8f63` —
  pre-existing, not on the corpus path.
- Implementation Registry / verified-reuse: was empty until this session's finding-D seed
  (2 impls, 3 procedures, 2 verified). Real coverage still thin.
- `SubprocessSandboxExecutor` does not actually enforce `network_access=False`
  (module docstring; matters for any real code execution in this run).

## Decisions taken for this run (documented per §0)

1. **Work on `feat/corpus-mcp-eval` off `origin/main`**, not on `core-a/ingestion-testing`.
   That branch is the peer session's active ingestion lane and its remote tip (`91e8f63`)
   is behind `main`; building a parallel 60-source wave on it would collide. `main` already
   contains the prior `.scratch/corpus_wave/` artifacts, so nothing is lost.
2. **Push to `main` at checkpoints** (the user's explicit instruction), as fast-forward,
   non-force, re-fetching first — the same discipline used all session around the
   concurrent peer + frontend sessions. This overrides the pasted spec's §97 "do not push
   to main".
3. **Scope of this session**: source resolution for all 60 seeds + the MCP evaluation
   harness (real code + tests) + claim-graph verification + representative end-to-end runs
   through the real MCP surface + a composite-experiment scaffold + an honest final report.
   A full 60 × (ingest → implementation → real MCP-driven coding-agent execution → repeated
   runs → deterministic + independent-LLM verification → evaluation → admission) program is
   multi-week work (the prior wave took a full session for 50 sources at a *shallower*
   depth with no execution); seeds not carried to execution in this session are marked
   `INSUFFICIENT_EVIDENCE` with the reason recorded, never fabricated (§77, §82, §102).
