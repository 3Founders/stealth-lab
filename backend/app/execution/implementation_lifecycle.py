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
from those real columns plus real evidence/binding facts, honestly.

HONEST GAPS (documented, not silently glossed): this codebase's data
model has no signal distinguishing DISCOVERED from REGISTERED (no
"candidate implementation noticed but not yet registered" state exists
anywhere -- `register()` IS the first real event), so both collapse into
REGISTERED here. It also has no distinct signal for INCOMPATIBLE vs
FAILED_EXECUTION vs FAILED_VERIFICATION beyond "evidence recorded a
failure outcome against this implementation" -- `evidence.failure_class`
(a free-text field) is the closest real distinguishing signal that
exists and is surfaced as-is rather than force-mapped onto one of the
three. STALE (a freshness/staleness state) is not computed at all --
there is no "implementation last used at" signal in this schema to
threshold against; forcing one would be exactly the fabricated-signal
pattern B38 forbids.

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
  FAILED (class in failure_classes_seen) >=1 evidence row (target_type=
                         'implementation', outcome_status='failure')
                         exists -- `failure_classes_seen` names the real,
                         recorded `failure_class` values rather than
                         guessing INCOMPATIBLE vs FAILED_EXECUTION vs
                         FAILED_VERIFICATION for evidence that doesn't
                         itself distinguish them
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

CHAIN: tuple[str, ...] = (
    "REGISTERED", "RESOLVABLE", "AVAILABLE", "VERIFIED_IN_CONTEXT", "REUSED",
)


async def compute_implementation_lifecycle_state(
    pool: asyncpg.Pool, implementation_id: str,
) -> Optional[dict[str, Any]]:
    """Returns None if the implementation does not exist. Otherwise:
    {"reached": [...ordered CHAIN states this implementation satisfies...],
     "current_state": str, "status_flags": {"unavailable": bool,
     "retired": bool}, "failure_classes_seen": [...distinct real
     evidence.failure_class values recorded against it, if any...]}."""
    impl = await pool.fetchrow(
        "SELECT status, verification_status, locator, invocation FROM implementations WHERE id = $1",
        implementation_id,
    )
    if impl is None:
        return None

    reached: list[str] = ["REGISTERED"]

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
    if impl["verification_status"] == "verified" or has_success_evidence:
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

    return {
        "reached": reached,
        "current_state": reached[-1],
        "skipped_optional": skipped_optional,
        "status_flags": {
            "unavailable": impl["status"] in ("disabled", "quarantined"),
            "retired": impl["status"] == "deprecated",
        },
        "failure_classes_seen": sorted({r["failure_class"] for r in failure_rows}),
    }
