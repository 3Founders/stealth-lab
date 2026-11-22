# Tech-transfer plan — what remains (as of 2026-10-17, main @ eb524ed)

Read against `docs/plan_2026-10_tech_transfer_targeted_repair.md` (written 10-15, widened 10-17).
Status below is verified against the working tree, not inferred from the plan.

**Headline: nothing in §4 has started. The plan's foundational claim — that procedures transfer
across organisations at all — is still untested, and E4 is the only experiment that tests it.**

---

## 1. §4 order of work

| # | Item | Status | Evidence in the tree |
|---|---|---|---|
| 1 | **Decide ownership/licence of contributed procedures** (one-page ruling) | **NOT DONE** | `BAND0_DECISIONS.md` last modified 2026-08-29 — predates the plan. Plan §3: "The user has an open question: who owns the procedures." |
| 2 | **`targets[]` in the brief + recorded `scope_spill`, no blocking** | **NOT STARTED** | `brief.mjs:36,177-178` still emits `files_hinted: [string]` and `parts: [string]`. Repo-wide `scope_spill` = **0 hits** |
| 3 | **Abstraction pass / E3** | **NOT STARTED** | The 350 `abstraction` hits are `goal_abstraction.py` + SPECIALIZES — the *goal* DAG. Procedure parameterisation (names→typed slots) does not exist |
| 4 | **Probation tag + "verified where" field** | **NOT STARTED** | `probation` = 0 hits; `verified_where` = 0 hits. `evidence` pins `target_version` only |
| 5 | **Two-organisation test / E4** | **BLOCKED** on 1–4 | |

**One correction to the plan's sequencing.** Item 1 has *no engineering dependency*. It blocks
everything **outward-facing** (item 5, contract terms, the neutral-operator design) but it does not
block E1, E2 or E3. Treat it as a parallel human/legal track started now, not as a gate in front of
the engineering.

---

## 2. §5 experiments — none have been run

| Exp | What it needs | Cost | Notes |
|---|---|---|---|
| **E1** spill vs error | `scope_spill` instrumentation | **Offline, free** | Uses the recorded 153-task attempts. Its result *decides* whether spill becomes a rule or stays cosmetic — so it must precede step 2's blocking decision. **Best value per hour in the whole plan** |
| **E2** per-part checks | brief change + one test per part | Runs models | Bar: ≥+3 solved on the medium subset at ≤+15% cost. Note the brief **refuses large tasks** (`brief.mjs:10`), so this targets the medium band that is currently refused or fails |
| **E3** procedure audit | 30 procedures sampled and read | Offline | Bar: ≥20/30 abstract to typed slots with no loss of check. Plan §6 concedes **no procedure was read in this pass** |
| **E4** transfer | items 1–4 first | Multi-org | Bar: net ≥+3, losses ≤ half of wins. **This is the only test of the plan's foundational claim** |

---

## 3. §7.1 — the structural blocker nobody has touched

The plan's corrected idea ("importance = number of independent dependents on part-of edges") **cannot
be computed today**, because the edge does not exist:

- `part_of` / `partof` = **0 hits** across `backend/db` and `backend/app`.
- Migration 130 (`goal_search_index.has_procedures`) is a **derived boolean flag**, not an edge. It
  excludes empty Goals from search — useful, but it carries no composition.
- The only goal-to-goal relation is `SPECIALIZES`.

**Two new schema items are required:**
1. a **part-of edge** from a procedure step to a goal;
2. an **interface contract** on that edge (preconditions, outputs, side effects) — because a swap is
   only safe if the new implementation honours what parents rely on. Without the contract, "swap the
   sub-goal procedure and re-run every dependent" cannot be done safely.

Already available and reusable: migration 102 (`procedure_usage_events` with `goal_id` and
`is_self_use`, verification layers 0–5 where `verified_*` needs layer ≥3, append-only
`credit_ledger_events`, Wilson-interval ranking), `goal_abstraction.py` (SPECIALIZES), and
`benchmark_transfer.py`.

---

## 4. §7.2 — the adapter extraction

Scope ruling (10-17): **stay on coding / AI / agentic literature. Biology parked.** That ruling is
recorded; the structural consequence is not acted on.

