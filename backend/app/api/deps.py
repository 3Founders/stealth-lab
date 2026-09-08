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
from app.services.governance import (
    BudgetExceeded,
    CostGovernor,
    RateLimiter,
    RateLimitExceeded,
)


async def get_scope(request: Request, x_viewer_id: Optional[str] = Header(default=None)) -> AccessScope:
    """
    The scope for this request. Anonymous by default — the normal case on
    a public commons, not a failure.

    Band 2.9: a VALIDATED OIDC/Supabase actor (published by authn's
    middleware on the contextvar) always wins — its subject is real
    identity.

    Phase 2 hardening: the X-Viewer-Id header is a dev convenience for the
    FULLY-PUBLIC posture only. The moment real identity is configured
    (Supabase Auth preset or generic OIDC), an unauthenticated request is
    anonymous — a plain header can no longer name a user, so a private row
    written by an authenticated user cannot be read by anyone spoofing
    `X-Viewer-Id: <their subject>`.
    """
    from app.services.authn import current_actor, oidc_configured

    actor = current_actor()
    if actor is not None:
        return AccessScope.for_user(actor.subject)
    if x_viewer_id and not oidc_configured(settings):
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


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """A request-scoped, server-derived identity. `user_id` is the
    canonical StealthLab `users.id` (provisioned on first login from the
    verified token); `subject` is the raw verified token subject (the
    Supabase `auth.users` uid). `org_ids` are the caller's active
    organization memberships — the ORG_PRIVATE visibility boundary."""

    user_id: str
    subject: str
    issuer: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    org_ids: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()          # role names across the caller's active memberships
    claims: Mapping[str, Any] = field(default_factory=dict)

    def has_role(self, *names: str) -> bool:
        return any(r in self.roles for r in names)

    def access_scope(self) -> AccessScope:
        """The read/write scope for this principal: public rows, own rows,
        and — when the caller holds memberships — their organizations'
        'org'-visibility rows. Access is resolved from this, BEFORE any
        relevance ranking (data-flow spec INV-02)."""
        if self.org_ids:
            return AccessScope.for_org_member(self.subject, list(self.org_ids))
        return AccessScope.for_user(self.subject)


async def require_authenticated_user(request: Request) -> AuthenticatedPrincipal:
    """FastAPI dependency: require a validated end-user identity.

    Raises 401 when no credentials are presented (a present-but-invalid
    token is already rejected with 401 by the ASGI middleware before this
    runs). Raises 403 when the token is valid but the account is
    deactivated. Never consults request-body identity fields.
    """
    from app.services.authn import (
        AmbiguousTenant,
        IdentityInactive,
        current_actor,
        ensure_user,
        resolve_memberships,
    )

    actor = current_actor()
    if actor is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    pool = request.app.state.pool
    try:
        user_id = await ensure_user(pool, actor)
        memberships = await resolve_memberships(pool, user_id)
    except IdentityInactive as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AmbiguousTenant as exc:
        # Multiple active org memberships with no per-request org selector
        # yet: fail loud rather than silently pick one (H1 discipline).
        raise HTTPException(
            status_code=409,
            detail=f"ambiguous organization membership: {exc}",
        ) from exc

    org_ids = tuple(sorted({m.organization_id for m in memberships}))
    roles = tuple(sorted({m.role_name for m in memberships}))
    return AuthenticatedPrincipal(
        user_id=user_id,
        subject=actor.subject,
        issuer=actor.issuer,
        email=actor.email,
        name=actor.name,
        org_ids=org_ids,
        roles=roles,
        claims=dict(actor.claims),
    )


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
