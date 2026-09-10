"""Offline proof of the fail-closed provider guard (V4-hardening T1 / K->T1).

Runs with DATABASE_URL unset. Uses a lightweight `SimpleNamespace` for
`settings` -- the guard duck-types every attribute it reads, so the real
pydantic Settings is never needed to exercise a branch.

What is pinned here:
  - a fully-configured PRODUCTION-shaped settings object is reported safe;
  - each synthetic dependency (offline embedder, missing DB URL, missing
    embedding key, fake auth validator) is reported unsafe, by name;
  - `assert_production_safe` raises `RuntimeGuardViolation` for each, with
    the reason in the message;
  - under environment == "TEST" the guard is a total no-op even with every
    fake active (the one intentional bypass);
  - THE T1 PROVING TEST: an object pinned to environment == "PRODUCTION"
    has no code path back to a test adapter -- a fake embedder swapped in
    afterwards still yields (False, ...), it cannot hide.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.runtime_guard import (
    RuntimeGuardViolation,
    assert_production_safe,
    is_production_safe,
)


def _production_settings(**overrides) -> SimpleNamespace:
    """A PRODUCTION-shaped settings stand-in with every real dependency
    present. Overrides punch individual holes in it."""
    base = dict(
        environment="PRODUCTION",
        # real embedding provider + its credential
        embedding_provider_chain="gemini,voyage",
        use_local_models=False,
        gemini_api_key="real-gemini-key",
        gemini_api_keys=None,
        voyage_api_key="real-voyage-key",
        # real durable storage
        database_url="postgresql://user:pw@db.example.com:5432/stealth",
        # auth: not intended on -> single-tenant public commons, allowed
        real_auth_enabled=False,
        multi_user_exposure_enabled=False,
        private_visibility_enabled=False,
        auth_provider=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


def test_fully_configured_production_is_safe():
    ok, reasons = is_production_safe(_production_settings())
    assert ok is True
    assert reasons == []


def test_assert_production_safe_returns_none_when_safe():
    assert assert_production_safe(_production_settings()) is None


# --------------------------------------------------------------------------
# each violation, reported by name
# --------------------------------------------------------------------------


def test_production_plus_offline_embedder_is_unsafe():
    ok, reasons = is_production_safe(
        _production_settings(embedding_provider_chain="offline")
    )
    assert ok is False
    assert any("embedding provider" in r and "offline" in r for r in reasons)


def test_production_plus_local_embedder_is_unsafe():
    ok, reasons = is_production_safe(_production_settings(use_local_models=True))
    assert ok is False
    assert any("embedding provider" in r and "local" in r for r in reasons)


def test_production_missing_database_url_is_unsafe():
    ok, reasons = is_production_safe(_production_settings(database_url=None))
    assert ok is False
    assert any("DATABASE_URL is unset" in r for r in reasons)


def test_production_sqlite_database_url_is_unsafe():
    ok, reasons = is_production_safe(
        _production_settings(database_url="sqlite:///./local.db")
    )
    assert ok is False
    assert any("in-memory/sqlite substitute" in r for r in reasons)


def test_production_missing_embedding_key_is_unsafe():
    ok, reasons = is_production_safe(
        _production_settings(gemini_api_key=None, gemini_api_keys=None)
    )
    assert ok is False
    assert any("gemini" in r and "unset" in r for r in reasons)


def test_production_voyage_head_missing_key_is_unsafe():
    ok, reasons = is_production_safe(
        _production_settings(embedding_provider_chain="voyage", voyage_api_key=None)
    )
    assert ok is False
    assert any("voyage" in r and "VOYAGE_API_KEY" in r for r in reasons)


def test_staging_plus_fake_auth_validator_is_unsafe():
    ok, reasons = is_production_safe(
        _production_settings(environment="STAGING", auth_provider="fake")
    )
    assert ok is False
    assert any("permissive/no-op validator" in r for r in reasons)


def test_staging_auth_enabled_without_oidc_is_unsafe():
    ok, reasons = is_production_safe(
        _production_settings(
            environment="STAGING",
            real_auth_enabled=True,
            oidc_issuer=None,
            oidc_audience=None,
            supabase_project_url=None,
            supabase_jwt_audience=None,
        )
    )
    assert ok is False
    assert any("OIDC is not configured" in r for r in reasons)


def test_multiple_violations_are_all_listed():
    ok, reasons = is_production_safe(
        _production_settings(
            embedding_provider_chain="offline",
            database_url=None,
        )
    )
    assert ok is False
    assert len(reasons) >= 2


# --------------------------------------------------------------------------
# assert_production_safe raises, with the reason in the message
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"embedding_provider_chain": "offline"}, "offline"),
        ({"database_url": None}, "DATABASE_URL is unset"),
        ({"gemini_api_key": None, "gemini_api_keys": None}, "gemini"),
        ({"environment": "STAGING", "auth_provider": "fake"}, "permissive/no-op"),
    ],
)
def test_assert_production_safe_raises_with_reason(overrides, needle):
    with pytest.raises(RuntimeGuardViolation) as excinfo:
        assert_production_safe(_production_settings(**overrides))
    assert needle in str(excinfo.value)


def test_violation_is_a_runtime_error_subclass():
    assert issubclass(RuntimeGuardViolation, RuntimeError)


# --------------------------------------------------------------------------
# the TEST bypass is total and intentional
# --------------------------------------------------------------------------


def test_test_environment_permits_every_fake():
    every_fake = _production_settings(
        environment="TEST",
        embedding_provider_chain="offline",
        use_local_models=True,
        gemini_api_key=None,
        gemini_api_keys=None,
        voyage_api_key=None,
        database_url=None,
        real_auth_enabled=True,
        auth_provider="noop",
        oidc_issuer=None,
        oidc_audience=None,
    )
    ok, reasons = is_production_safe(every_fake)
    assert ok is True
    assert reasons == []
    assert assert_production_safe(every_fake) is None


def test_test_environment_via_is_test_flag_only():
    # No `environment` attribute at all -> fall back to is_test.
    ns = SimpleNamespace(is_test=True, database_url=None, embedding_provider_chain="offline")
    ok, reasons = is_production_safe(ns)
    assert ok is True and reasons == []


# --------------------------------------------------------------------------
# T1 proving test: PRODUCTION cannot be flipped back to a test adapter
# --------------------------------------------------------------------------


def test_t1_production_config_cannot_activate_a_test_adapter(monkeypatch):
    """A settings object pinned to PRODUCTION stays strict: nothing flips
    it back to TEST, and a fake embedder introduced AFTER construction is
    still caught. The fake cannot hide behind a production-shaped config."""
    settings = _production_settings()

    # Baseline: production config with real providers is safe.
    assert is_production_safe(settings) == (True, [])

    # 1. A fake provider name slipped onto the (still PRODUCTION) settings
    #    is detected on re-derivation -- environment did not move.
    settings.embedding_provider = "fake"
    ok, reasons = is_production_safe(settings)
    assert settings.environment == "PRODUCTION"
    assert ok is False
    assert any("fake" in r for r in reasons)

    # 2. Swapping the Embedder class itself for a test double is caught by
    #    the class-name check even when every settings attribute is clean.
    settings2 = _production_settings()
    from app.services import embeddings as emb_module

    class FakeEmbedder:  # noqa: D401 - stand-in
        pass

    monkeypatch.setattr(emb_module, "Embedder", FakeEmbedder)
    ok2, reasons2 = is_production_safe(settings2)
    assert ok2 is False
    assert any("test double" in r for r in reasons2)

    # 3. There is no environment value outside the tri-state that reads as
    #    permissive: a garbled one is treated as PRODUCTION-strict.
    settings3 = _production_settings(environment="not-a-real-env", database_url=None)
    ok3, reasons3 = is_production_safe(settings3)
    assert ok3 is False
