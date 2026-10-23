# Proposal: a Goal's contract as linked claims, with a computed Goal status

Status: proposal for the board, 2026-10-23. Not implemented. Spec v4 and `schema.md` are frozen
(CLAUDE.md hard rule 3), so this is a request, not an edit. Based on reading the code and migrations named
below; nothing was run for this document, and no test counts are claimed.

## 1. What we want, in plain words

- The primitive stays **Goal**. No new primitive, and no renaming of existing ones (Goal, Procedure, Step,
  Claim, Evidence, Applicability, Outcome).
- A Goal's **contract is the set of Claims linked to it**: claims that must hold before, and claims that must
  hold after.
- A Goal's **verification status is computed from those claims' evidence**. Nobody types it, and an agent
  saying "done" never sets it.
- Where a machine cannot check a claim, a **human answers**, and the answer is stored as evidence on that
  claim. Human verification is one more method, not a new object.

## 2. What exists today (verified by reading the code)

| piece | where | what it does |
|---|---|---|
| Verification ladder | `backend/db/55_verification_results.sql`, `backend/app/services/verification.py` | One row per (run, criterion). Methods: `self_report`, `artifact_inspection`, `deterministic_check`, `independent_agent`, `human_review`, `real_world_outcome`. States: `claimed_done` < `checked` < `verified` < `independently_verified`, plus `failed_verification` and `inconclusive`. No caller can pass a state: each `record_*` function computes it. A self-report can never reach `verified`. `human_review` needs a reviewer and the reviewed targets (DB CHECK) and stores per-criterion answers. `generate_human_review_packet` builds what the reviewer sees. `evaluate_run_completion` (behind the `verify_completion` MCP tool) only reads results. |
| Evidence gate | `backend/db/30_verified_requires_evidence.sql`, `backend/app/execution/evidence.py` | A trigger on `procedures` refuses the transition into `verified` unless `procedure_evidence_stats.independent_supporting_required >= 1`. Rows sharing an `independence_group` count once. |
| Promotion counters | `backend/app/services/procedures.py::record_execution_outcome` | A candidate becomes `verified` after at least 10 successes, 0 failures, 3 distinct contexts. A self-report is stored as evidence but does not touch the lifecycle. The evidence row is written in the same transaction. |
| Execution chain | `backend/app/execution/stealth_execution_contract.py` | Run created, applicability checked, version pinned, execution, verification, outcome, evidence, finalized. Fails closed. |
| Goal verification field | `backend/db/83_goals.sql` | `goals.expected_outcome` and `goals.verification_requirement`, both free-form JSONB, default `{}`. Copied onto nodes by `goal_resolution.py` and passed to the agent as `goal_check` by `goal_knowledge.py`. **No code evaluates it.** |
| Goal status | `backend/db/83_goals.sql` | `candidate | active | deprecated | merged`. This is the record's lifecycle, not whether the goal is proven. |
| Claims for a goal | `backend/app/mcp_server/resources.py` (`stealth://goals/{goal_id}/claims`) | Found by search on the goal's text (`get_relevant_claims`). In the places read, no stored claim-to-goal link was found. |
| Related queue | `backend/db/74_pending_global_verifications.sql` | A review queue for globally published procedures. It never promotes anything. |

Known weakness of the ladder: criteria come from `procedures.postconditions`, a plain list of statement
strings. Migration 55 recorded that 2 of 2,625 procedures had any. The ladder is mostly unused.

## 3. The gap

1. A Goal has no structured contract, and the free-form `verification_requirement` is not evaluated.
2. Claims are related to a Goal only by search, with no role (before / after) and no stored link.
3. There is no computed Goal status. `goals.status` means something else.
4. No composition: a parent Goal's status does not depend on its child Goals.
5. The ladder is tied to execution runs and procedure postconditions, not to Goals or Claims.
6. Human review exists as a record, but not as typed questions (accept or reject, risk label,
   counterexample, intent correction).

## 4. The proposal

### 4.1 Link claims to goals, with a role (additive)

One new table, `goal_claims`: `goal_id`, `claim_id`, `role` (`assumption` = must hold before, `guarantee` = must
hold after), with the same bi-temporal and scope and provenance columns as `goals` (migration 83's
conventions, V0 gate fields nullable at the DB layer and required in `v0_gate.py`). The claims table name and
primary key are to be confirmed against the live schema before writing the migration. Next free migration
number at the time of writing: 152 (highest existing: 151).

### 4.2 A Goal's status is computed, never written

Status values: `verified`, `open`, `failed`, `contested`, `expired`.

- `verified`: every required `guarantee` claim has enough evidence, and every required child Goal is
  `verified`.
