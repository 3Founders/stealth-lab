# Verification Disclaimer: Candidate vs. Verified

**STATUS: DRAFT — REQUIRES LEGAL REVIEW.**

This document explains exactly what "Candidate" and "Verified" mean for a StealthLab procedure,
based on what the verification code actually checks — not on how those words might sound. It is
referenced by the [Terms of Service](./TERMS_OF_SERVICE.md) and
[Global Commons Terms](./GLOBAL_COMMONS_TERMS.md).

## 1. Candidate ("Discovered") — proposed, not established

A procedure is a **Candidate** the moment it's created — captured privately, published, or
admitted from an internet source. It represents someone's (or some ingestion pipeline's) claim
that a set of steps accomplishes a task. At this stage:

- It has **not** been independently executed and confirmed by the verification pipeline.
- It may have been through the [admission safety gate](./ACCEPTABLE_USE_POLICY.md) (for
  internet-sourced content) — but that gate is a hygiene/safety check, explicitly **not** a
  correctness check. Nothing in the admission gate ever sets a procedure's status to Verified.
- Treat a Candidate procedure the way you'd treat an unreviewed suggestion: plausible, but
  unconfirmed.

## 2. Verified — independently tested, evidence-backed, still not a guarantee

A procedure (or, more precisely, a specific execution claim about it) becomes **Verified** when
independent execution evidence supports the claimed behavior, under the conditions actually
tested. Concretely, the pipeline that produces this evidence is layered:

1. **Artifact validation** (`backend/app/execution/artifact_validation.py`) — a generic,
   capability-agnostic check: is the edited artifact even valid (e.g. does a `.py` file parse and
   import). This says nothing about whether the code does what it claims.
2. **Behavioral verification** (`backend/app/execution/behavior_verification.py`,
   `backend/app/execution/verifiers/`) — an **opt-in**, capability-specific check that only runs
   for capabilities that have a registered verifier. It re-checks the procedure's own advertised
   behavior (for example, `verify_mcp_lazy_tool_schemas` re-runs a live probe to confirm a
   specific claimed behavior actually holds), and it can only **downgrade** an already-valid
   declared success — it never launders a declared failure into a success, and a capability with
   no registered verifier is simply not behaviorally checked at all (there is no universal,
   mandatory verification gate).

**What "Verified" therefore actually means:** for the specific capability/procedure that has a
registered, executed verifier, independent evidence was collected under the tested conditions and
supported the claimed behavior. It does **not** mean:

- The procedure is universally correct, in every environment, OS, dependency version, or
  configuration.
- The procedure will keep working forever. Environments, dependencies, and third-party APIs
  change; a procedure verified against yesterday's conditions can go **stale**. StealthLab's
  design accounts for this (see references to staleness/applicability controls in
  `STEALTHLAB-LAUNCH-COMPLIANCE-SPEC-V1.md` §1) but does not promise continuous re-verification
  of every Verified procedure on every dependency change.
- Any capability without a registered behavioral verifier received behavioral checking at all —
  for those, "Verified" (if shown) rests only on artifact validation plus whatever evidence
  standard the surrounding feature defines, not a re-run of the procedure's actual claimed
  behavior.
- The procedure is safe, in the Acceptable-Use sense — verification is a correctness signal, not
  a safety/malice signal. Safety is the separate admission gate's job (§1 above).

## 3. Local verification is not global verification

Verifying a procedure privately does not make its public (Global Commons) counterpart Verified.
Publishing creates a brand-new global candidate with **zero inherited verification evidence** —
see [Global Commons Terms](./GLOBAL_COMMONS_TERMS.md) §2. Global trust is earned independently,
by independent executions against the published version, specifically so that one person's
possibly-idiosyncratic local success can't silently promote a shared, public claim.

## 4. Revalidation

A procedure that was Verified under one set of conditions (dependency versions, OS, repository
state) may no longer hold under different conditions. StealthLab does not currently guarantee
automatic, continuous revalidation of every previously-Verified procedure — treat a "Verified"
label as time-stamped evidence, not a permanent certification. Before relying on a Verified
procedure in a materially different environment, re-run it or check for staleness signals if the
UI exposes them.

## 5. Practical guidance

- Candidate = someone's claim. Review before running, especially for anything destructive or
  security-sensitive.
- Verified = independent evidence exists for the specific claim tested. Still review before
  running in a materially different environment, and never treat it as a warranty.
- Neither status changes your responsibility under the [Acceptable Use Policy](./ACCEPTABLE_USE_POLICY.md)
  or the disclaimers in the [Terms of Service](./TERMS_OF_SERVICE.md) §9.

---
*Generated from repository state on 2026-09-09. Cross-references:
`backend/app/execution/behavior_verification.py`, `backend/app/execution/artifact_validation.py`,
`backend/app/execution/verifiers/mcp_lazy_tool_schemas.py`, `backend/app/services/publish.py`.*
