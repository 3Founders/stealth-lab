# Technology transfer through verified procedures, and decomposition as the targeting step

Written 2026-10-15. Plan and design note, not a landing: no product code changed. Reads of the repo are cited as file:line.
"APR" is read here as automated program repair; the claim it carries is that repair works when the edit is aimed at the right
place (file, symbol, failing test) and does not when the model is left to roam the repository.

## 1. The claim, stated so it can fail

A procedure that was verified in one organisation can be reused in another, at a lower cost than a fresh solve, with no loss of
quality, once it has been (a) abstracted from the first organisation's names, (b) matched to the second organisation's repository
by its preconditions, and (c) re-verified there before it is trusted. The transfer unit is a *targeted repair pattern*, not a
prompt: "this failure signature, in this kind of symbol, is fixed by this change shape, and this check proves it."

Not yet shown: that procedures transfer across organisations at all. Everything measured so far (153 paired tasks, MultiPL-E) is
within one benchmark. The quality of the formed procedures has not been audited in this pass; "really amazing" is a hypothesis
to test with the procedure-audit in section 5, not a finding.

## 2. Why decomposition is the targeting step

What exists:
- The dispatch brief already produces `files_hinted` and `parts[]` and a `size` (`packaging/npm/lib/brief.mjs:33-51`); a part per
  separate ask, a file list per task. Large tasks are refused (`brief.mjs:10`).
- Related tests run before the full check, selected from the changed files (`packaging/npm/lib/exec/related.mjs:1-13`).
- The backend has a generative decomposition service with a validity boundary (acyclic, no dangling refs, new-nodes-only),
  `.scratch/decomposition_unbounded_design.md`, and promotion of an approved decomposition into a reusable agent
  (`backend/app/services/agent_promotion.py:1-16`).
- Dispatch does not call the backend decomposition today (no `decompos` match under `packaging/npm`).

What the Cursor hook test showed (`communications.md`, "Cursor test 2026-10-14", step 2): "make it reject strings" changed three
functions and rewrote tests. That is the missing targeting step in one observation: "it" was resolved to a module, not a symbol.

Proposed shape, smallest first:
1. **Target, not only files.** Extend the brief's output from `files_hinted` to `targets[]`: `{file, symbol?, test?}`. Resolve the
   symbol deterministically where possible (grep and AST for the name in the message or the failing test's imports) and fall back
   to one cheap call. A target is a hypothesis; the check is still the judge.
2. **One part, one target, one check.** `parts[]` become the decomposition: each part carries its target and its own check
   (the test-first writer already writes one test per request; make it one per part). A multi-part task is solved and verified
   part by part, so a failure is localised to a part instead of failing the whole task.
3. **Scope guard on the diff.** After an attempt, compare the changed files and symbols with the targets. Out-of-target edits are
   recorded as `scope_spill` (always) and rejected when the check does not need them. Spill is a measured quantity before it is a
   rule; do not turn it into a blocking rule until the spill rate and its relation to wrong answers are known.
4. **Procedures keyed by target signature.** A procedure's applicability should be stated over the target (language, framework and
   version, failure class, symbol kind, test runner), not over task wording. This is the same shape the applicability cascade
   already enforces (a violated precondition is a disqualification: `backend/app/services/applicability.py`).

Honest limits: size gating means we only handle small and medium tasks, so decomposition helps most on "medium" tasks that are
currently refused or fail. Whether parts-with-checks lifts the solve rate on those has not been measured.

## 3. What else technology transfer requires

The backend already carries part of this; the rest is missing. "Exists" is from the file named, "missing" is from absence in grep.

