"""ProviderPolicyService (launch compliance Phase 5 / LC-005, INV-07).

ONE decision point for "may this classified data leave its trust boundary
to this provider/model?". API-key existence is NOT authorization: a call
is permitted only if an EFFECTIVE `model_provider_policies` row for the
(provider, model) pair lists the data's classification in
`allowed_data_classes`.

Both allow and deny decisions are auditable. Deny is ALWAYS audited (it
is a security-relevant refusal). Allow is audited when a pool + actor are
supplied; the hot embedding path may pass `audit=False` and rely on the
aggregate provider-call telemetry instead.

Fail closed: no matching effective policy row  ->  DENY.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.classification import DataClass


class ProviderPolicyDenied(PermissionError):
    """A model/embedding call was refused by routing policy."""

    def __init__(self, decision: "PolicyDecision"):
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    provider: str
    model: str
    data_classification: str
    policy_version: Optional[str] = None
    region: Optional[str] = None
    allowed_classes: tuple[str, ...] = field(default_factory=tuple)
    policy_id: Optional[str] = None


# Most-specific effective row wins: a tenant-scoped row over a global one,
# an exact model over the '*' wildcard, the newest effective_from as the
# final tie-break. effective_until (when set) must be in the future.
_POLICY_SELECT = """
    SELECT id::text, provider, model, region, allowed_data_classes,
           policy_version, training_or_improvement_use, dpa_available
      FROM model_provider_policies
     WHERE provider = $1
       AND (model = $2 OR model = '*')
       AND effective_from <= now()
       AND (effective_until IS NULL OR effective_until > now())
       AND (tenant_id IS NULL OR $3::uuid = tenant_id)
     ORDER BY (tenant_id IS NOT NULL) DESC,
              (model <> '*') DESC,
              effective_from DESC
     LIMIT 1
"""


async def can_send(
    pool: Any,
    *,
    data_classification: DataClass | str,
    provider: str,
    model: str = "*",
    tenant_id: Optional[str] = None,
    audit: bool = False,
    actor_subject: Optional[str] = None,
) -> PolicyDecision:
    cls = data_classification.value if isinstance(data_classification, DataClass) else str(data_classification)
    row = await pool.fetchrow(_POLICY_SELECT, provider, model, tenant_id)

    if row is None:
        decision = PolicyDecision(
            allowed=False,
            reason=(
                f"no effective model-provider policy for {provider}/{model}; "
                f"egress of {cls} data is denied (fail closed)"
            ),
            provider=provider, model=model, data_classification=cls,
        )
    else:
        allowed_classes = tuple(row["allowed_data_classes"] or ())
        ok = cls in allowed_classes
        decision = PolicyDecision(
            allowed=ok,
            reason=(
                f"{provider}/{row['model']} policy {row['policy_version']} "
                + ("permits " if ok else "does NOT permit ")
                + f"{cls} (allowed: {', '.join(allowed_classes) or 'none'})"
            ),
            provider=provider, model=row["model"], data_classification=cls,
            policy_version=row["policy_version"], region=row["region"],
            allowed_classes=allowed_classes, policy_id=row["id"],
        )

    if (not decision.allowed) or audit:
        await _audit(pool, decision, actor_subject, tenant_id)
    return decision


async def guard_send(
    pool: Any,
    *,
    data_classification: DataClass | str,
    provider: str,
    model: str = "*",
    tenant_id: Optional[str] = None,
    actor_subject: Optional[str] = None,
) -> PolicyDecision:
    """can_send + raise ProviderPolicyDenied when not allowed."""
    decision = await can_send(
        pool, data_classification=data_classification, provider=provider,
        model=model, tenant_id=tenant_id, audit=True, actor_subject=actor_subject,
    )
    if not decision.allowed:
        raise ProviderPolicyDenied(decision)
    return decision


async def _audit(pool: Any, decision: PolicyDecision, actor_subject, tenant_id) -> None:
    try:
        from app.services.audit import record_audit_event

        await record_audit_event(
            pool,
            actor_subject=actor_subject or "system:provider_policy",
            action="provider_call_allowed" if decision.allowed else "provider_call_denied",
            object_type="model_provider",
            object_id=f"{decision.provider}/{decision.model}",
            tenant_id=tenant_id,
            details={
                "data_classification": decision.data_classification,
                "policy_version": decision.policy_version,
                "region": decision.region,
                "reason": decision.reason,
            },
        )
    except Exception:  # noqa: BLE001 - audit failure must not break an allowed call; deny already raised elsewhere
        import logging

        logging.getLogger(__name__).warning(
            "provider-policy audit write failed for %s/%s", decision.provider, decision.model
        )
