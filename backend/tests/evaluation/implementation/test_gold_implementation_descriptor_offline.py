"""
This suite's own gold-labeled layer over the Implementation execution
descriptor (task spec §8), extending -- not duplicating --
tests/test_implementation_descriptor_offline.py's 7 existing product-level
assertions. `descriptor()` is a pure function (no DB), per its own
docstring, so this whole phase is offline.

Real-entrypoint discipline: every case below calls
app.execution.implementation_registry.descriptor() directly -- the exact
production function, never a re-implementation of its rules.

No JSON gold-fixture file here (unlike gold_applicability/gold_retrieval):
inputs are nested dicts with UUID-shaped ids that don't serialize cleanly
to JSON gold cases without losing readability, so this follows the same
plain-Python-parametrize convention
tests/evaluation/durable/test_gold_durable_offline.py already established
for a policy/pure-function gold layer over a real production function.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.execution.implementation_registry import (
    DESCRIPTOR_VERSION,
    ImplementationRegistryError,
    REGISTRABLE_KINDS,
    _DESCRIPTOR_FIELDS,
    descriptor,
)


def _row(**over):
    base = {
        "id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        "name": "gold.impl_probe",
        "kind": "tool",
        "provider": "gold-provider",
        "version": 1,
        "status": "active",
        "verification_status": "verified",
        "locator": {"server": "gold", "tool": "probe"},
        "invocation": {"tool": "probe"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "requirements": {},
        "auth_requirements": {"credential_ref": "vault://gold/probe"},
        "resource_requirements": {},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Deterministic serialization + stable field set/order
# ---------------------------------------------------------------------------
def test_deterministic_serialization_is_independent_of_input_key_order():
    r = _row()
    shuffled = dict(reversed(list(r.items())))
    d1, d2 = descriptor(r), descriptor(shuffled)
    assert d1 == d2
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)


def test_stable_field_set_and_order_matches_the_declared_schema():
    d = descriptor(_row())
    assert list(d.keys()) == list(_DESCRIPTOR_FIELDS)
    assert d["descriptor_version"] == DESCRIPTOR_VERSION


# ---------------------------------------------------------------------------
# Exact implementation/version identity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "impl_id,version,expect_id,expect_version",
    [
        (uuid.UUID("11111111-1111-1111-1111-111111111111"), 1,
         "11111111-1111-1111-1111-111111111111", 1),
        (uuid.UUID("22222222-2222-2222-2222-222222222222"), 7,
         "22222222-2222-2222-2222-222222222222", 7),
        # a plain string id (as e.g. a REST/MCP boundary might already have
        # stringified it) is accepted identically to a UUID object.
        ("33333333-3333-3333-3333-333333333333", 2,
         "33333333-3333-3333-3333-333333333333", 2),
    ],
    ids=["v1", "v7", "string-id"],
)
def test_gold_exact_identity_matrix(impl_id, version, expect_id, expect_version):
    d = descriptor(_row(id=impl_id, version=version))
    assert d["implementation_id"] == expect_id
    assert d["version"] == expect_version


def test_two_versions_of_the_same_name_are_two_distinct_descriptors():
    v1 = descriptor(_row(id=uuid.UUID("11111111-1111-1111-1111-111111111111"), version=1))
    v2 = descriptor(_row(id=uuid.UUID("22222222-2222-2222-2222-222222222222"), version=2))
    assert v1 != v2 and v1["version"] != v2["version"]
    # re-deriving the descriptor of the SAME already-bound row is byte-identical
    assert descriptor(_row(id=uuid.UUID("11111111-1111-1111-1111-111111111111"), version=1)) == v1


# ---------------------------------------------------------------------------
# Optional field behavior
# ---------------------------------------------------------------------------
def test_gold_missing_optionals_default_to_empty_object_not_none_or_missing_key():
    minimal = {"id": uuid.uuid4(), "kind": "human", "provider": "gold", "version": 1}
    d = descriptor(minimal)
    for k in ("locator", "invocation", "input_schema", "output_schema",
              "requirements", "auth_requirements", "resource_requirements"):
        assert k in d and d[k] == {}
    assert d["status"] is None and d["verification_status"] is None


# ---------------------------------------------------------------------------
# Provider/kind/protocol representation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind,locator,invocation,expected_protocol",
    [
        ("frontier", {}, {}, "model"),
        ("slm", {}, {}, "model"),
        ("deterministic", {}, {}, "native"),
        ("human", {}, {}, "human"),
        ("tool", {}, {}, "mcp"),
        ("api", {}, {}, "https"),
        ("wasm", {}, {}, "wasm"),
        ("computer_use", {}, {}, "computer_use"),
        # an explicit locator/invocation field wins over the kind-based default
        ("tool", {"protocol": "rest"}, {}, "rest"),
        ("frontier", {}, {"transport": "grpc"}, "grpc"),
    ],
    ids=["frontier", "slm", "deterministic", "human", "tool", "api", "wasm",
         "computer_use", "locator-overrides-kind", "invocation-overrides-kind"],
)
def test_gold_protocol_derivation_matrix(kind, locator, invocation, expected_protocol):
    assert descriptor(_row(kind=kind, locator=locator, invocation=invocation))["protocol"] == expected_protocol


def test_every_registrable_kind_produces_a_descriptor_without_error():
    # REGISTRABLE_KINDS is the exact CHECK-constraint vocabulary (migration
    # 33's `implementations_kind_chk`) -- every one of them must be a legal
    # descriptor kind, not just the ones providers.py currently executes.
    for kind in REGISTRABLE_KINDS:
        d = descriptor(_row(kind=kind))
        assert d["kind"] == kind


def test_gold_kind_outside_the_registrable_vocab_is_rejected_not_silently_passed_through():
    with pytest.raises(ImplementationRegistryError):
        descriptor(_row(kind="made_up_kind_nobody_registered"))


# ---------------------------------------------------------------------------
# Secret redaction matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "auth_in,redacted_keys,kept_as_is",
    [
        ({"token": "sk-live-XYZ"}, ["token"], {}),
        ({"password": "hunter2"}, ["password"], {}),
        ({"api_key": "ak_live_123"}, ["api_key"], {}),
        ({"client_secret": "shh"}, ["client_secret"], {}),
        ({"bearer": "abc.def.ghi"}, ["bearer"], {}),
        ({"credential_ref": "vault://x/y"}, [], {"credential_ref": "vault://x/y"}),
        ({"scheme": "bearer", "scopes": ["read"]}, [], {"scheme": "bearer", "scopes": ["read"]}),
        # a *_ref-suffixed key is a reference, not a secret, even though its
        # name contains a secret-ish substring -- kept as-is, top level only
        # (see the dedicated nested-asymmetry case below).
        ({"token_ref": "vault://token-pointer"}, [], {"token_ref": "vault://token-pointer"}),
        # empty-string secret value: nothing to redact (no material present)
        ({"token": ""}, [], {"token": ""}),
    ],
    ids=["token", "password", "api_key", "client_secret", "bearer",
         "credential_ref-kept", "non-secret-keys-kept",
         "top-level-_ref-suffix-kept", "empty-string-not-redacted"],
)
def test_gold_secret_redaction_matrix(auth_in, redacted_keys, kept_as_is):
    d = descriptor(_row(auth_requirements=auth_in))
    a = d["auth_requirements"]
    for k in redacted_keys:
        assert a[k] == {"redacted": True}, f"{k} should have been redacted, got {a[k]!r}"
    for k, v in kept_as_is.items():
        assert a[k] == v, f"{k} should have been kept as {v!r}, got {a[k]!r}"


def test_gold_nested_secret_is_redacted_one_level_deep():
    d = descriptor(_row(auth_requirements={
        "oauth": {"client_id": "abc", "client_secret": "shh"},
    }))
    oauth = d["auth_requirements"]["oauth"]
    assert oauth["client_id"] == "abc"
    assert oauth["client_secret"] == {"redacted": True}


def test_nested_ref_suffixed_key_is_over_redacted_documented_asymmetry_not_a_leak():
    """Real, verified behavior (not a typo in this gold case): the one-level
    recursion in _sanitize_auth does NOT re-apply the top-level "a key
    ending in _ref is a reference, never a secret" exception -- so a NESTED
    key like oauth.token_ref gets redacted even though an identically-named
    TOP-LEVEL token_ref would be kept as-is (see
    test_gold_secret_redaction_matrix's "top-level-_ref-suffix-kept" case).
    This is over-conservative (a real reference value is hidden), not a
    secret leak, so it is documented here as a known asymmetry rather than
    something this evaluation pass fixes."""
    d = descriptor(_row(auth_requirements={
        "oauth": {"client_id": "abc", "token_ref": "vault://nested-token-ref"},
    }))
    assert d["auth_requirements"]["oauth"]["token_ref"] == {"redacted": True}


# ---------------------------------------------------------------------------
# credential_ref preservation + no secret material leakage anywhere
# ---------------------------------------------------------------------------
def test_gold_credential_ref_survives_alongside_other_redactions_untouched():
    d = descriptor(_row(auth_requirements={
        "credential_ref": "vault://gold/probe/v2",
        "token": "sk-should-not-survive",
    }))
    a = d["auth_requirements"]
    assert a["credential_ref"] == "vault://gold/probe/v2"
    assert a["token"] == {"redacted": True}


def test_gold_no_secret_material_appears_anywhere_in_the_serialized_descriptor():
    secret_values = ["sk-live-DEADBEEF", "hunter2", "shh-nested-secret", "ak_live_QWERTY"]
    d = descriptor(_row(auth_requirements={
        "token": secret_values[0],
        "password": secret_values[1],
        "oauth": {"client_secret": secret_values[2]},
        "api_key": secret_values[3],
        "credential_ref": "vault://gold/probe",  # the one thing allowed to survive
    }))
    blob = json.dumps(d)
    for secret in secret_values:
        assert secret not in blob, f"secret material {secret!r} leaked into the descriptor"
    assert "vault://gold/probe" in blob  # the reference itself is not secret material


# ---------------------------------------------------------------------------
# Newer Implementation does not silently change an already-bound descriptor:
# already proven at the real execution-plumbing level by
# tests/evaluation/durable/test_gold_durable_e2e.py::
# test_implementation_binding_survives_resume_unchanged_despite_a_newer_registration
# (a plan node's implementation_id stays pinned to the version that existed
# when the plan was compiled, verified by rebuilding the CompiledPlan from
# persisted rows after a newer version was registered) and at the pure
# descriptor level by test_two_versions_of_the_same_name_are_two_distinct_
# descriptors above (a version bump is necessarily a different id/version,
# hence a different descriptor -- there is no code path in descriptor()
# that could mutate a previously-returned dict in place). No new e2e case
# is added here for this specific claim; both existing proofs already cover
# it from the two angles that matter (real binding persistence, and
# descriptor purity) without duplicating either.
# ---------------------------------------------------------------------------
