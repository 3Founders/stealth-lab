"""Fail-closed provider guard (V4-hardening Testing §T1 / audit K->T1).

WHY THIS EXISTS
    The substrate ships several "seams" where a real, durable, credentialed
    dependency can be swapped for a cheap stand-in: the embedding provider
    (real Voyage/Gemini vs. a localhost Ollama or a monkeypatched Fake*),
    the auth validator (real OIDC/JWKS vs. the pass-through no-op middleware
    that authenticates nobody), and durable storage (Postgres vs. an unset
    or sqlite/:memory: DATABASE_URL). Those stand-ins are legitimate under
    `environment == "TEST"` and nowhere else. The rule K->T1 ("NO SYNTHETIC
    FALLBACKS") says: starting STAGING or PRODUCTION on any of them MUST
    abort the process, loudly, at startup -- not warn, not degrade.

    This module is that check. `main.py` calls `assert_production_safe`
    once during lifespan startup, before the app serves a request.

SCOPE LIMITS (honest)
  - The guard introspects the RESOLVED provider by reading `settings`
    attributes -- it mirrors `Embedder._configured_provider()` rather than
    importing and constructing `Embedder`, so a plain `SimpleNamespace`
    stand-in is enough to unit-test every branch and importing this module
    has no side effects.
  - It additionally does a best-effort class-name check on
    `app.services.embeddings.Embedder`: a test (or a bad patch) that swaps
    the class for `FakeEmbedder` / `OfflineEmbedder` / `MockEmbedder` is
    caught even though `settings` looks clean. That check never raises.
  - It does NOT verify that a configured Postgres URL actually connects,
    that an API key is actually valid, or that a `--workers` flag matches
    `mcp_worker_count` (see tasks_extension's own docstring). Those are
    liveness checks; this is a configuration check.
  - There is no local "auth signing secret" in this codebase: OIDC tokens
    are verified against a remote JWKS, so the auth arm checks "OIDC is
    configured when auth is meant to be on", which is the equivalent
    missing-credential condition.
  - `MockAgent` (debate panel) and `StaticJwks` (authn) are test-only and
    not selectable from `settings`, so they are deliberately out of scope.

FAIL CLOSED
    `settings.environment` (app/config.py) already resolves an unset or
    unrecognised environment to PRODUCTION. This module treats anything
    that is not exactly "TEST" as "apply the strict checks". The TEST
    bypass is the single, total, intentional hole: under TEST the guard
    returns immediately and every fake is permitted.
"""
from __future__ import annotations

import re
from typing import Any

# Provider-name tokens that denote a non-durable / synthetic embedding path.
# "local" == a localhost Ollama/LM-Studio server: real inference, but the
# config docstring itself calls it "Not a production substitute", and T1
# forbids it outside TEST.
_FAKE_EMBED_PROVIDERS = frozenset({"offline", "fake", "stub", "mock", "local"})

# Class-name shapes that mean "this is a test double", per the T1 brief.
_FAKE_CLASS_RE = re.compile(r"^(Fake|Mock|Stub|Dummy)|Offline", re.IGNORECASE)

# Values of an explicit auth-provider setting that mean "not a real validator".
_FAKE_AUTH_PROVIDERS = frozenset({"fake", "mock", "stub", "dev", "noop", "none", "permissive", "insecure"})

# DATABASE_URL substrings that mean "not a durable external database".
_NON_DURABLE_DB_MARKERS = ("sqlite", ":memory:", "memory://")


class RuntimeGuardViolation(RuntimeError):
    """A fake/mock/in-memory dependency was found active outside TEST.

    Raised only by `assert_production_safe`. The message names every
    distinct violation so an operator can fix the config in one pass.
    """


def _environment(settings: Any) -> str:
    """The tri-state, upper-cased, from a real Settings OR a stand-in.

    Prefers `settings.environment`; falls back to `is_test` only when the
    object exposes no `environment` at all. Absent both, fail closed to
    PRODUCTION so a malformed stand-in gets the strict checks.
    """
    env = getattr(settings, "environment", None)
    if env:
        return str(env).strip().upper()
    if getattr(settings, "is_test", False):
        return "TEST"
    return "PRODUCTION"


def _resolved_embedding_provider(settings: Any) -> str:
    """Mirror of `Embedder._configured_provider()` over settings attributes.

    Order: an explicit one-off override, then the `use_local_models`
    switch, then the head of `embedding_provider_chain`. Lower-cased;
    "unknown" if nothing is configured.
    """
    override = (
        getattr(settings, "embedding_provider_override", None)
        or getattr(settings, "embedding_provider", None)
    )
    if override and str(override).strip():
        return str(override).strip().lower()
    if getattr(settings, "use_local_models", False):
        return "local"
    chain = getattr(settings, "embedding_provider_chain", "") or ""
    parts = [p.strip().lower() for p in str(chain).split(",") if p.strip()]
    return parts[0] if parts else "unknown"


