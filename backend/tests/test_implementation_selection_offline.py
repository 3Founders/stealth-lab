"""
DB-free coverage for implementation_selection.py's pure deterministic
pieces: evaluate_requirements' HARD_FALSE/SOFT/SATISFIABLE/UNKNOWN
classification and is_eligible's short-circuit rule. Mirrors the
established pattern (test_applicability_hard_constraints_offline.py) of
proving cascade decision logic without touching Postgres.
"""
from app.execution.implementation_selection import evaluate_requirements, is_eligible


def _impl(**overrides):
    row = {
        "id": "00000000-0000-4000-8000-000000000001",
        "status": "active",
        "execution_location": "stealth_hosted",
        "scope_type": None,
        "auth_requirements": {},
        "resource_requirements": {},
        "verification_status": "unverified",
    }
    row.update(overrides)
    return row


def test_inactive_status_is_hard_false():
    checks = evaluate_requirements(_impl(status="deprecated"), {})
    assert not is_eligible(checks)
    lifecycle = next(c for c in checks if c.name == "lifecycle")
    assert lifecycle.state == "HARD_FALSE"


def test_active_status_alone_is_eligible():
    checks = evaluate_requirements(_impl(), {})
    assert is_eligible(checks)


def test_execution_location_outside_allowed_set_is_hard_false():
    checks = evaluate_requirements(
        _impl(execution_location="third_party_hosted"),
        {"allowed_execution_locations": ["stealth_hosted", "user_hosted"]},
    )
    assert not is_eligible(checks)
    loc = next(c for c in checks if c.name == "execution_location")
    assert loc.state == "HARD_FALSE"


def test_missing_execution_location_declaration_is_unknown_not_false():
    checks = evaluate_requirements(
        _impl(execution_location=None),
        {"allowed_execution_locations": ["stealth_hosted"]},
    )
    assert is_eligible(checks)  # UNKNOWN never disqualifies
    loc = next(c for c in checks if c.name == "execution_location")
    assert loc.state == "UNKNOWN"


def test_privacy_policy_forbids_third_party_hosted():
    checks = evaluate_requirements(
        _impl(execution_location="third_party_hosted"),
        {"privacy_policy": "no_third_party"},
    )
    assert not is_eligible(checks)
    privacy = next(c for c in checks if c.name == "privacy")
    assert privacy.state == "HARD_FALSE"


def test_scope_mismatch_is_hard_false():
    checks = evaluate_requirements(
        _impl(scope_type="repository"),
        {"required_scope_type": "workspace"},
    )
    assert not is_eligible(checks)


def test_unscoped_implementation_never_conflicts_with_required_scope():
    checks = evaluate_requirements(_impl(scope_type=None), {"required_scope_type": "workspace"})
    assert is_eligible(checks)


def test_missing_credential_declaration_is_unknown():
    checks = evaluate_requirements(
        _impl(auth_requirements={"credentials": ["github_token"]}),
        {},  # caller never declared available_credentials at all
    )
    assert is_eligible(checks)
    auth = next(c for c in checks if c.name == "auth")
    assert auth.state == "UNKNOWN"


def test_declared_missing_credential_is_hard_false():
    checks = evaluate_requirements(
        _impl(auth_requirements={"credentials": ["github_token"]}),
        {"available_credentials": []},
    )
    assert not is_eligible(checks)
    auth = next(c for c in checks if c.name == "auth")
    assert auth.state == "HARD_FALSE"


def test_declared_available_credential_is_satisfiable():
    checks = evaluate_requirements(
        _impl(auth_requirements={"credentials": ["github_token"]}),
        {"available_credentials": ["github_token"]},
    )
    assert is_eligible(checks)
    auth = next(c for c in checks if c.name == "auth")
    assert auth.state == "SATISFIABLE"


def test_resource_mismatch_is_hard_false():
    checks = evaluate_requirements(
        _impl(resource_requirements={"gpu": True}),
        {"available_resources": {"gpu": False}},
    )
    assert not is_eligible(checks)


def test_unresolved_resource_requirement_is_unknown():
    checks = evaluate_requirements(
        _impl(resource_requirements={"gpu": True}),
        {"available_resources": {}},
    )
    assert is_eligible(checks)
    resources = next(c for c in checks if c.name == "resources")
    assert resources.state == "UNKNOWN"


def test_no_declared_available_resources_is_unknown_not_false():
    checks = evaluate_requirements(
        _impl(resource_requirements={"gpu": True}),
        {},
    )
    assert is_eligible(checks)
    resources = next(c for c in checks if c.name == "resources")
    assert resources.state == "UNKNOWN"