| Need | State |
|---|---|
| Scope on every row, publish as a deliberate act, withdrawal | Exists: `publication_records` with review and withdrawal state; export and tombstone deletion in `backend/app/services/data_rights.py:1-33`; a published procedure becomes a fresh Global Candidate with its own provenance |
| Promotion needs its own review, not a bypass | Exists for decompositions (`agent_promotion.py:11-15`) |
| Redaction of free text before storage | Exists (`trace_redaction.py::redact_event`); not the same as abstraction |
| **Abstraction**: replace names, paths, internal APIs, constants with typed slots so a procedure is not a leak and can bind to another repo | Missing. Redaction removes secrets; it does not parameterise |
| **Re-verification at the recipient**: a transferred procedure is a hypothesis until it passes the recipient's check; evidence must say *where* it was verified | Partly: evidence pins `target_version`; no tag for "verified here" versus "verified elsewhere" found |
| **Negative-transfer guard**: the hook once fixed 6 failures and broke 5 successes (`docs/findings.md`) | Missing as a gate: a transferred procedure should start on probation (shadow-score on the recipient's tasks before it is allowed to act) |
| **Cross-tenant evidence threshold**: publish to the commons only after verification in at least N distinct tenants, N set by a number, not a feeling | Missing; `BAND0_DECISIONS.md` is the place to rule on it |
| Attribution and contribution accounting (who contributed which verified procedure) | Partial: `backend/app/services/contributors.py` exists; whether it feeds any payout or credit is unchecked |
| Portable pack: a signed, versioned bundle of procedures plus their diffs and checks that moves between organisations | Partial: the in-repo library (`.stealth/library.md`, solutions as diffs, merge=union, `packaging/npm/lib/library.mjs:1-30`) is already a portable format inside one repository; no cross-organisation pack |
| Contract terms: ownership of contributed procedures, licence to recipients, no customer-code leakage, exit and withdrawal | Not a code item. The user has an open question: who owns the procedures. It must be decided before any second organisation is involved |
| A neutral operator that both sides trust, and an incentive for the first organisation to contribute | Business design; not in the repo |
| For research groups and universities: the transfer-office route (material or data transfer agreements, publication rights) | Not in the repo; separate from the product |

## 4. Order of work

1. Decide ownership and licence of contributed procedures (a one-page ruling; blocks everything outward-facing).
2. Targeting: `targets[]` in the brief, a recorded `scope_spill`, no blocking yet. Measure on the 153 paired tasks.
3. Abstraction pass over a sample of existing procedures; count how many survive with no organisation-specific token.
4. Probation tag and a "verified where" field on evidence.
5. Only then a two-organisation test (one lead, one internal second repo as stand-in).

## 5. Experiments and numbers

- **E1 spill versus error.** On the recorded 153-task attempts, compute how often the changed files fall outside the hinted and
  related files, and whether spill predicts a wrong answer. Useful signal if spill rate among failures is at least double that
  among passes. No signal if the rates are within noise; then step 3 above is cosmetic. Offline, free.
- **E2 per-part checks on medium tasks.** Take the tasks the brief labels `medium`; run whole-task versus per-part with per-part
  checks, same models. Success: at least +3 solved of the medium subset at no more than +15% cost. Falsified: no gain or a loss.
- **E3 procedure audit.** Sample 30 formed procedures; for each, count organisation-specific tokens and rate whether the
  preconditions are checkable from a repository snapshot. Success: at least 20 of 30 abstract to typed slots with no loss of the
  check. This is the test of "the procedures formed are good".
- **E4 transfer.** Pick procedures verified in repo A and apply them to repo B tasks of the same failure signature, with the
  no-procedure arm as control, recording wins, losses and negative transfers separately. Success: net at least +3 with losses at
  most half of wins. Falsified: net at or below 0.

## 6. What I did not check

- Procedure quality: no procedure was read in this pass.
- Whether `contributors.py` drives any incentive.
- Whether the backend decomposition output carries a per-part target; the dispatch side does not use it.
- Any legal framework for cross-organisation transfer; that needs counsel, not this note.

## 7. Widening the scope (2026-10-17): from repair to business transformations, and the publication format

Repair is the first domain because its verifier is cheap (a test). The unit is wider: a **transformation record** is a claim, an
intervention (the procedure), a measurement plan, the evidence, and the replications. The video the founder cited
(youtube.com/watch?v=q_-_t5AmKz0, chapter 11:09-16:21 as described by the founder: publishing strained by AI-driven volume,
the case for interactive high-quality formats like the defunct Distill) could not be read here: the page fetch returned no
transcript and a search found none. This section therefore rests on the founder's description plus general knowledge of the
field, which is unverified at primary sources.

What the record would carry (mapped to what exists): claim and evidence (`claim_evidence.py`), a procedure with preconditions
(`applicability.py`), the verifier or measurement plan, the run trace (redacted), an audit trail of changes (ChangeSet, bi-temporal
invalidate-and-append: nothing deleted), and replications as further evidence rows against the same record.

Where the verifier changes: a test gives a verdict in seconds; a business transformation gives a KPI after weeks, noisy and
confounded. So the evidence class must be declared per record: (1) replayable check (code), (2) controlled field measurement
(randomised or difference-in-differences, a pre-stated non-inferiority or superiority margin, as in the pilot design), (3)
observational, labelled as such. A record states its class; claims never borrow strength from a stronger class.

How this answers gated review, and where it does not:
- Replay is checked by a machine and the log is public: whether a result reproduces is not a reviewer's opinion.
- Review events (who, which version, what was asked, what changed) go into an append-only ledger with the same bi-temporal
  semantics as the rest of the substrate; a rejected version stays visible. Identity can be pseudonymous but stable.
- Reviewers and replicators are credited through contributor accounting (to be checked, see section 3).
- It does **not** judge importance, novelty or whether the interpretation is right. Replay shows reproducibility, not truth. A weak
  check yields confident wrong records; the false-accept rate of the verifier (alpha, unmeasured on our checks) bounds how far this
  can be trusted. Volume is a risk too: free submission plus cheap generation floods the ledger, so submission needs a cost
  or a stake, and replication compute needs a budget owner.
- Prior art to read before claiming novelty (named from memory, unverified): Distill (interactive articles, paused 2021),
  ReScience, Code Ocean and similar executable-capsule services, CodaLab worksheets, Papers with Code, Registered Reports,
  OpenReview, eLife reviewed preprints, ML Reproducibility Challenge.

### 7.1 Goal importance and "tested everywhere" (founder idea, 2026-10-17)

Idea: a goal many procedures serve is important by that fact, can carry financial volume, and because the system is embedded in
companies, a new procedure for a sub-goal is tested across all of them as soon as it is added.

**Correction (founder, same day):** the reference is not "procedures that solve the goal". It is "procedures of which the goal is a
part": a procedure's step is a sub-goal, so the goal is a *component* of many parent procedures. That is the package-manager
shape: goal = interface, procedure for the goal = implementation, parent procedures = dependents. Importance is the number of
independent dependents (in-degree on part-of edges, or PageRank over them, so a sub-goal used by important parents counts more),
and a new implementation of a sub-goal is tested against every dependent, the way a compiler or distribution rebuilds all reverse
dependencies before a library change ships (Rust crater runs and distribution rebuild tests are the analogues, named from memory).
The test of a new sub-goal procedure is then: swap it into each parent, run each parent's own check, count regressions and wins.
Schema gap: I grepped `schema.md`, spec v4 and `backend/db` + `backend/app` for sub-goal, child procedure and composition terms
and found no edge from a procedure step to a goal. The only goal-to-goal edge is SPECIALIZES (migration 113). So the part-of edge,
and the parent-procedure's binding of a step to a goal with an input/output contract, are new. The contract matters: swapping is
safe only if the new implementation honours the interface the parents rely on (preconditions, outputs, side effects).

The rest of this subsection was written before the correction and still applies where noted: the credit, security and staged-rollout
points hold; "importance = independent verified reuse" now reads as "independent dependents whose own checks still pass".

Already in the schema (migration 102, `backend/db/102_contribution_economy.sql`): `procedure_usage_events` with `goal_id` and
`is_self_use` (self-use is never rewarded; independent reuse is the strongest signal), verification layers 0-5 where a
`verified_*` outcome needs layer 3 or above, an append-only `credit_ledger_events` (reasons: new_procedure, improvement,
verified_reuse, clawback), Wilson-interval ranking, and an accepted-edge goal abstraction DAG (`goal_abstraction.py`, SPECIALIZES).
`benchmark_transfer.py` already moves benchmark definitions across accepted goal neighbours.

Design points:
- **Importance is not reference count.** Counting procedures per goal rewards authors who spawn near-duplicates. Use independent
  tenants with verified reuse in a window, weighted by spend through the goal, discounted by concentration (a goal used verified in
  one tenant is not important however many procedures it has). Dedup via the existing embedding check.
- **Financial volume** = cost that flowed through the goal's procedures (dispatch and model spend, from run records). It can set the
  replication budget and the credit rate for the goal. Wash trading (a tenant running its own procedures to farm credit) is the
  obvious attack; independent-reuse-only rewards and per-tenant caps are the defence.
- **"Tested everywhere" is a staged rollout, not instant execution.** New sub-goal procedure -> offline replay in each interested
  tenant's isolated worktree on that tenant's own past tasks of that goal (no effect on their work) -> canary on a few tenants with
  a human-visible gate -> active. Promotion by a pre-stated sequential rule with a negative-transfer stop (the hook once broke 5
  successes while fixing 6). Results are stratified per environment; a pooled average hides tenants where it hurts.
- **Security is the first risk.** A procedure that auto-runs in many companies is a supply-chain channel. Required before any
  cross-tenant run: signed procedures with provenance, sandboxed execution (the worktree exists), no network or secrets in replay,
  per-tenant opt-in, and a revocation path (invalidate-and-append already gives the record; the push of the revocation is missing).
- **Cost and consent:** who pays the replay compute, and what each tenant agrees to have run on its code. A contract item.

Falsifiers: importance ranking by independent verified reuse disagrees little with plain reference count (then the extra machinery
buys nothing); a new procedure's replay results in tenant A do not predict its results in tenant B (rank correlation below 0.3
across 20 procedures); credit farming shows up in a simulated tenant that tries it.

