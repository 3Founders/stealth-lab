# Band 1 Closure Review — VERDICT: CLOSED ✅

Reviewer: integrator ox-alpha · Review commit range: `b43e6c2`…`8fd83e1` (+ `2505705`, ship `e069e50`)
Integrated verification performed by reviewer: **backend suite 981 passed / 106 skipped / 0 failed · packaging suite 30 passed / 0 failed**

Band 1 — Trust-correctness contracts — is closed. Every ROADMAP Band 1 item is
implemented, merged, and pinned by proving tests. Two conditions are carried
forward, both already scheduled (see Deferreds).

## Item scorecard

| Item | Verdict | Landed as | Proving tests |
|---|---|---|---|
| 1. Contract tests before ingestion | ✅ | contract suites grew from 168 → 981 across the wave | every item below |
| 1.2 Provenance parameterized | ✅ | `seed.py` explicit provenance; `system_pending_review` enum value (migration 21); V0 refusal of unattributed writes | V0 provenance tests |
| 1.3 Scope columns everywhere + V0 gate | ✅ | migrations 21–22 (7 tables + CHECKs behind the gate), `v0_gate.py` ten-type vocabulary | scope gate tests, DDL static tests |
| 1.4 Claim shape born correct | ✅ | subject/predicate/object/proposition_type/claim_status/belief columns native (migration 21), status CHECK constraint | proposition/status constraint tests |
| 1.5 UUIDv7 | ✅ | `app/utils/ids.py`, RFC 9562 field/ordering/uniqueness tested | 3 tests |
| 1.6 Embedding stamps | ✅ | model_id/dim on three vector-bearing tables (migration 21) | static DDL test |
| 1.7 ExecutionPlan/TaskGraph `[D→frozen]` | ✅ | migration 23: composite FK `(procedure_id, procedure_version)`, freeze triggers, executions append-only; `plans.py` compile/hash/rebind | 36 tests incl. canonical-hash replay proof |
| 1.8 Extraction correctness bundle | ✅ | precondition relevance filter; V6 authoring validator; bounded z3 off event loop; memoized `project_state()`; tenant-scoped cold-start | +29 tests |
| 1.9a Evidence first-class | ✅ | db/24 append-only `[H]` w/ tombstone retraction; §36 failure_class home; engine ban on bare model-asserted success (inv #13); independence-capped stats views; boundary validate/outcome_to_evidence/assert_verified_requires_evidence (inv #3/#10/#13) | 33 tests |
| 1.9b Capability computation | ✅ | levels-as-banded-P via Wilson **lower bound** (conservative: small samples cannot claim high trust); ratified routing tiers as named config (`ROUTE_AUTO=0.90 / OFFER=0.70`); bidirectional demotion | 25 tests |
| 1.9c Universal ChangeSet coverage | ✅ | db/25 append-only change_sets+operations w/ triggers; `changeset_record.py` boundary failing closed on empty/non-[V]/unknown; wired approve/reject/quarantine-disable | 9 tests |

## Appendix C invariant audit (rows owned or advanced by Band 1)

| Row | Invariant | Evidence |
|---|---|---|
| #1 #2 | execution→plan, plan→procedure version | composite FK at engine level; no nullable plan reference (36 tests) |
| #6 | claim has provenance | V0 gate refuses unattributed inserts |
| #7 | [V] mutation → ChangeSet record | wired lifecycle paths emit; whitelists refuse non-[V] targets (9 tests) |
| #10 | failure reduces capability | demotion trajectory API proven (capability tests) |
| #12 | capability evidence-based not brand-based | Wilson bound over outcome streams only; brand never enters computation |
| #13 | outcomes ≠ self-reports | engine ban `{}` success_criteria + semantic check in `evidence.py` |
| #14 #19 | raw records immutable | append-only triggers on evidence, plans/graphs/executions, change_sets |
| #15 | candidates ≠ accepted | claim_status machine with candidate default |
| #17 | instantiation never modifies source | frozen canonical-hash comparison tests |
| #20 #21 | replayability + extractor versioning | invariant text landed in §39; extractor_version column live (migration 21); full-pipeline replay test remains Band 2.8 (below) |

## Exit criteria verdict (ROADMAP Band 1)

| Criterion | Verdict |
|---|---|
| Zero scope-less writes accepted | ✅ enforced at boundary + engine CHECKs |
| V1–V6 green on every ingested row | ✅ validators green throughout wave |
| Migrations apply on production-shaped copy <1h | ✅ **full chain 01→23 verified on a real engine** (pgvector/pg15, Chaitanya run; one bug found & fixed — proof the discipline works) |
| Plan persistence demonstrated end-to-end | ✅ contract level + hash-replay proof |
| Replay regenerates derived objects deterministically | 🟡 **partial**: plans replay-proven; observations/claims regeneration = Band 2 replayability item (scheduled) |

## Deferreds (tracked, none block closure)

1. **Freeze-trigger probe against live engine** — Chaitanya's bonus finding: his probe INSERTs hit `procedure_row_id NOT NULL` before exercising the freeze trigger path. Fold into the next real-DB session (5 min).
2. **Full-pipeline replay test** — Band 2.8 owns it.
3. **Changeset wiring for supersession/revision paths** — wires in as §20 revision machinery builds; entry point exists (`record_change_set`).
4. **D2/D3/D5 defaults** — standing until founder overrides.

## Process notes

- Two lane sessions died on provider endpoint outages; 1.9c was absorbed by the
  integrator rather than left blocked — protocol worked as designed.
- One cross-lane ownership conflict (change.py) resolved by scoped exception,
  recorded on the board.
- SHIP delivered P1 (`stealthlab-connect`) inside this review window — fastest
  lane completion yet; its own acceptance review follows separately.

## Band 2 is hereby OPEN

Starting order per ROADMAP: segmenter promotion (FINDINGS.md rules are waiting),
ClaimFamily resolver v0, TMS-readability fix (in flight on sl-core-a),
end-to-end replayability, OIDC identity gate. MEASURE's micro-experiment pack
runs in parallel and feeds Band 3.
