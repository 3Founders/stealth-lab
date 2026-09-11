"""
MCP hardening B29: Implementation lifecycle -- the spec's own named chain:

    DISCOVERED -> REGISTERED -> RESOLVABLE -> AVAILABLE
    -> VERIFIED_IN_CONTEXT -> REUSED

with failure states UNAVAILABLE / INCOMPATIBLE / FAILED_EXECUTION /
FAILED_VERIFICATION / STALE / RETIRED, and: "Historical evidence
remains. A new implementation version does not inherit verification
automatically."

WHY A DERIVED VIEW (same reasoning as B4's stealth_execution_contract.py,
CLAUDE.md rule 2: no parallel architectures): `implementations.status`
(candidate/active/deprecated/disabled/quarantined, migration 33) and
`implementations.verification_status` (unverified/verified) are the
REAL, already-enforced lifecycle columns this codebase has -- adding a
second, independently-mutated lifecycle-state column would only be able
to drift from them. This module derives the spec's richer named states
from those real columns plus real evidence/binding/claim facts, honestly.

STRICT CLOSURE (this pass): DISCOVERED and STALE were previously
declared "impossible without a fabricated signal" and left permanently
unreachable. Re-investigated against real, ALREADY-EXISTING production
mechanisms -- both are now real, computed states, closed without
inventing anything:

  DISCOVERED  A real `environment_fact` claim (app/services/
              environment_probe.py::assert_environment_claims --
              genuinely wired into the real extract_procedure() path by
              this pass, previously a dormant, zero-caller function)
              whose `object` names this implementation's own `name` or
              `provider`, asserted (t_created) strictly BEFORE this
              implementation's own registration. This is Stealth
              GENUINELY having observed the candidate (a real
              deterministic filesystem probe of a real repo, persisted
              with real provenance) before it became a registered
              Implementation -- never merely "a row exists but
              inactive". Absence of a matching prior claim is honest:
              this implementation's own history starts at REGISTERED,
              not a fabricated DISCOVERED.
  STALE       This implementation was VERIFIED_IN_CONTEXT (see below)
              AND a version of the SAME (name, provider) identity with
              a STRICTLY HIGHER `version` number now exists. Grounded
              directly in this item's own literal text -- "a new
              implementation version does not inherit verification
              automatically" -- so a verified-but-superseded version is
              exactly what became stale: real, superseded verification.
              `(name, provider, version)` is already the real, DB-
              enforced identity tuple (idx_implementations_identity,
              migration 33) -- no new column, no invented threshold, no
              elapsed-time policy (none exists anywhere in this
              codebase to reuse, confirmed by investigation -- see
              PR discussion). Distinct from RETIRED (status=
              'deprecated', an explicit operator action) -- STALE is
              purely relative to a newer sibling now existing.

  REGISTERED           implementations row exists
  RESOLVABLE            `locator` or `invocation` is a non-empty dict --
                         the same "something to actually resolve/invoke"
                         signal `implementation_registry.resolve()`
                         itself depends on
  AVAILABLE              status == 'active' (implementation_registry.
                         activate(), a real, guarded transition)
  VERIFIED_IN_CONTEXT    verification_status == 'verified' OR >=1
                         evidence row (target_type='implementation',
                         outcome_status='success') exists -- "in context"
                         because evidence rows carry a context_key,
                         never a context-free global claim
  REUSED                 bound (procedure_implementations, t_invalid IS
                         NULL) to >=2 DISTINCT procedures -- real reuse
                         across contexts, not merely registered once

  UNAVAILABLE            status IN ('disabled', 'quarantined')
  RETIRED                status == 'deprecated'
  STALE                  see above
  FAILED (class in failure_classes_seen) >=1 evidence row (target_type=
                         'implementation', outcome_status='failure')
                         exists -- `failure_classes_seen` names the real,
                         recorded `failure_class` values rather than
                         guessing INCOMPATIBLE vs FAILED_EXECUTION vs
                         FAILED_VERIFICATION for evidence that doesn't
                         itself distinguish them

REMAINING HONEST GAP (documented, not silently glossed): this codebase
still has no distinct signal for INCOMPATIBLE vs FAILED_EXECUTION vs
FAILED_VERIFICATION beyond "evidence recorded a failure outcome" --
`evidence.failure_class` (a free-text field) is the closest real
distinguishing signal that exists and is surfaced as-is rather than
force-mapped onto one of the three.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

CHAIN: tuple[str, ...] = (
    "DISCOVERED", "REGISTERED", "RESOLVABLE", "AVAILABLE", "VERIFIED_IN_CONTEXT", "REUSED",
)


async def compute_implementation_lifecycle_state(
    pool: asyncpg.Pool, implementation_id: str,
) -> Optional[dict[str, Any]]:
    """Returns None if the implementation does not exist. Otherwise:
    {"reached": [...ordered CHAIN states this implementation satisfies...],
     "current_state": str, "status_flags": {"unavailable": bool,
     "retired": bool, "stale": bool}, "failure_classes_seen": [...distinct
     real evidence.failure_class values recorded against it, if any...]}."""
    impl = await pool.fetchrow(
        "SELECT name, provider, version, t_created, status, verification_status, "
        "locator, invocation FROM implementations WHERE id = $1",
        implementation_id,
    )
    if impl is None:
        return None

    reached: list[str] = []

    # DISCOVERED: a real environment_fact claim naming this implementation's
    # own name/provider, asserted before this row's own registration.
    # knowledge_nodes.properties is JSONB -- ->> reads the real text field
    # environment_probe.py's own INSERT writes ('object'), never inferred.
    discovered = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM knowledge_nodes "
        "WHERE node_type = 'claim' AND t_invalid IS NULL "
        "AND properties->>'claim_type' = 'environment_fact' "
        "AND lower(properties->>'object') IN (lower($2), lower($3)) "
        "AND t_created < $1)",
        impl["t_created"], impl["name"], impl["provider"],
    )
    if discovered:
        reached.append("DISCOVERED")

    reached.append("REGISTERED")

    has_locator_or_invocation = bool(impl["locator"]) or bool(impl["invocation"])
    if has_locator_or_invocation:
        reached.append("RESOLVABLE")

    if impl["status"] == "active":
        reached.append("AVAILABLE")

    has_success_evidence = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM evidence WHERE target_type = 'implementation' "
        "AND target_id = $1 AND outcome_status = 'success')",
        implementation_id,
    )
    verified_in_context = impl["verification_status"] == "verified" or bool(has_success_evidence)
    if verified_in_context:
        reached.append("VERIFIED_IN_CONTEXT")

    distinct_procedures_bound = await pool.fetchval(
        "SELECT COUNT(DISTINCT procedure_id) FROM procedure_implementations "
        "WHERE implementation_id = $1 AND t_invalid IS NULL",
        implementation_id,
    )
    if distinct_procedures_bound and distinct_procedures_bound >= 2:
        reached.append("REUSED")

    furthest_index = max(CHAIN.index(s) for s in reached)
    skipped_optional = [s for s in CHAIN[:furthest_index + 1] if s not in reached]

    failure_rows = await pool.fetch(
        "SELECT DISTINCT failure_class FROM evidence WHERE target_type = 'implementation' "
        "AND target_id = $1 AND outcome_status = 'failure' AND failure_class IS NOT NULL",
        implementation_id,
    )

    # STALE: verified in some context, but a strictly newer version of the
    # SAME (name, provider) identity now exists -- "a new implementation
    # version does not inherit verification automatically" (this item's
    # own literal text), so this row's own verification is exactly what
    # went stale.
    newer_version_exists = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM implementations "
        "WHERE name = $1 AND provider = $2 AND version > $3)",
        impl["name"], impl["provider"], impl["version"],
    )
    is_stale = verified_in_context and bool(newer_version_exists)

    return {
        "reached": reached,
        "current_state": reached[-1],
        "skipped_optional": skipped_optional,
        "status_flags": {
            "unavailable": impl["status"] in ("disabled", "quarantined"),
            "retired": impl["status"] == "deprecated",
            "stale": is_stale,
        },
        "failure_classes_seen": sorted({r["failure_class"] for r in failure_rows}),
    }
