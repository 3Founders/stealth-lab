"""
Execution-descriptor regression (final-V1 §1). Pure function over an
implementation-registry row shape; no DB.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.execution.implementation_registry import (
    DESCRIPTOR_VERSION,
    ImplementationRegistryError,
    _DESCRIPTOR_FIELDS,
    descriptor,
)


def _row(**over):
    base = {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "name": "Graphify.query_graph",
        "kind": "tool",
        "provider": "graphify",
        "version": 3,
        "status": "active",
        "verification_status": "verified",
        "locator": {"protocol": "mcp", "server": "graphify", "tool": "query_graph"},
        "invocation": {"tool": "query_graph", "args_schema_ref": "#/inv"},
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "output_schema": {"type": "object"},
        "requirements": {"network": True},
        "auth_requirements": {"scheme": "bearer", "credential_ref": "vault://graphify/token"},
        "resource_requirements": {"cpu": "0.5"},
    }
    base.update(over)
    return base


def test_stable_shape_and_deterministic_serialization():
    r = _row()
    d1, d2 = descriptor(r), descriptor(dict(reversed(list(r.items()))))
    assert list(d1.keys()) == list(_DESCRIPTOR_FIELDS)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert d1["descriptor_version"] == DESCRIPTOR_VERSION


def test_exact_identity_id_and_version():
    d = descriptor(_row())
    assert d["implementation_id"] == "11111111-1111-1111-1111-111111111111"
    assert d["version"] == 3 and d["kind"] == "tool" and d["provider"] == "graphify"


def test_newer_version_is_a_different_descriptor_bound_one_never_changes():
    v3 = descriptor(_row(version=3))
    v4 = descriptor(_row(version=4, id=uuid.UUID("22222222-2222-2222-2222-222222222222")))
    assert v3 != v4
    assert v3["version"] == 3 and v4["version"] == 4
    # re-serializing the SAME bound row is byte-identical (no drift)
    assert descriptor(_row(version=3)) == v3


def test_no_secret_leakage_inline_tokens_are_redacted():
    d = descriptor(_row(auth_requirements={
        "scheme": "bearer",
        "token": "sk-live-DEADBEEF",                     # inline secret -> redacted
        "client_secret": "hunter2",                       # inline secret -> redacted
        "credential_ref": "vault://x",                    # a reference -> kept
        "oauth": {"client_id": "abc", "client_secret": "shh"},  # nested secret -> redacted
    }))
    a = d["auth_requirements"]
    assert a["token"] == {"redacted": True}
    assert a["client_secret"] == {"redacted": True}
    assert a["credential_ref"] == "vault://x"
    assert a["oauth"]["client_id"] == "abc"
    assert a["oauth"]["client_secret"] == {"redacted": True}
    blob = json.dumps(d)
    assert "sk-live-DEADBEEF" not in blob and "hunter2" not in blob and "shh" not in blob


def test_missing_optional_fields_serialize_as_empty_objects():
    d = descriptor({"id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
                    "kind": "deterministic", "provider": "builtin", "version": 1})
    for k in ("locator", "invocation", "input_schema", "output_schema",
              "requirements", "auth_requirements", "resource_requirements"):
        assert d[k] == {}, k
    assert d["protocol"] == "native"  # derived from kind when no locator
    assert d["status"] is None and d["verification_status"] is None


def test_protocol_derivation_prefers_locator_then_kind():
    assert descriptor(_row(kind="api", locator={}))["protocol"] == "https"
    assert descriptor(_row(kind="wasm", locator={}))["protocol"] == "wasm"
    assert descriptor(_row(kind="frontier", locator={}, invocation={}))["protocol"] == "model"
    assert descriptor(_row(kind="tool", locator={"protocol": "rest"}))["protocol"] == "rest"


def test_unknown_kind_is_rejected():
    with pytest.raises(ImplementationRegistryError):
        descriptor(_row(kind="quantum_oracle"))
