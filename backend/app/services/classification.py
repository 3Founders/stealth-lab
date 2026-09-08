"""ClassificationService (launch compliance Phase 5 / LC-004, data-flow §15).

ONE canonical data-classification vocabulary. Classification drives
storage, retrieval, publication, provider/model egress, export, deletion,
and audit policy. This module owns the vocabulary and the deterministic
mapping from what the substrate already records (visibility, scope_type,
source class, secret markers) onto a class. It does not itself gate
anything -- ProviderPolicyService, PublicationService, DeletionService and
ExportService consume the class it produces.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional


class DataClass(str, Enum):
    PUBLIC_SOURCE = "PUBLIC_SOURCE"          # a fetched public web/repo/doc/dataset source
    PUBLIC_DERIVED = "PUBLIC_DERIVED"        # StealthLab-derived from public sources
    GLOBAL_PROCEDURE = "GLOBAL_PROCEDURE"    # a published Global Commons procedure
    ORG_PRIVATE = "ORG_PRIVATE"              # organization-scoped private knowledge
    USER_PRIVATE = "USER_PRIVATE"            # a single user's private knowledge
    EXECUTION_SECRET = "EXECUTION_SECRET"    # credentials/tokens in an execution descriptor
    PERSONAL_DATA = "PERSONAL_DATA"          # identifiable personal data
    CONFIDENTIAL_DATA = "CONFIDENTIAL_DATA"  # proprietary/confidential business material
    SECURITY_DATA = "SECURITY_DATA"          # security findings, incident detail
    AUDIT_DATA = "AUDIT_DATA"                # audit_events rows and derivatives


# Classes that must never leave the trust boundary without an explicit,
# tenant-level policy opt-in. ProviderPolicyService enforces this via the
# per-provider allowed_data_classes whitelist; this set is the
# fail-closed default used when no policy row exists at all.
PRIVATE_CLASSES = frozenset({
    DataClass.ORG_PRIVATE,
    DataClass.USER_PRIVATE,
    DataClass.EXECUTION_SECRET,
    DataClass.PERSONAL_DATA,
    DataClass.CONFIDENTIAL_DATA,
    DataClass.SECURITY_DATA,
    DataClass.AUDIT_DATA,
})

PUBLIC_CLASSES = frozenset({
    DataClass.PUBLIC_SOURCE,
    DataClass.PUBLIC_DERIVED,
    DataClass.GLOBAL_PROCEDURE,
})


# source-class (data-flow §5) -> the class its derived text carries
_SOURCE_CLASS_MAP = {
    "USER_PRIVATE": DataClass.USER_PRIVATE,
    "ORG_PRIVATE": DataClass.ORG_PRIVATE,
    "PUBLIC_WEB": DataClass.PUBLIC_SOURCE,
    "PUBLIC_REPOSITORY": DataClass.PUBLIC_SOURCE,
    "PUBLIC_DOCUMENTATION": DataClass.PUBLIC_SOURCE,
    "PUBLIC_DATASET": DataClass.PUBLIC_SOURCE,
    "STEALTHLAB_GENERATED": DataClass.PUBLIC_DERIVED,
    "STEALTHLAB_EXECUTION": DataClass.EXECUTION_SECRET,
    "THIRD_PARTY_PROVIDER": DataClass.PUBLIC_DERIVED,
}


def classify(
    *,
    visibility: Optional[str] = None,
    verification_state: Optional[str] = None,
    scope_type: Optional[str] = None,
    source_class: Optional[str] = None,
    is_execution_secret: bool = False,
    is_personal: bool = False,
    is_confidential: bool = False,
) -> DataClass:
    """Deterministic classification. Most-restrictive wins.

    The order matters: an explicit secret/personal/confidential marker
    dominates; then visibility (`private`/`org`); then a public procedure
    that has been verified is GLOBAL_PROCEDURE; otherwise PUBLIC_DERIVED.
    """
    if is_execution_secret:
        return DataClass.EXECUTION_SECRET
    if is_personal:
        return DataClass.PERSONAL_DATA
    if is_confidential:
        return DataClass.CONFIDENTIAL_DATA

    v = (visibility or "").lower()
    if v == "private":
        return DataClass.USER_PRIVATE
    if v in ("org", "organization"):
        return DataClass.ORG_PRIVATE

    if source_class and source_class in _SOURCE_CLASS_MAP:
        mapped = _SOURCE_CLASS_MAP[source_class]
        # a private source class always wins over a 'public' visibility flag
        if mapped in PRIVATE_CLASSES:
            return mapped

    if v == "public":
        if (verification_state or "").lower() == "verified":
            return DataClass.GLOBAL_PROCEDURE
        return DataClass.PUBLIC_DERIVED

    # Unknown / unset visibility: fail closed to the most restrictive
    # non-secret private class rather than assume public.
    return DataClass.USER_PRIVATE


def classify_procedure_row(row: Mapping[str, Any]) -> DataClass:
    """Classify a `procedures` row (asyncpg Record or dict)."""
    dp = row.get("domain_payload") or {}
    src = None
    if isinstance(dp, Mapping):
        src = (dp.get("source_provenance") or {}).get("source_class") or dp.get("source_class")
    return classify(
        visibility=row.get("visibility"),
        verification_state=row.get("verification_state"),
        scope_type=row.get("scope_type"),
        source_class=src,
    )


def is_private(cls: DataClass) -> bool:
    return cls in PRIVATE_CLASSES
