"""
Per-request dependencies (V2).

Resolves the viewer identity for a request into an AccessScope. Real
authentication (Tier 1) is not built yet, so identity currently comes
from a header — which is fine for a public commons where nothing is
private, and is *not* fine the moment private visibility is enabled.

The header is trusted, and that is a deliberate, temporary shortcut, not
an oversight. It is safe only because every node is currently public, so
a forged identity grants nothing that isn't already world-readable. The
check in `require_trustworthy_identity` exists so that turning on private
mode without real auth fails loudly instead of silently exposing private
content to anyone who sets a header.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from fastapi import Depends, Header, HTTPException, Request

from app.config import settings
from app.services.access import AccessScope
from app.services.auth_context import AnonymousContext, AuthContext, Principal, resolve_auth_context
from app.services.authorization import AuthorizationDenied
from app.services.authorization import require_scopes as _check_scopes
from app.services.governance import (
    BudgetExceeded,
    CostGovernor,
    RateLimiter,
    RateLimitExceeded,
)


def auth_enforced() -> bool:
    """True whenever credentials are actually required: any non-TEST process,
    or a TEST process with real identity (OIDC / service tokens) configured.
    The only unenforced posture is TEST + no identity provider -- the offline
    suite's public-commons mode -- and runtime_guard refuses to boot it anywhere
    else."""
    from app.services.authn import oidc_configured

    if not settings.is_test:
        return True
    return oidc_configured(settings) or bool(getattr(settings, "service_token_keys", None))


async def _human_context(request: Request, actor: Any, *, strict_tenant: bool = False) -> AuthContext:
    """Actor -> AuthContext, resolved at most once per request (cached on
    request.state when the request object supports it)."""
    state = getattr(request, "state", None)
    cached = getattr(state, "auth_context", None) if state is not None else None
    if cached is not None and cached.subject == actor.subject and not (strict_tenant and len(cached.org_ids) > 1):
        return cached
    ctx = await resolve_auth_context(request.app.state.pool, actor, strict_tenant=strict_tenant)
    if state is not None and not strict_tenant:
        try:
            state.auth_context = ctx
        except Exception:  # noqa: BLE001 - caching is an optimisation only
            pass
    return ctx


async def get_auth_context(request: Request) -> Principal:
    """THE per-request identity resolver. Returns a ServiceAuthContext (verified
    worker credential), an AuthContext (verified human; memberships and roles
    resolved from the database now, not cached across requests) or an
    AnonymousContext. Deactivated accounts are 403, never a degraded scope."""
    import logging

    from app.services.authn import IdentityInactive, current_actor, current_service

    svc = current_service()
    if svc is not None:
        return svc
    actor = current_actor()
    if actor is None:
        return AnonymousContext()
    try:
        ctx = await _human_context(request, actor)
        if ctx.break_glass and not getattr(request.state, "_bg_audited", False):
            from app.services.audit import record_security_event

            request.state._bg_audited = True
            await record_security_event(request.app.state.pool, actor_subject=ctx.subject, action="break_glass.used",
                                        object_type="endpoint", object_id=f"{request.method} {request.url.path}"[:200])
        return ctx
    except IdentityInactive as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).error("identity resolution failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="identity resolution unavailable") from exc


async def get_scope(request: Request, x_viewer_id: Optional[str] = Header(default=None)) -> AccessScope:
    """
    The visibility scope for this request (read path). Anonymous by default --
    the normal case on a public commons, not a failure.

    * a verified SERVICE credential -> public rows only (private data is
      reachable by a worker only through its job authority, authorization.py);
    * a verified human -> AuthContext.access_scope(): public + own + their
      organizations' org rows;
    * a deactivated account -> 403 (a valid JWT proves who, not that they may
      still act -- this used to degrade to an owner scope; it no longer does);
    * a TRANSIENT resolution failure -> owner-only scope (strictly narrower than
      the real one, logged), so a DB hiccup neither locks users out of their
      own rows nor widens anything;
    * X-Viewer-Id is honoured only in the fully-public TEST posture where no
      identity provider is configured.
    """
    import logging

    from app.services.authn import IdentityInactive, current_actor, current_service, oidc_configured

    svc = current_service()
    if svc is not None:
        return svc.access_scope()
    actor = current_actor()
    if actor is not None:
        try:
            return (await _human_context(request, actor)).access_scope()
        except IdentityInactive as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("membership resolution failed (%s); owner-only scope", type(exc).__name__)
            return AccessScope.for_user(actor.subject)
    if x_viewer_id and not oidc_configured(settings) and settings.is_test:
        return AccessScope.for_user(x_viewer_id)
    return AccessScope.anonymous()


def require_trustworthy_identity() -> None:
    """
    Guard against the dangerous combination: private content enabled
    while identity is still header-asserted.

    Called at startup rather than per-request so the failure is immediate
    and obvious, instead of surfacing as a quiet data leak in production.
    """
    if settings.private_visibility_enabled and not settings.real_auth_enabled:
        raise RuntimeError(
            "private_visibility_enabled is on, but real_auth_enabled is off. "
            "Viewer identity currently comes from an unverified X-Viewer-Id "
            "header, so anyone could read any private content by setting it. "
            "Build real authentication before enabling private visibility."
        )


# ---------------------------------------------------------------------------
# Phase 1 (launch compliance): the ONE authentication dependency.
#
# A route that declares `Depends(require_authenticated_user)` requires a
# VALIDATED bearer token — a Supabase Auth access token, or a generic OIDC
# token, verified by the ASGI middleware in services/authn.py (signature
# against the issuer JWKS, iss/aud/exp/nbf, required sub, asymmetric algs
# only). Identity is the verified token subject and the users row it
# provisions — NEVER a request body, query, or path field. There is no
# X-Viewer-Id fallback here on purpose: that header is unverified and is
# only ever honoured in the fully-public posture by get_scope(); an
# authenticated endpoint that accepted it would be exactly the "fake
# authorization that could be mistaken for production authorization" the
# launch spec forbids.
#
# Consequence: these endpoints return 401 for every caller until Supabase
# Auth (or generic OIDC) is configured — SUPABASE_PROJECT_URL +
# SUPABASE_JWT_AUDIENCE, or OIDC_ISSUER + OIDC_AUDIENCE. That is the
# intended frozen-posture behaviour, not a gap.
# ---------------------------------------------------------------------------


# Converged: the principal IS the canonical AuthContext (services/auth_context.py).
AuthenticatedPrincipal = AuthContext


async def require_authenticated_user(request: Request) -> AuthenticatedPrincipal:
    """FastAPI dependency: require a validated END-USER identity.

    401 when no credentials are presented (a present-but-invalid token is
    already a 401 from the ASGI middleware). 403 when the token is valid but
    the account is deactivated, or when the caller is a service (workers are
    not users). 409 for ambiguous multi-organization membership on write
    paths. Never consults request-body identity fields.
    """
    from app.services.authn import AmbiguousTenant, IdentityInactive, current_actor, current_service

    if current_service() is not None:
        raise HTTPException(status_code=403, detail="a user identity is required; service credentials are not accepted here")
    actor = current_actor()
    if actor is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await _human_context(request, actor, strict_tenant=True)
    except IdentityInactive as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AmbiguousTenant as exc:
        # Multiple active org memberships with no per-request org selector
        # yet: fail loud rather than silently pick one (H1 discipline).
        raise HTTPException(
            status_code=409,
            detail=f"ambiguous organization membership: {exc}",
        ) from exc


def require_scopes(*scopes: str):
    """Dependency factory: the caller (human OR service) must hold every named
    scope. 401 anonymous / expired, 403 authenticated-but-missing-scope
    (audited best-effort). Unenforced only in the TEST posture with no identity
    provider configured (see auth_enforced)."""

    async def _dep(request: Request) -> Principal:
        ctx = await get_auth_context(request)
        if isinstance(ctx, AnonymousContext) and not auth_enforced():
            return ctx
        try:
            _check_scopes(ctx, *scopes)
        except AuthorizationDenied as exc:
            if exc.status == 403:
                from app.services.audit import record_security_event

                await record_security_event(
                    getattr(getattr(request, "app", None), "state", None) and getattr(request.app.state, "pool", None),
                    actor_subject=getattr(ctx, "actor_id", None), action="access.denied",
                    object_type="endpoint", object_id=f"{request.method} {request.url.path}"[:200],
                    details={"required_scopes": sorted(scopes), "principal": ctx.kind},
                )
            raise HTTPException(
                status_code=exc.status, detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
            ) from exc
        return ctx

    return _dep


async def optional_authenticated_user(
    request: Request,
) -> Optional[AuthenticatedPrincipal]:
    """Like `require_authenticated_user` but returns None instead of 401
    when unauthenticated — for endpoints that are public but richer when
    signed in."""
    from app.services.authn import current_actor

    if current_actor() is None:
        return None
    return await require_authenticated_user(request)


def _current_service_for_key():
    from app.services.authn import current_service

    return current_service()


def scope_key_for(scope: AccessScope, request: Request) -> str:
    """
    The key rate limits and budgets are counted against.

    Falls back to client IP for anonymous traffic — without it, every
    anonymous user shares one bucket, so a single script would exhaust
    the limit for everyone. Note this is the direct socket address: behind
    a proxy or load balancer that becomes the proxy's IP, collapsing all
    users into one bucket again. Real deployment needs a trusted
    X-Forwarded-For chain, which is deliberately not trusted here because
    an untrusted one is trivially spoofed to bypass limits entirely.
    """
    svc = _current_service_for_key()
    if svc is not None:
        return svc.rate_key          # verified service id, not the socket address
    if scope.viewer_id:
        return f"viewer:{scope.viewer_id}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


async def enforce_limits(
    request: Request,
    scope: AccessScope = Depends(get_scope),
) -> str:
    """
    Rate limit and budget check for LLM-spending endpoints.

    Returns the scope key so handlers can attribute spend to the same
    identity the limit was counted against.
    """
    if not settings.governance_enabled:
        return scope_key_for(scope, request)

    pool = request.app.state.pool
    key = scope_key_for(scope, request)
    endpoint = request.url.path

    try:
        await RateLimiter(pool).check_and_record(key, endpoint)
    except RateLimitExceeded as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}
        ) from exc

    try:
        await CostGovernor(
            pool,
            daily_cap_usd=settings.daily_llm_budget_usd,
            per_viewer_daily_cap_usd=settings.per_viewer_daily_budget_usd,
        ).check_budget(key)
    except BudgetExceeded as exc:
        # 402 rather than 429: this is not a "slow down and retry" —
        # retrying immediately will fail identically until the window
        # rolls or the cap is raised.
        raise HTTPException(402, str(exc)) from exc

    return key


async def require_admin_api_key(
    request: Request = None,  # type: ignore[assignment]  -- injected by FastAPI; optional for direct calls
    x_admin_api_key: Optional[str] = Header(default=None),
) -> None:
    """Gate for `/v1/admin/*` and `/v1/trajectories/*` (name kept: routers and
    tests import it).

    Accepts, in order:
      1. a verified HUMAN holding `admin:ops` (platform_admin grant) or a
         verified SERVICE holding `maintenance:run` -- scoped, attributable,
         revocable identities (the intended path);
      2. the LEGACY static `X-Admin-Api-Key`, only while
         ADMIN_API_KEY_LEGACY_ENABLED (default true, for compatibility). It is
         a permanent universal secret with no identity, so: constant-time
         compare, >=32 chars outside TEST (boot guard), and every use writes a
         best-effort `admin.legacy_key_used` audit event. Set the flag false to
         retire it.

    Fails CLOSED: with nothing valid presented (or the legacy key unset or
    disabled) the answer is 401/403, never open. The legacy key deliberately
    does not use `Authorization: Bearer` (the ASGI middleware owns that header
    for JWTs).
    """
    import secrets

    from app.services import auth_context as _ac
    from app.services.audit import record_security_event
    from app.services.authn import current_actor, current_service

    if request is not None and (current_actor() is not None or current_service() is not None):
        ctx = await get_auth_context(request)
        if isinstance(ctx, AuthContext) and ctx.has_scope(_ac.ADMIN_OPS):
            return
        if not isinstance(ctx, AuthContext) and ctx.has_scope(_ac.MAINTENANCE_RUN):
            return
        if not x_admin_api_key:
            raise HTTPException(403, "admin scope required (admin:ops / maintenance:run)")

    configured = settings.admin_api_key
    if not configured or not settings.legacy_admin_key_enabled:
        raise HTTPException(401, "admin API is not configured (ADMIN_API_KEY unset or legacy key disabled) -- refusing all requests")
    if not x_admin_api_key:
        raise HTTPException(401, "missing X-Admin-Api-Key header")
    if not secrets.compare_digest(x_admin_api_key, configured):
        raise HTTPException(401, "invalid admin API key")
    if request is not None:
        await record_security_event(
            getattr(getattr(request, "app", None), "state", None) and getattr(request.app.state, "pool", None),
            actor_subject="legacy-admin-key", action="admin.legacy_key_used", object_type="endpoint",
            object_id=f"{request.method} {request.url.path}"[:200], details={},
        )


def make_cost_recorder(pool, scope_key: str, operation: str):
    """
    Build the `on_call` callback threaded through DebateEngine,
    Layer1Evaluator, SimulatedReplayEvaluator, DecompositionService, and
    ChatService (see their `on_call` parameters).

    This is the fix for a real gap: `CostGovernor.check_budget()` was
    being called and genuinely blocking requests, but nothing ever called
    `.record()`, so `llm_spend` stayed empty and the budget check always
    saw $0 spent regardless of real usage. The cap looked enforced and
    was not.

    Token counts are estimated from prompt/response text length
    (`estimate_tokens`), not read from provider usage fields -- General
    Compute and other OpenAI-compatible providers vary in whether and how
    they return exact usage, and a rough estimate that always fires beats
    an exact one that silently doesn't. Consistent with `estimated_cost`'s
    own documented purpose in the schema: a guardrail against runaway
    spend, not an accounting record.
    """
    from app.services.governance import CostGovernor, estimate_tokens

    governor = CostGovernor(pool)

    async def record(agent, prompt_text: str, response_text: str) -> None:
        try:
            await governor.record(
                provider=agent.family,
                model=agent.model_id,
                operation=operation,
                input_tokens=estimate_tokens(prompt_text),
                output_tokens=estimate_tokens(response_text),
                scope_key=scope_key,
            )
        except Exception as exc:  # noqa: BLE001
            # Recording failing must never break the actual response the
            # user is waiting on -- the LLM call already happened and was
            # already paid for. Logged, not raised.
            import logging
            logging.getLogger(__name__).error(
                "cost recording failed for %s/%s: %s", agent.family, agent.model_id, exc
            )

    return record