def _embedder_class_is_fake() -> str | None:
    """Best-effort: is `embeddings.Embedder` currently a test double?

    Catches `monkeypatch.setattr(embeddings, "Embedder", FakeEmbedder)` --
    a swap `settings` cannot reveal. Never raises: introspection failure
    must not itself break startup.
    """
    try:
        from app.services import embeddings as _emb

        cls = getattr(_emb, "Embedder", None)
        name = getattr(cls, "__name__", "")
        if name and name != "Embedder" and _FAKE_CLASS_RE.search(name):
            return f"embeddings.Embedder is bound to test double {name!r}"
    except Exception:  # noqa: BLE001 - introspection is advisory, never fatal
        return None
    return None


def _auth_is_permissive(settings: Any) -> str | None:
    """Does the auth validator resolve to a dev/no-op/permissive one?

    Two ways that happens:
      - an explicit `auth_provider` / `auth_validator` setting names a fake;
      - auth is declared ON (`real_auth_enabled` or
        `multi_user_exposure_enabled`) but OIDC is not configured, so
        `make_actor_middleware` installs the pass-through that
        authenticates nobody.
    """
    for attr in ("auth_provider", "auth_validator", "auth_backend"):
        val = getattr(settings, attr, None)
        if val and str(val).strip().lower() in _FAKE_AUTH_PROVIDERS:
            return f"auth setting {attr}={val!r} selects a permissive/no-op validator"

    auth_intended = bool(
        getattr(settings, "real_auth_enabled", False)
        or getattr(settings, "multi_user_exposure_enabled", False)
        or getattr(settings, "private_visibility_enabled", False)
    )
    if not auth_intended:
        return None
    try:
        from app.services.authn import oidc_configured

        if not oidc_configured(settings):
            return (
                "auth is enabled (real_auth_enabled / multi_user_exposure_enabled) "
                "but OIDC is not configured -- the actor middleware would run as a "
                "pass-through that authenticates nobody"
            )
    except Exception:  # noqa: BLE001 - if authn cannot even be imported, say so
        return "could not resolve the auth configuration (app.services.authn import failed)"
    return None


def _database_url(settings: Any) -> str:
    return str(getattr(settings, "database_url", None) or "").strip()


def is_production_safe(settings: Any) -> tuple[bool, list[str]]:
    """Report whether `settings` is safe to serve outside TEST.

    Returns ``(ok, reasons)``. ``ok`` is True with an empty list either
    when the environment is TEST (every fake permitted -- the one
    intentional bypass) or when no synthetic dependency is active. Each
    string in ``reasons`` names one distinct problem.

    Pure: no exceptions for a caller who wants the list rather than a
    raise. `assert_production_safe` is the raising wrapper.
    """
    env = _environment(settings)
    if env == "TEST":
        return True, []

    reasons: list[str] = []

    # --- embedding provider -------------------------------------------------
    provider = _resolved_embedding_provider(settings)
    if provider in _FAKE_EMBED_PROVIDERS:
        reasons.append(
            f"embedding provider resolves to {provider!r}, a non-durable/synthetic "
            f"path forbidden outside TEST (env={env})"
        )
    fake_cls = _embedder_class_is_fake()
    if fake_cls:
        reasons.append(f"{fake_cls} (env={env})")

    # --- auth validator ---------------------------------------------------
    auth_problem = _auth_is_permissive(settings)
    if auth_problem:
        reasons.append(f"{auth_problem} (env={env})")

    # --- durable storage + its credential -------------------------------
    db_url = _database_url(settings)
    if not db_url:
        reasons.append(
            f"DATABASE_URL is unset -- required durable storage has no backing "
            f"database (env={env})"
        )
    elif any(marker in db_url.lower() for marker in _NON_DURABLE_DB_MARKERS):
        reasons.append(
            f"DATABASE_URL points at an in-memory/sqlite substitute for durable "
            f"storage (env={env})"
        )

    # --- mandatory provider credential (only meaningful for a real provider) ---
    if provider not in _FAKE_EMBED_PROVIDERS:
        missing_key = _missing_embedding_credential(settings, provider)
        if missing_key:
            reasons.append(f"{missing_key} (env={env})")

    return (not reasons), reasons


def _missing_embedding_credential(settings: Any, provider: str) -> str | None:
    """Which API key the resolved real embedding provider needs, if absent."""
    if provider == "gemini":
        if not (getattr(settings, "gemini_api_key", None) or getattr(settings, "gemini_api_keys", None)):
            return "embedding provider 'gemini' is selected but GEMINI_API_KEY / GEMINI_API_KEYS is unset"
    elif provider == "voyage":
        if not getattr(settings, "voyage_api_key", None):
            return "embedding provider 'voyage' is selected but VOYAGE_API_KEY is unset"
    return None


def assert_production_safe(settings: Any) -> None:
    """Raise `RuntimeGuardViolation` unless `settings` is safe to serve.

    No-op under `environment == "TEST"`. Called once from `main.py`'s
    lifespan startup; a raise there aborts the process before it serves.
    """
    ok, reasons = is_production_safe(settings)
    if ok:
        return
    env = _environment(settings)
    raise RuntimeGuardViolation(
        f"refusing to start in environment {env}: "
        + "; ".join(reasons)
        + ". Set STEALTHLAB_ENV=TEST only for tests, or provide the real "
        "provider/credentials for this environment (rule K->T1, NO SYNTHETIC FALLBACKS)."
    )