- `open`: evidence is missing or too weak.
- `failed`: a check on a required claim failed (the ladder's `failed_verification`).
- `contested`: evidence disagrees (for example a passing test and a rejecting human).
- `expired`: a dependency, claim, or requirement changed after the evidence was recorded.
- Conservative rule: missing or contradictory evidence means not `verified`, whatever an agent claims.

Computed from a view or a service function over the existing evidence tables (like
`procedure_evidence_stats`). No stored status column, so it cannot drift.

### 4.3 Use the ladder, do not add a second one

Evidence on a claim reuses the six methods and the rule that callers cannot set a state. Migration 55 refused
to add a separate plan entity, and this proposal follows that: the contract lives in claims, the results live
in `verification_results`-style rows. Open question for the board: extend `verification_results` with a
`claim_id`, or add a sibling table. Prefer the former if the `(execution_run_id, criterion_id)` uniqueness can
stay intact.

### 4.4 Human verification where machines cannot decide

- A claim can declare that its method is `human_review`.
- The human sees the claim and the evidence already gathered, and answers one typed question: accept or
  reject, risk label (`none | low | medium | high`), counterexample, or intent correction.
- The answer is stored as `human_review` evidence on that claim: reviewer, reviewed targets, answers,
  timestamp. The existing DB CHECK (reviewer and targets required) stays.
- A human is required when the claim is ambiguous, the blast radius is high (auth, money, privacy,
  irreversible state), or automated checks disagree.

### 4.5 Composition across the Goal tree

`goal_resolution.py` already builds the Goal tree. A parent Goal is `verified` only when its own guarantees and
all required children are `verified`. A change to a child's claims makes the parent `expired` until re-checked.

## 5. What does not change

- Goal remains the only primitive. No `VerificationUnit`.
- Procedure promotion rules, the `procedures` evidence trigger, and the execution contract chain.
- The meaning of `goals.status` (record lifecycle).
- Redaction, scope and provenance rules, and the tenant SQL conventions (`scope_predicates()`).

## 6. Proving tests (same change as the code, CLAUDE.md rule 7)

- A Goal with a required guarantee claim and no evidence is `open`, never `verified`.
- A `self_report` on a claim cannot make the Goal `verified`.
- A `human_review` without reviewer and targets is refused.
- A failed check makes the Goal `failed`; a later contradictory result makes it `contested`.
- A parent Goal stays `open` while any required child is not `verified`.
- Changing a claim after verification marks the Goal `expired`.
- Tenant isolation holds for `goal_claims` (offline fakes, plus the DB-backed test when `DATABASE_URL` is set).
- Gate and writer land together (hard rule 6): no status gate without the code that writes the evidence.

## 7. Open questions

1. Claims table name and primary key for the link (to confirm in the live schema).
2. Extend `verification_results` with `claim_id`, or add a sibling table.
3. How many independent pieces of evidence make a claim "enough", and whether that threshold differs by
   method and by risk label.
4. Where the router's checks (reproduction test, reviewer, accept rule; currently in the client plugin) get
   recorded as `deterministic_check` and `independent_agent` evidence.
5. Whether `goals.verification_requirement` stays as a derived summary or is retired in favor of the claim
   links (retiring it needs a migration under the fresh-start rule: no backfills, no dual reads).

## 8. Suggested order

1. `goal_claims` table and the link service, with the proving tests for scope and provenance.
2. The computed status for a single Goal (no children yet).
3. Human-review evidence on claims, with the typed questions.
4. Composition across the Goal tree.
5. Hook the router's checks into the same evidence path.

## 9. Local first (implemented, 2026-10-23)

The first step shipped on the client, in `packaging/npm/`, as `stealthlab-mcp status`: a computed status for every
`.stealth/run.md` node and `.stealth/library.md` entry, from recorded check runs in a new append-only
`.stealth/evidence.md`. Details, real output and limits: `docs/stealth_status_results.md`.

How it maps onto section 4:

| this proposal | local version |
|---|---|
| Goal with a contract | a `run.md` node or a `library.md` entry |
| `guarantee` claim with a `deterministic_check` | the node's `check=` |
| `assumption` claims | the node's `claims=` references to `claims.md` |
| `verified`, `open`, `failed`, `contested` (4.2) | the same rules, over evidence lines at the current file hashes |
| `expired` (4.2) | a claim the survey validator no longer keeps, a changed `touches=` file, or evidence recorded against files whose hash changed |
| composition (4.5) | `deps=` between nodes |
| the ladder's method names (4.3) | `deterministic_check` and `independent_agent` in the evidence line, so a line maps onto `verification_results` |

Steps 1 to 2 of the order below are unchanged for the server; the local step comes first and its evidence feeds them.

The server step for this local evidence (table choice, what is uploaded and what never leaves the machine, the reviewer, the proving
tests) is `docs/evidence_upload_design.md`; it answers open question 2 of section 7 in favour of a sibling table.
