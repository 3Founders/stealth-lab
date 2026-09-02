# Corpus wave — final report

Written 2026-09-02, branch `core-a/ingestion-testing`. All numbers below are grounded in
an actual run against live Supabase (`wckeklqxmiglivfolujn`, ap-south-1) or in the wave's
own committed artifacts under `.scratch/corpus_wave/`. Nothing here is estimated.

Backing artifacts:
`source_registry.jsonl` · `ranking.json` · `admitted.json` · `rejected.json` ·
`ingest_run.py` + `ingest_result.json` (Phase 9) ·
`retrieval_eval.py` + `retrieval_results.json` (Phase 10 deliverable 1) ·
`_claim_probe.py` + `claim_retrieval_check.md` (Phase 10 deliverable 2).

---

## 1. Source coverage

| | count |
|---|---|
| sources attempted | **50** |
| resolved (fetched + provenance row written) | **49** |
| failed / dead | **1** — S44 Papers-with-Code (`ARTIFACT_MOVED`: 302 → huggingface.co/papers; discontinued). Provenance row retained, no procedure or claim, no substitute invented. |

All 50 URLs are retained in `sources/source_registry.jsonl` regardless of admission
outcome (wave §18).

## 2. Extraction

| unit | authored this wave | ingested to Supabase (Phase 9) |
|---|---|---|
| procedure candidates (`candidates/Sxx.md`, 10-section) | 48 full + 1 claims-source (S20) + 1 dead stub (S44) | **28** procedures (`provenance='prior_library'`, `verification_state='candidate'`) |
| task nodes | task-candidate lists in every candidate file | **170** `task_nodes` (`created_by='corpus_wave'`) |
| claims | ~40 assertions across candidate files | **15** `knowledge_nodes` (`node_type='claim'`, `created_by='corpus_wave'`), all embedded, all anchored to one `document` episode via `episode_links` |
| implementations | **0** | **0** |

**Why implementations = 0 this wave.** The corpus sources describe procedures and, in
many cases, real CLIs (`save-the-token`, `agent-browser`, `gh aw`, OpenAlex/S2 REST), but
this wave deliberately did not author `implementations` / `implementation_tasks` rows:
no locator/invocation split, no harness descriptor, no sandbox provider wiring. Registering
runnable Implementations and binding them to tasks is the production execution architecture's
job (audit §"implementation / provider / execution state"), not the ingestion-proof wave's.

## 3. Validation

| state | count |
|---|---|
| ingested as `verification_state='candidate'` | **28 / 28** |
| `verified` | **0** |

0 verified is correct and expected: `verified` requires real execution evidence
(`backend/app/execution/evidence.py` → a `success` with an explicit predicate/metrics),
and no candidate was executed this wave (see §5). The 15 claims are `epistemic_status`
`observed` (EXPERIMENTAL, 8 claims) or `inferred` (SOURCE_DERIVED, 7 claims); none carry
verification evidence either.

## 4. Retrieval

Probe: `.scratch/corpus_wave/retrieval_eval.py` → `retrieval_results.json`.
Real callable: `app.services.domain_search.search_global(object_types=["procedure"],
filters={"require_verified": False})`, which delegates to
`app.services.applicability.find_applicable_procedures` — the **same** hard-constraint
cascade + similarity/capability RRF fusion that `/v1/procedures/search` and the MCP
`search_procedures` tool use, reused verbatim. Query embeddings via the real
`app.services.embeddings.Embedder` (Gemini/Voyage chain), `input_type='query'`.
Scope: `AccessScope.unrestricted()`. 28 admitted procedures, 1 primary NL query + 1
paraphrase each (56 queries total).

`require_verified=False` because all 28 corpus procedures are candidates — this is
`search_global`'s documented browse-mode default; every other hard constraint (temporal
validity, staleness, availability, scope, preconditions, invariants) still applied and
none of the 28 were disqualified.

| metric | value |
|---|---|
| **procedure recall@5** | **1.000** (28/28 — every target procedure in the top 5 for its primary query) |
| **procedure recall@10** | **1.000** (28/28) |
| **mean reciprocal rank (MRR)** | **0.7565** |
| targets ranked #1 | 19 / 28 |
| worst primary rank | 5 (S47 k8s-debug, S07 discover-stack-TDD, S03 Vercel-deploy) |
| paraphrase recall@5 | 0.929 (26/28) |
| paraphrase recall@10 | 1.000 (28/28) |
| paraphrase MRR | 0.7411 |
| paraphrase stability — both primary & paraphrase in top 5 | 26/28 (0.929) |
| paraphrase stability — both in top 10 | 28/28 (1.000) |
| paraphrase mean \|rank delta\| | 0.46 (median 0) |