### 7.2 Domain order: AI first, then biology and others (founder, 2026-10-17)

**Scope ruling, same day: for now the work stays on coding, AI and the agentic literature. Everything below about biology and
other domains is parked, kept only as the reason the core must stay domain-free.**

Core stays domain-free: goal, part-of edge with an interface contract, procedure, evidence class, replications, review ledger,
credits. A **domain adapter** supplies five things: (1) goal vocabulary and interface contracts, (2) the verifier and its harness,
(3) environment capture (the pack must run on someone else's machine), (4) data-rights class, (5) risk class and release gate.
Today the code-repair assumptions (git worktree, pytest-style check, `.stealth/`) live in `packaging/npm` and are in effect the
only adapter; they should be named as one and kept out of the core.

Order and why:
1. **AI and software** (now): verifier is cheap and replayable; the audience reads and runs code; the 153-task data exists.
   Includes ML research procedures (an eval harness, a data-cleaning recipe, a training recipe at small scale), where the
   "replay" is rerunning a stated experiment.
2. **Computational biology**: same evidence class as code, because pipelines are replayable. Steps are tools (QC, alignment,
   variant calling), so part-of composition is literal, and community truth sets (for example Genome in a Bottle for variant
   calling) give a ground-truth verifier. Existing workflow ecosystems (Nextflow and nf-core, Snakemake, CWL, WorkflowHub,
   RO-Crate) are the prior art and the packaging to interoperate with; all named from memory, unverified.
3. **Wet-lab and clinical protocols**: verifier is slow, costly, noisy and sometimes unavailable; evidence class 2 or 3 only;
   "tested everywhere" cannot mean automatic execution. Replication becomes a request to a lab, with human sign-off.

Biology-specific blockers that code does not have: (a) dual-use risk, so a release gate with named human review before any
procedure becomes visible to other tenants, and some goal categories never published; (b) data rights for human-subject data
(consent terms, and the applicable data-protection law in each jurisdiction; this needs counsel); (c) replication cost per
record, so the budget-owner and stake design in 7 matters more; (d) truth sets can be overfit, so hold-out sets are separate
from public ones.

Gate to move down the list, as numbers: at least 5 records in the current domain replayed by a party that did not write them,
at least 4 of 5 within the stated tolerance, across at least 2 organisations, before building the next adapter. Falsified for
the domain order if the AI-domain records do not replay across organisations; then fix that before widening.

Falsifiers (publication format): (a) a third party cannot replay a record on their own machine from the pack alone (test with 5 records, success is 4 of
5 reproduce the stated number within the stated tolerance); (b) for the field-measurement class, two organisations running the same
record disagree beyond its stated margin; (c) nobody outside the founders submits a record or a replication within a fixed window
once it is open.
