"""V0 gate — the boundary every payload crosses before persistence.

Band 1.3 / ROADMAP ground rule 2: nothing enters the system without
scope + provenance (+ extractor version for derived objects). This gate
is the enforcement point; storage columns are nullable only so THIS
module -- not the database engine -- owns the error message.

The ten scope types mirror spec §3's canonical vocabulary. `entity` is
the object-level granularity (a claim about one function); `branch` is
repo lineage. Both exist; they are different tiers, not synonyms.
"""
from __future__ import annotations

SCOPE_TYPES = (
    "global", "organization", "team", "project", "repository",
    "branch", "user", "session", "task", "entity",
)

PROVENANCE_VALUES = (
    "company_ingested", "company_debate", "public_generated",
    "prior_library", "system_pending_review",
)

# Derived objects must say what made them (§39 invariants 20–21).
DERIVED_PROVENANCE = ("public_generated", "prior_library")


class V0Violation(ValueError):
    """Raised when a payload would enter the substrate without the
    mandatory identity fields. Callers surface this verbatim: it is a
    contract violation by the producer, not an internal error."""


def validate_scope(scope_type: str | None, entity_id: str | None = None,
                   *, allow_global_entity_id: bool = False) -> tuple[str, str | None]:
    if not scope_type:
        raise V0Violation("V0: scope_type is required — nothing is implicit-global")
    if scope_type not in SCOPE_TYPES:
        raise V0Violation(f"V0: unknown scope_type {scope_type!r} (valid: {SCOPE_TYPES})")
    if entity_id and scope_type == "global":
        if not allow_global_entity_id:
            raise V0Violation("V0: global scope cannot carry an entity_id")
    if scope_type != "global" and not entity_id:
        raise V0Violation(
            f"V0: scope_type {scope_type!r} requires scope_entity_id "
            "(what does this scope point at?)"
        )
    return scope_type, entity_id


def validate_provenance(provenance: str | None, *,
                        derived: bool = False,
                        extractor_version: str | None = None) -> str:
    if not provenance:
        raise V0Violation("V0: provenance is required — nothing is born unattributed")
    if provenance not in PROVENANCE_VALUES:
        raise V0Violation(f"V0: unknown provenance {provenance!r} (valid: {PROVENANCE_VALUES})")
    if derived:
        if not extractor_version:
            raise V0Violation("V0: derived objects require extractor_version (§39 inv 20–21)")
        if provenance == "company_ingested":
            # A derived object was never ingested from company documents;
            # stamping it so would launder its origin.
            raise V0Violation(
                "V0: derived objects cannot be company_ingested — use "
                "public_generated or prior_library"
            )
    return provenance