> "Today the code-repair assumptions (git worktree, pytest-style check, `.stealth/`) live in
> `packaging/npm` and are in effect the only adapter; they should be named as one and kept out of the
> core."

Not done. The core cannot be domain-free while the only domain's verifier is imported at the call
sites. Naming one adapter and routing it behind an interface is the concrete deliverable.

**Gate to widen** (not met): ≥5 records replayed by a party who did not write them, ≥4/5 within
stated tolerance, across ≥2 organisations. Also the falsifier for the whole domain order: *if AI-domain
records do not replay across organisations, fix that before widening.*

---

## 5. §7 security prerequisites — required before **any** cross-tenant run

| Requirement | State |
|---|---|
| Sandboxed execution | **Exists** — isolated git worktree per run |
| Per-tenant opt-in | **Likely exists** — migrations 136 `org_governance`, 139 `org_denials_and_user_usage` |
| Signed procedures with provenance | Unconfirmed |
| No network or secrets in replay | Unconfirmed |
| **Revocation path** | **Missing.** Plan §7.1: "invalidate-and-append already gives the record; **the push of the revocation is missing**" |

The plan's own words: *"Security is the first risk. A procedure that auto-runs in many companies is a
supply-chain channel."* This is the one §7 workstream that is a hard blocker rather than a nice-to-have.

---

## 6. The thing that connects this plan to the 205-task run

Plan §7 says: *"the false-accept rate of the verifier (alpha, **unmeasured on our checks**) bounds how
far this can be trusted."*

**It is now measured — once.** The final A/B run (`HANDOFF_abtest_2026-10-11.md` §12) records the
code gap as **DS-1000 (3) and BIRD (2): cheap answers that passed every check but were wrong.** That
is the first empirical value of alpha on our own checks, and it is the plan's central risk made
concrete: a weak check yields confident wrong records.

**The queued mitigation is also the measurement instrument.** The agreement check — accept only when
two cheap answers agree on the tests *plus* extra inputs, otherwise hand to Opus — both reduces alpha
and lets us estimate it. Running it on the recorded attempts gives a second alpha number, and the pair
brackets how much of the 137-vs-140 gap is verifier weakness versus routing weakness.

---

## 7. §6 — still unchecked from the plan's own list

- **Procedure quality.** No procedure has been read. E3 is the first look.
- **Whether `contributors.py` drives any incentive.** It exists; whether it feeds a payout or credit is
  unchecked — and credits are load-bearing for the whole contribution story.
- **Whether the backend decomposition output carries a per-part target.** Dispatch does not call the
  backend decomposition at all today (no `decompos` match under `packaging/npm`), so the generative
  decomposition service with its validity boundary is currently **unwired into the product**.
- **Legal framework for cross-organisation transfer.** Needs counsel, not a note.

---

## 8. Recommended order

**Start now, in parallel:**

- **Track A (human/legal, no code):** the ownership and licence ruling. It blocks nothing in
  engineering and blocks everything outward-facing.
- **Track B (offline, free):** **E1**. Compute spill vs error on the recorded 153-task attempts. It
  decides whether `scope_spill` ever becomes a rule, and it needs no new decisions.
- **Track C (small, high leverage):** **E3** — read 30 formed procedures and count org-specific
  tokens. This is the first evidence that the transfer unit is real, and it is the cheapest test of
  the plan's premise.

**Then, in order:**

1. `targets[]` + `scope_spill` recording (item 2) — informed by E1's result, still **recording only,
   not blocking**.
2. Abstraction as a first-class pass (item 3) — E3 tells you what to parameterise.
3. Probation tag and "verified where" (item 4). The negative-transfer history is concrete: **the hook
   once fixed 6 failures and broke 5 successes** (`docs/findings.md`).
4. The part-of edge + interface contract (§7.1) — unblocks importance, "tested everywhere", and
   safe substitution. Prerequisite for credit design.
5. **Revocation push** and signed procedures (§7.1) — hard blockers before cross-tenant runs.
6. **E2** per-part checks.
7. Adapter extraction (§7.2) — name the code adapter, keep the core domain-free.
8. **E4** the two-organisation test, once 1–4 and Track A are done.

**Do not widen the domain** until the §7.2 gate is met: ≥5 records replayed by a third party, ≥4/5
within tolerance, ≥2 organisations.