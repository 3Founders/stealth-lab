"""Phase 5 (launch compliance) — data classification + provider policy.

Offline: a FakePool answers the model_provider_policies lookup. Proves
that classification is deterministic and that egress is gated by policy,
not by API-key presence.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.classification import DataClass, classify, classify_procedure_row, is_private
from app.services.provider_policy import (
    ProviderPolicyDenied,
    can_send,
    guard_send,
)


# ------------------------------------------------------- classification


def test_secret_marker_dominates_everything():
    assert classify(visibility="public", is_execution_secret=True) is DataClass.EXECUTION_SECRET


def test_private_visibility_is_user_private():
    assert classify(visibility="private") is DataClass.USER_PRIVATE


def test_org_visibility_is_org_private():
    assert classify(visibility="org") is DataClass.ORG_PRIVATE


def test_public_verified_is_global_procedure_else_public_derived():
    assert classify(visibility="public", verification_state="verified") is DataClass.GLOBAL_PROCEDURE
    assert classify(visibility="public", verification_state="candidate") is DataClass.PUBLIC_DERIVED


def test_private_source_class_beats_a_public_visibility_flag():
    assert classify(visibility="public", source_class="USER_PRIVATE") is DataClass.USER_PRIVATE


def test_unknown_visibility_fails_closed_to_private():
    assert classify(visibility=None) is DataClass.USER_PRIVATE


def test_classify_procedure_row():
    assert classify_procedure_row({"visibility": "private"}) is DataClass.USER_PRIVATE
    assert classify_procedure_row(
        {"visibility": "public", "verification_state": "verified"}
    ) is DataClass.GLOBAL_PROCEDURE


# ------------------------------------------------------- provider policy


class _PolicyPool:
    def __init__(self, row):
        self._row = row
        self.audits: list[tuple] = []

    async def fetchrow(self, sql, *params):
        flat = " ".join(sql.split())
        if "FROM model_provider_policies" in flat:
            return self._row
        if "INSERT INTO audit_events" in flat:
            self.audits.append(params)
            return {"id": 1}
        raise AssertionError(flat[:60])


def _policy_row(allowed):
    return {
        "id": "pol-1", "provider": "gemini", "model": "*", "region": "US/global",
        "allowed_data_classes": allowed, "policy_version": "seed-v1",
        "training_or_improvement_use": False, "dpa_available": False,
    }


def test_public_class_allowed_when_policy_lists_it():
    pool = _PolicyPool(_policy_row(["PUBLIC_DERIVED", "GLOBAL_PROCEDURE"]))
    d = asyncio.run(can_send(pool, data_classification=DataClass.PUBLIC_DERIVED,
                             provider="gemini", model="gemini:emb"))
    assert d.allowed is True
    assert d.policy_version == "seed-v1"
    assert d.region == "US/global"


def test_private_class_denied_and_audited():
    pool = _PolicyPool(_policy_row(["PUBLIC_DERIVED", "GLOBAL_PROCEDURE"]))
    d = asyncio.run(can_send(pool, data_classification=DataClass.USER_PRIVATE,
                             provider="gemini", model="gemini:emb"))
    assert d.allowed is False
    assert "does NOT permit" in d.reason
    assert pool.audits, "a denied provider call must be audited"


def test_no_effective_policy_row_fails_closed():
    pool = _PolicyPool(None)
    d = asyncio.run(can_send(pool, data_classification=DataClass.PUBLIC_DERIVED, provider="mystery"))
    assert d.allowed is False
    assert "fail closed" in d.reason


def test_guard_send_raises_on_deny():
    pool = _PolicyPool(_policy_row(["PUBLIC_DERIVED"]))
    with pytest.raises(ProviderPolicyDenied):
        asyncio.run(guard_send(pool, data_classification=DataClass.ORG_PRIVATE, provider="gemini"))


def test_embedder_gates_external_provider_on_policy():
    # The Embedder must refuse to send USER_PRIVATE text to an external
    # provider whose policy does not allow it, BEFORE any network call.
    from app.services.embeddings import Embedder

    pool = _PolicyPool(_policy_row(["PUBLIC_DERIVED", "GLOBAL_PROCEDURE"]))
    emb = Embedder(provider="gemini", data_classification=DataClass.USER_PRIVATE, policy_pool=pool)
    with pytest.raises(ProviderPolicyDenied):
        asyncio.run(emb._embed_configured_provider(["secret repo notes"], "document"))


def test_is_private_helper():
    assert is_private(DataClass.USER_PRIVATE)
    assert not is_private(DataClass.PUBLIC_DERIVED)
