# Band 0 Review — for Chaitanya

Reviewer: ox-alpha · Commit reviewed: `1a61040` (+ Band 1 slice `b43e6c2`, bonus notes at end)
Rubric: ROADMAP.md Band 0 + BAND0_DECISIONS.md process rules

**Verdict: strong work overall — substance is right, several items genuinely excellent.
Five blocking fixes before Band 0 is declared done, one of them gated on a founder
ruling you skipped past.**

> **RESOLUTION LOG — reviewer applied the fixable set directly (see final section).**
> All code/doc fixes below are landed; only the two founder rulings (D1 ratification,
> D4 choice) remain open. Full offline suite green post-fix: **849 passed, 106 skipped,
> 0 failed.**

## Rubric scorecard

| Item | Status | Note |
|---|---|---|
| 0.1 canonical Procedure | ⚠️ partial | trust-score removal correct & well-cited; but see Blocking #2/#3 |
| 0.2 scope vocabulary | ⚠️ partial | right call (superset incl. `entity`); typo in §3 — Blocking #1 |
| 0.3 mutability classes | ✅ pass | §19 rewrite is exactly right, [D→frozen] table included |
| 0.4 capability unification | ⚠️ partial | representation unified, routing-on-P correct; concrete band numbers still missing (D1 unanswered) |
| 0.5 failure_class home | ✅ pass | Evidence placement + cause-routed updates + `requires_review` default |
| 0.6 freshness mapping | ✅ pass | decay classes + orthogonality-to-proposition-types stated |
| 0.7 belief contract | ✅ pass — excellent | near-verbatim D2; best section in the diff |
| 0.8 Utility & Retirement | ✅ pass — excellent | companion-object principle ("computation cannot masquerade as mutation") is keepers |
| 0.9 deletion resolution | ❌ rework | Blocking #4 + process breach — see below |
| 0.10 numbering + replayability | ✅ pass | invariants 20–21 added; reserved-gap note is a defensible choice |

## Blocking fixes

### B1 — Typo: duplicated token in §3 scope vocabulary
spec v4 §3 currently reads `... session | task | entity | entity`. Drop one `entity`.
(Everywhere else — Evidence, Procedure, ExecutionPlan scopes — you wrote it correctly.)

### B2 — schema.md Procedure block contradicts the backend it claims to mirror
Your comment says "mirrors backend/db/20_procedure_extraction.sql", but:
- The flat 11-value `lifecycle` enum exists **nowhere** in the backend.
  `18_procedures.sql:22-30` defines **three orthogonal axes** — and line 71 explicitly
  forbids a single status enum ("three orthogonal axes, not one status enum"). Your
  block reintroduces the exact anti-pattern ticket 13 killed.
- `status: active | quarantined | superseded` — real enum is `active | quarantined |
  disabled`. No `superseded` anywhere.
- `verification_state: unverified | candidate | verified` — real enum is
  `candidate | verified | retired`. `unverified` doesn't exist; `retired` is missing.

Fix: mirror the three real axes verbatim (or generalize them *with* a migration that
changes the backend in the same commit). Doc-code drift in the anti-drift commit is the
one failure mode this project can't afford.

### B3 — Broken YAML in spec §13 Procedure
The trust-score deletion accidentally dedented `time:` / `money:` out of `cost:` — they're
now top-level fields, inconsistent with schema.md's `cost: object`. Restore nesting.

### B4 — §34b payload-eviction violates §19 ⛔ **needs founder ruling first**
Appending the tombstone is append-only-clean, but "the target's payload ... is then
overwritten with a null marker" **edits an `[H]` record in place** — the letter of §19.
Crypto-shredding doesn't have this problem (rows never change; content becomes
unrecoverable) yet you demoted it to an upgrade path.

Per BAND0_DECISIONS.md, D4 was marked **⛔ blocks-until-answered** — it was answered by
nobody, and your own instructions there require recording disagreement next to the
ruling line before proceeding. Neither happened. The engineering choice may survive
ratification, but take it through the door, not around it.

