"""PublicationService (launch compliance Phase 4 / LC-002, data-flow §8-12,24-25).

ONE canonical operation: take a caller's PRIVATE or ORGANIZATION
`procedures` row and create a fresh GLOBAL CANDIDATE, or refuse.

It never:
  - publishes without an authenticated actor who owns the source row;
  - copies private verification counts / evidence into the global row
    (the candidate starts with zero independent evidence);
  - publishes a row that still carries a secret / personal / confidential
    signal after sanitization, or that depends on an un-generalizable
    private dependency;
  - promotes silently after a successful execution.

It always:
  - traverses dependencies and classifies each PUBLIC / PRIVATE /
    ORGANIZATION / MIXED / UNKNOWN;
  - runs the sanitizer (reusing services/publish.py's secret + path
    scrub primitives);
  - records a durable `publication_records` row (the contribution proof);
  - preserves provenance and emits an audit event.

Withdrawal traverses the same lineage and returns one of WITHDRAWN /
WITHDRAWN_FROM_RETRIEVAL / REQUIRES_REMEDIATION /
RETAINED_AS_INDEPENDENTLY_SOURCED without rewriting historical evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.classification import DataClass, PRIVATE_CLASSES, classify_procedure_row
from app.services.publish import _scrub_value  # reuse the real secret + path scrub
from app.services.trace_redaction import redact_value

PUBLICATION_SANITIZER_VERSION = "pub_sanitize_v1"
PUBLICATION_PROVENANCE = "prior_library"  # a vetted contribution entering the commons


class PublicationDenied(PermissionError):
    """The publication gate refused. `.reasons` lists every failed check."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class SourceProcedureNotFound(LookupError):
    pass


@dataclass
class DependencyReport:
    total: int = 0
    public: int = 0
    private: int = 0
    organization: int = 0
    unknown: int = 0
    blocking: list[str] = field(default_factory=list)

    @property
    def classification(self) -> str:
        if self.blocking:
            return "MIXED"
        if self.private or self.organization:
            return "MIXED"
        if self.unknown:
            return "UNKNOWN"
        return "PUBLIC"


_SRC_SELECT = "SELECT * FROM procedures WHERE id = $1::uuid"
_DEPS_SELECT = (
    "SELECT d.dependency_ref, d.resolution_status, d.target_procedure_id, "
    "p.visibility AS target_visibility, p.name AS target_name "
    "FROM procedure_dependencies d "
    "LEFT JOIN procedures p ON p.id = d.target_procedure_id "
    "WHERE d.procedure_id = $1::uuid"
)
_IMPL_SELECT = (
    "SELECT resource_path FROM procedure_implementations WHERE procedure_id = $1::uuid"
)
_PUBREC_INSERT = """
    INSERT INTO publication_records
      (source_object_type, source_object_id, published_object_type,
       published_object_id, actor_subject, actor_user_id, organization_id,
       destination_scope, sanitization_version, sanitization_record,
       provenance_version, dependency_report, classification_report,
       source_license, review_state)
    VALUES ('procedure', $1, 'procedure', $2, $3, $4::uuid, $5::uuid,
            'global_candidate', $6, $7::jsonb, $8, $9::jsonb, $10::jsonb, $11,
            'candidate')
    RETURNING id::text
"""


async def _traverse_dependencies(pool: Any, source_row_id: str) -> DependencyReport:
    rep = DependencyReport()
    for d in await pool.fetch(_DEPS_SELECT, source_row_id):
        rep.total += 1
        vis = (d["target_visibility"] or "").lower()
        if d["resolution_status"] not in ("resolved", "resolved_verified", None) and not d["target_procedure_id"]:
            rep.unknown += 1
            rep.blocking.append(
                f"dependency {d['dependency_ref']!r} is unresolved and cannot be classified"
            )
        elif vis == "private":
            rep.private += 1
            rep.blocking.append(
                f"dependency {d['dependency_ref']!r} ({d['target_name']}) is PRIVATE and "
                "cannot be generalized automatically"
            )
        elif vis in ("org", "organization"):
            rep.organization += 1
            rep.blocking.append(
                f"dependency {d['dependency_ref']!r} is ORGANIZATION-scoped"
            )
        elif vis == "public":
            rep.public += 1
        else:
            rep.unknown += 1
    return rep