The 2 paraphrases that slipped to rank 6: **S07** (test-first "discover the repo's own
framework first") and **S47** (kubectl pod debugging). Both lose top-5 to a close sibling
under generic wording — S07 vs S09 (the other TDD procedure), S47 vs generic
"troubleshoot / debug" phrasing that also pulls S34/S11. This is the applicability
cascade + RRF behaving correctly on genuinely near-duplicate intents, not a retrieval
failure — recall@10 is still perfect and the median rank shift across all 28 pairs is 0.

**Solution-search / cross-type ordering: not exercised — by design.**
`search_global` returns results **grouped by `object_type`** (`{"procedure": [...],
"task": [...]}`), each bucket ordered by its own mechanism (procedure = applicability
cascade + similarity/capability RRF; task/claim = `HybridRetriever` vector+lexical RRF).
There is no combined ranked list in which a Task could "outrank" a Procedure — the
CLAUDE.md hard rule ("retrieval fuses by RRF; applicability is a non-compensatory
cascade… keep them apart") is enforced by keeping the buckets separate, and
`domain_search.py` explicitly refuses to invent a cross-type comparison formula. The
probe confirmed the response shape: `object_types=["procedure","task"]` on one query
returned 5 procedures and 5 tasks in **separate** buckets (`grouped_by_object_type:
true`, `cross_type_single_list: false`). Cross-type ordering is preserved (grouped),
not tested for a "task beats procedure when sufficient" ranking — that ranking does not
exist on this path.

## 5. Execution

**0 procedures executed.** Explicitly deferred to the production execution architecture
per wave §4. No `executions`, no `execution_plans`, no `evidence` rows were written for
any corpus procedure. Consequently all 28 stay `candidate` (see §3).

## 6. Capability

- **Strongest candidate:** **S36** — "construct + verify a task instance" (SWE-bench).
  Composite 4.56 (top of the set); `evidence_quality=5`, `reproducibility=5`,
  `task_reusability=5`. It is the canonical objective pass/fail model
  (FAIL_TO_PASS flips green, PASS_TO_PASS stays green, in a pinned env) and its derived
  claim `be5d06b5…` is the substrate's own verification predicate restated.
- **Most reusable task:** the **S36** verification task
  ("a candidate patch passes iff it flips FAIL_TO_PASS while keeping PASS_TO_PASS green")
  and the **S38** trajectory-record schema (role-tagged conversation + final unified-diff
  `model_patch` + `exit_status` + `resolved` + generated-test signals) — both scored
  `task_reusability=5` and both map directly onto this repo's own replay/evidence model.
- **Largest claimed efficiency delta:** **S24** (ch040602/Save-The-Token) —
  **~69.3% weighted-average context reduction with 100% sufficiency retention on 5
  eligible cases**. Attribution: the repo author's own benchmark, `docs/benchmark.md`,
  captured as claim `36c151f0…` with `epistemic_origin=EXPERIMENTAL`,
  `epistemic_status=observed`. Runner-up: S23 (token-optimizer-mcp `smart_read`
  diff-on-reread) — larger notional savings but `evidence_quality=2`, no controlled
  benchmark, kept USER_REPORTED.

## 7. Research

- **Most surprising technique / finding:** **S20** — *"Models perform better on shuffled
  haystacks than on logically-structured ones."* Counter to the intuition that coherent
  context helps; captured as claim `7264f476…` (EXPERIMENTAL, 18 models).
- **Most useful claim:** **S20** — *"Focused (~300 tok) prompts beat full (~113k tok)
  prompts across all models on LongMemEval"* (`5d9413d7…`). Directly actionable for
  context engineering and it is the empirical backing for S24 / S21 / S26 (trim context
  to task-relevant).
- **Most reusable task:** **S38** trajectory-record schema — a replayable + gradable
  record shape the substrate can adopt for its own executions.
- **Most promising future experiment:** re-run the **S19** Context-Rot benchmark harness
  against *the substrate's own retrieval* — does verified-procedure recall degrade with
  input length the way raw LLM recall does? Pairs naturally with a three-arm sweep
  (solo / ordinary memory / verified substrate) using the **S36** FAIL_TO_PASS /
  PASS_TO_PASS harness as the scorer.

## 8. Product

`featured/` is empty; picks below are taken from `ranking.json` top composites +
`retrieval_results.json` rank-1 behaviour.

- **Best 4–6 featured procedures** (high composite, clean extraction, retrieved at
  rank 1 for both primary and paraphrase):
  1. **S36** — turn a merged PR into a verifiable pass/fail task (4.56)
  2. **S02** — eval-driven skill authoring loop (4.36)
  3. **S46** — OWASP authentication hardening (4.12)
  4. **S24** — trim MCP tool context to a budget + sufficiency check (4.10)
  5. **S12** — author an AGENTS.md at the repo root (3.99, near-universal applicability)
  6. **S09** — strict RED-GREEN-REFACTOR TDD loop (3.94)
- **Best visual procedure:** **S27** — frontend-slides: generate one dependency-free
  single-file HTML deck (inline CSS/JS), shareable by URL or PDF. Wave's flagship visual
  proving case; retrieved at rank 1 primary + paraphrase.
- **Best efficiency procedure:** **S24** — Save-The-Token (~69% context reduction, 100%
  sufficiency; see §6).
- **Best one-click procedure:** **S03** — deploy a project to Vercel as a claimable
  deployment (tarball → framework-detect → upload → preview URL + claim URL),
  `execution_readiness=5`. Alternates: **S41** / **S42** (a single authenticated GET
  against OpenAlex / Semantic Scholar), **S30** (`agent-browser` one-command page drive).

## 9. Rejected / negative evidence (these are useful results)

- **License-blocked 6** — S10 (Ralph blog, no reuse license), S18 (vercel-labs/skills,
  LICENSE not stated), S26 (knowledge-base, "synthesized from YouTube", no license),
  S29 (codebase-to-course, no LICENSE file), S37 (SWE-Gym, LICENSE unverified),
  S49 (AutomationBench, LICENSE unverified). Source rows retained in provenance;
  **no procedure, claim, or quoted text ingested.** Notably S18 (3.70), S37 (3.73) and
  S26 (3.65) scored at ADMITTED level on merit and were held *purely* on licensing —
  evidence that the license gate is load-bearing, not decorative.
- **S13 inconclusive** — AGENTS.md 8-section starter. Step sequence was *reconstructed
  from a structure table*, not source-quoted; held for re-fetch. The extraction-provenance
  discipline (wave rule 2) caught a non-verbatim extraction before it entered canon.
- **S44 dead** — Papers-with-Code discontinued. Provenance-only, no substitute invented
  (wave rule against fabricating a stand-in).
- **S22 weak evidence** — claude-code-token-optimization "output sandboxing". Composite
  2.89, `evidence_quality=1`; the repo self-declares its numbers are "not controlled
  benchmarks". **Not ingested** (below the 3.6 admission bar). The 40–80% figure would
  have stayed USER_REPORTED even if it had been. A clean example of the bar rejecting a
  plausible-sounding procedure whose only support is uncontrolled self-report.

## 10. Carry-forward for the production hardening wave

1. **Execution evidence.** All 28 candidates are unverified. Run the deterministic ones
   through the real execution architecture (S24, S33, S41, S42, S03, S30, S36) to produce
   `evidence` rows and move `candidate → verified`. This is the blocker on every
   downstream "best verified way" claim.
2. **Implementation Registry.** 0 rows. Author locator/invocation/harness descriptors for
   the CLI/REST-backed procedures (S24 `save-the-token`, S30 `agent-browser`, S15
   `gh aw`, S41/S42 REST) and bind them to their `task_nodes`.
3. **Typed claim relations.** The claim graph has **0 `relation` edges** — connectivity
   is entirely computed k-NN similarity (30 edges). A synthesis pass should assert the
   SUPPORTS edges the candidate files already document (S20 "focused beats full" →
   S24/S21/S26; S20 "degrades with length" → S23/S10).
4. **License re-check.** Confirm SPDX for S18, S37, S49 and admit if clear; S10/S26/S29
   stay blocked (no license exists to clear).
5. **S13** — re-fetch the real `AGENTS.md` body and re-extract verbatim, or drop it.
   **S44** — leave dead.
6. **Cross-type ranking.** If the product needs a single "best answer regardless of
   object type" list, that comparison formula must be designed — `search_global`
   deliberately does not have one, and inventing it ad hoc violates the CLAUDE.md
   cascade/RRF separation rule.
7. **DB role.** Real tenant isolation on the hosted box needs a non-owner app role
   (the current `postgres` owner is exempt from FORCE ROW LEVEL SECURITY) — see
   `supabase_readiness.md`.