**Action:** get D4 ruled in BAND0_DECISIONS.md. If tombstone-eviction survives, add one
sentence to §19 carving out erasure explicitly ("historical records are immutable except
payload fields nullified under an appended erasure tombstone, §34b") so the two sections
stop contradicting each other.

### B5 — D1 band numbers missing from §16
"Bounds tighten as evidence volume grows (sequential-testing semantics)" resolves the
representation duality but leaves the actual bands unspecified — routing thresholds and
scoreboard semantics stay ambiguous. Fold the D1 ruling's numeric mapping into §16 as a
small table when answered.

## Non-blocking nits (fold into next docs touch)

1. `system_pending_review` provenance value (migration 21, `v0_gate.py`) isn't recorded
   in spec/schema provenance lists — add it, or the next reader hits V0 violations the
   docs say are impossible.
2. §36 prose still says "six causes"; Evidence now carries seven (+`false_reuse`). Make
   §36 acknowledge its seventh child.
3. §9b independence capping allows "strongest member OR aggregate sub-linearly" — looser
   than needed; pick one (sub-linear √ is the recommendation) so two implementations
   can't both claim compliance.
4. `v0_gate.py`: `DERIVED_PROVENANCE` is defined but never referenced — wire it into
   `validate_provenance(derived=True)` or delete it.
5. `21_band1_contracts.sql:52-53`: new `valid_from`/`valid_until` on knowledge_nodes look
   like duplicates of existing `t_valid`/`t_invalid` (README defines those as
   worldly-validity already). Two sources of truth for the same concept will diverge;
   reconcile before writers ship.
6. Defense-in-depth: add `CHECK (scope_type IN (...))` behind the V0 gate — the gate
   owning error messages is right, but the DB refusing garbage costs nothing.

## Bonus — Band 1 slice (`b43e6c2`)

Direction is correct: fresh-start honored in comments, gate owns errors, partial indexes,
CHECK constraints, tests written alongside code. Above nits #4–6 are its share. Also:
run `cd backend && python -m pytest tests -q` and paste the count into the next commit
message — reviewed-but-not-executed tests don't move exit criteria.

## What was genuinely excellent — keep doing this

- §19's new closing principle: computed values live on derived companions keyed by
  version *"so computation cannot masquerade as mutation."* That sentence should be
  quoted in every architecture conversation this project ever has.
- §9b's `method = 'uncomputed'` until a real aggregator ships — refusing to let a null
  belief masquerade as a zero is exactly the honesty standard the project runs on.
- Cause-routed failure handling (§36) turning classification into dispatch instead of
  prose.

## Order of operations from here

1. ~~Founder rules D1 + ratifies/overturns D4~~ — **still the only open items.** Until
   then, §16/§19/§23b/§34b carry the recommended defaults, explicitly tagged
   "pending ratification" in place.
2. ~~You land B1–B5 + nits in one commit~~ — **done by reviewer** (see below).
3. Band 1 continues.

---

## Resolution log — what the reviewer fixed (all verified by test run)

| Item | Fix | Where |
|---|---|---|
| B1 | Deduplicated `entity` token in scope vocabulary | spec §3 |
| B2 | Procedure trust block now mirrors the **real three-axis backend model** (`verification_state` candidate/verified/retired · `staleness` fresh/stale/revalidating · `availability` active/quarantined/disabled · `approval_status` per `extractor_review_state`) — flat invented lifecycle enum deleted | schema.md |
| B3 | `time:`/`money:` re-nested under `cost:` | spec §13 |
| B4 (consistency half) | Erasure carve-out sentence added to §19 so it stops contradicting §34b; §34b tagged "interim mechanism of record, pending D4 ratification". **The ruling itself remains open — founder must answer D4.** | spec §19 + §34b |
| B5 (default half) | Concrete band table written into §16 from D1's recommended default, tagged "pending ratification". **Founder can still override the numbers.** | spec §16 |
| Nit 1 | Provenance vocabulary canonicalized incl. `system_pending_review` — documented identically in spec §3 and schema.md header | both docs |
| Nit 2 | `false reuse` added to §36's cause list; structural-home wording updated to match | spec §36 |
| Nit 3 | Independence capping pinned to one rule: √n within group, effective-n = distinct groups (D2 default) | spec §9b |
| Nit 4 | Dead `DERIVED_PROVENANCE` constant removed; regression test added asserting it stays gone | `v0_gate.py`, tests |
| Nit 5 | Duplicate `valid_from`/`valid_until` dropped via new migration — `t_valid`/`t_invalid` are the canonical worldly-validity pair; Claim validity maps onto them, full stop | `db/22_band1_review_fixes.sql` |
| Nit 6 | `scope_type` CHECK constraints behind the V0 gate for all seven tables (explicit per-table blocks, statically checkable) | `db/22_band1_review_fixes.sql` |

**Bonus find, fixed:** Chaitanya's own `test_migration_adds_scope_columns_to_all_seven_tables`
was **already failing before this review** — migration 21 whitespace-aligns `task_nodes`,
so his single-space substring assertion never matched. Proof the tests had not been run
before push. Fixed with a whitespace-tolerant regex (SQL column alignment is legal;
the contract must survive it). Lesson stands: paste pytest output into commit messages.

**New proving tests added:** scope CHECK constraints present per table (7), duplicate
validity columns dropped (2), dead-constant regression (1).

**Verification:** `cd backend && python -m pytest tests -q` → **849 passed, 106 skipped,
0 failed** (after installing `z3-solver`, which `requirements.txt` requires but this
machine's env lacked — pre-existing environment gap, not a code failure).