def _residual_secret_signals(scrubbed: dict) -> list[str]:
    """After sanitization, does anything still look like a secret / private
    path / personal identifier? redact_value would have replaced a real
    match, so a surviving marker means the scrub already neutralized it;
    we look for patterns the scrub does NOT cover as a backstop."""
    import json
    import re

    blob = json.dumps(scrubbed, default=str)
    signals: list[str] = []
    if re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", blob):
        signals.append("an email address remains after sanitization")
    if re.search(r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*\S+", blob):
        signals.append("a credential-shaped key/value remains after sanitization")
    if re.search(r"[A-Za-z]:\\\\Users\\\\|/home/[a-z]|/Users/[A-Za-z]", blob):
        signals.append("a private home-directory path remains after sanitization")
    return signals


async def publish_procedure(
    pool: Any,
    *,
    source_row_id: str,
    actor_subject: str,
    actor_user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict:
    """The canonical publish. Returns
    {"publication_id", "global_procedure_id", "global_row_id",
     "dependency_report", "classification", "sanitization"}.
    Raises SourceProcedureNotFound / PublicationDenied."""
    from app.services.audit import record_audit_event
    from app.services.procedures import capture_procedure

    src = await pool.fetchrow(_SRC_SELECT, source_row_id)
    if src is None:
        raise SourceProcedureNotFound(source_row_id)
    src = dict(src)

    reasons: list[str] = []

    # 1. authority: the actor must own the source row.
    if (src.get("owner_id") or "") != actor_subject:
        reasons.append("actor does not own the source procedure")

    # 2. scope: only PRIVATE / ORG rows are publishable (a public row is
    #    already global).
    if (src.get("visibility") or "").lower() not in ("private", "org", "organization"):
        reasons.append(
            f"source visibility {src.get('visibility')!r} is not publishable "
            "(only PRIVATE or ORGANIZATION)"
        )

    # 3. classification: an execution-secret / personal / confidential /
    #    security-classified source is rejected outright.
    src_class = classify_procedure_row(src)
    if src_class in (
        DataClass.EXECUTION_SECRET, DataClass.PERSONAL_DATA,
        DataClass.CONFIDENTIAL_DATA, DataClass.SECURITY_DATA,
    ):
        reasons.append(f"source is classified {src_class.value}; not publishable")

    # 4. dependency traversal.
    dep_report = await _traverse_dependencies(pool, source_row_id)
    reasons.extend(dep_report.blocking)

    # 5. provenance / license presence.
    dp = src.get("domain_payload") or {}
    source_license = None
    if isinstance(dp, dict):
        source_license = dp.get("source_license") or (dp.get("frontmatter") or {}).get("license")
    if not src.get("provenance"):
        reasons.append("source has no provenance (data-flow INV-05)")

    # 6. sanitize (reuse the real primitives).
    scrubbed = {
        "name": _scrub_value(src.get("name")),
        "goal": _scrub_value(src.get("goal")),
        "steps": _scrub_value(src.get("steps") or []),
        "preconditions": _scrub_value(src.get("preconditions") or []),
        "invariants": _scrub_value(src.get("invariants") or []),
        "failure_conditions": _scrub_value(src.get("failure_conditions") or []),
        "exclusions": _scrub_value(src.get("exclusions") or []),
    }
    residual = _residual_secret_signals(scrubbed)
    reasons.extend(residual)
    sanitization_record = {
        "version": PUBLICATION_SANITIZER_VERSION,
        "fields_scrubbed": sorted(scrubbed.keys()),
        "residual_signals": residual,
    }

    if reasons:
        await record_audit_event(
            pool, actor_subject=actor_subject, action="publication_rejected",
            object_type="procedure", object_id=source_row_id,
            actor_user_id=actor_user_id, tenant_id=organization_id,
            details={"reasons": reasons, "dependency": dep_report.classification},
        )
        raise PublicationDenied(reasons)

    # 7. fresh Global Candidate. NO evidence, NO verification stats, NO
    #    private embedding are forwarded -- a fresh public row gets the
    #    DB's zero-evidence default and a fresh public re-embed later.
    result = await capture_procedure(
        pool,
        name=scrubbed["name"],
        goal=scrubbed["goal"],
        steps=scrubbed["steps"],
        preconditions=scrubbed["preconditions"],
        invariants=scrubbed["invariants"],
        failure_conditions=scrubbed["failure_conditions"],
        exclusions=scrubbed["exclusions"],
        provenance=PUBLICATION_PROVENANCE,
        domain_payload={
            "published_from_procedure_row_id": source_row_id,
            "published_by": actor_subject,
            "source_domain": src.get("domain"),
            "source_license": source_license,
            "dependency_classification": dep_report.classification,
        },
        created_by="publication_service",
        owner_id=actor_subject,
        visibility="public",
        scope_type="global",
        scope_entity_id=None,
    )

    pub_id = await pool.fetchval(
        _PUBREC_INSERT,
        source_row_id, result["id"], actor_subject, actor_user_id, organization_id,
        PUBLICATION_SANITIZER_VERSION,
        _json(sanitization_record),
        "v0_gate@1",
        _json({
            "total": dep_report.total, "public": dep_report.public,
            "private": dep_report.private, "organization": dep_report.organization,
            "unknown": dep_report.unknown, "classification": dep_report.classification,
        }),
        _json({"source_class": src_class.value}),
        source_license,
    )

    await record_audit_event(
        pool, actor_subject=actor_subject, action="publication_approved",
        object_type="procedure", object_id=source_row_id,
        actor_user_id=actor_user_id, tenant_id=organization_id,
        details={
            "publication_id": pub_id, "global_row_id": result["id"],
            "dependency_classification": dep_report.classification,
            "source_license": source_license,
        },
    )
    await record_audit_event(
        pool, actor_subject=actor_subject, action="global_candidate_created",
        object_type="procedure", object_id=result["id"],
        actor_user_id=actor_user_id,
        details={"publication_id": pub_id, "from": source_row_id},
    )

    return {
        "publication_id": pub_id,
        "global_procedure_id": result["procedure_id"],
        "global_row_id": result["id"],
        "dependency_report": {
            "total": dep_report.total, "classification": dep_report.classification,
        },
        "classification": src_class.value,
        "sanitization": sanitization_record,
        "verification": "candidate",
        "scope": "GLOBAL CANDIDATE",
    }


_PUBREC_SELECT = "SELECT * FROM publication_records WHERE id = $1::uuid"
_INDEP_EVIDENCE = """
    SELECT COALESCE(SUM(independent_success_count), 0)
      FROM procedure_evidence_stats
     WHERE procedure_row_id = $1::uuid
"""


async def withdraw_publication(
    pool: Any, *, publication_id: str, actor_subject: str,
    actor_user_id: Optional[str] = None,
) -> dict:
    """Traverse lineage; decide the withdrawal outcome without rewriting
    historical evidence."""
    from app.services.audit import record_audit_event

    rec = await pool.fetchrow(_PUBREC_SELECT, publication_id)
    if rec is None:
        raise SourceProcedureNotFound(publication_id)
    rec = dict(rec)
    if (rec.get("actor_subject") or "") != actor_subject:
        raise PublicationDenied(["actor did not create this publication"])

    published_row = rec.get("published_object_id")
    independent = 0
    if published_row:
        try:
            independent = int(await pool.fetchval(_INDEP_EVIDENCE, published_row) or 0)
        except Exception:  # noqa: BLE001 - stats view shape varies; treat as no independent evidence
            independent = 0

    dep_class = (rec.get("dependency_report") or {}).get("classification")
    if independent > 0:
        outcome = "RETAINED_AS_INDEPENDENTLY_SOURCED"
    elif dep_class in ("MIXED", "UNKNOWN"):
        outcome = "REQUIRES_REMEDIATION"
    else:
        outcome = "WITHDRAWN_FROM_RETRIEVAL"

    await pool.execute(
        "UPDATE publication_records SET withdrawal_state = $2, t_updated = now() WHERE id = $1::uuid",
        publication_id, outcome,
    )
    # WITHDRAWN_FROM_RETRIEVAL: hide the candidate from retrieval without
    # destroying it (tombstone, not physical delete) -- historical
    # evidence stays queryable. Real bug fix (found while hardening the
    # sibling Local -> Global path): 'withdrawn' is not a real
    # procedure_availability enum value (active|quarantined|disabled,
    # db/18_procedures.sql) -- this UPDATE would have raised
    # InvalidTextRepresentation the first time a real withdrawal with no
    # independent evidence ever ran. 'disabled' is the correct existing
    # value for "permanently excluded from retrieval" (applicability.py's
    # `_CANDIDATE_BASE_WHERE` excludes anything but 'active' identically
    # either way; 'disabled' is the semantically correct one -- distinct
    # from 'quarantined', which means "pending review", not "withdrawn").
    if outcome == "WITHDRAWN_FROM_RETRIEVAL" and published_row:
        await pool.execute(
            "UPDATE procedures SET availability = 'disabled' WHERE id = $1::uuid",
            published_row,
        )

    await record_audit_event(
        pool, actor_subject=actor_subject, action="publication_rejected",
        object_type="publication_record", object_id=publication_id,
        actor_user_id=actor_user_id,
        details={"withdrawal_state": outcome, "independent_evidence": independent},
    )
    return {"publication_id": publication_id, "withdrawal_state": outcome}


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)
