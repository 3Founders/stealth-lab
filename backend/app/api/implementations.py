"""
Read-only REST surface over the durable Implementation Registry
(directive Sec 43-54, `app/execution/implementation_registry.py`,
backed by `db/33_implementation_registry.sql`).

Mirrors `app/api/graph.py`'s/`app/api/claims.py`'s exact structure:
`pool` via `request.app.state.pool`, `scope: AccessScope =
Depends(get_scope)`, anti-enumeration posture (a row the caller's scope
cannot see 404s the same as a row that doesn't exist -- the real gate is
`visibility_predicate()` filtering inside `implementation_registry.py`
itself, this router adds no additional filtering logic of its own).

No business logic lives here beyond thin param passthrough and the one
inline capability fallback (see `_capability_endpoint` below, and its own
docstring for why it is inline rather than imported).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_scope
from app.execution import implementation_registry
from app.services.access import AccessScope

router = APIRouter(prefix="/v1/implementations", tags=["implementations"])


async def get_pool(request: Request):
    return request.app.state.pool


# ---------------------------------------------------------------------------
# GET /{implementation_id}
# ---------------------------------------------------------------------------


@router.get("/{implementation_id}")
async def get_implementation(
    implementation_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    """
    One implementation row by id, visibility-filtered by
    `implementation_registry.get()` itself. 404 for missing OR invisible
    alike -- same anti-enumeration posture every other Wave-1 reader in
    this codebase uses (`claim_graph_api.get_claim`, `graph.py::
    get_subgraph`).

    Sanity note (not new logic): `auth_requirements` only ever stores a
    `credential_ref` SHAPE per `implementation_registry.py`'s own
    docstring -- no secret value is ever written to this column by any
    real writer, so echoing the row verbatim here leaks nothing a
    `visibility='private'` row's real gate (the query predicate itself)
    doesn't already withhold from an unauthorized caller.
    """
    row = await implementation_registry.get(pool, str(implementation_id), scope=scope)
    if row is None:
        raise HTTPException(404, "implementation not found")
    return row


# ---------------------------------------------------------------------------
# GET /{implementation_id}/descriptor  -- the canonical execution ABI
# ---------------------------------------------------------------------------
@router.get("/{implementation_id}/descriptor")
async def get_implementation_descriptor(
    implementation_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    """
    The deterministic, secret-free execution descriptor for one visible
    implementation (§1/§22/§27) -- the stable machine-readable shape a
    harness/replay consumer binds against, identical to what the MCP
    `inspect_implementation` / `resolve_implementation` tools emit. Same
    404-for-missing-or-invisible posture as `get_implementation`.
    """
    d = await implementation_registry.get_descriptor(pool, str(implementation_id), scope=scope)
    if d is None:
        raise HTTPException(404, "implementation not found")
    return d


# ---------------------------------------------------------------------------
# GET / (list)
# ---------------------------------------------------------------------------


@router.get("")
async def list_implementations(
    kind: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    """Thin passthrough to `implementation_registry.list_implementations`
    -- bounded, filtered, visibility-checked there. An unknown `kind`/
    `status` raises `ImplementationRegistryError`, surfaced here as a
    422 (a caller-contract violation, not a missing-row 404)."""
    try:
        return await implementation_registry.list_implementations(
            pool, scope=scope, kind=kind, provider=provider, status=status, limit=limit,
        )
    except implementation_registry.ImplementationRegistryError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /{implementation_id}/evidence
# ---------------------------------------------------------------------------


async def _get_implementation_evidence(pool, implementation_id: str, *, scope: AccessScope) -> list[dict]:
    """Real evidence rows targeting this implementation
    (`target_type='implementation', target_id=implementation_id`),
    `t_invalid IS NULL`, visibility-filtered -- mirrors
    `app.services.claim_evidence.get_claim_evidence`'s exact SELECT
    shape, adjusted for `target_type` and for this router's own
    visibility predicate (claim_evidence.py's reader carries none; this
    one does, since `evidence` rows here may be private the same way
    claim/procedure evidence can be)."""
    from app.services.access import visibility_predicate

    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT * FROM evidence
        WHERE target_type = 'implementation' AND target_id = $1::uuid
          AND t_invalid IS NULL AND {vis_sql}
        ORDER BY t_valid ASC
        """,
        implementation_id, *vis_params,
    )
    return [dict(row) for row in rows]


@router.get("/{implementation_id}/evidence")
async def get_implementation_evidence(
    implementation_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    # Require the implementation itself to be visible first -- same
    # "check the parent row before composing its children" posture
    # claims.py's `_require_visible_claim` establishes.
    parent = await implementation_registry.get(pool, str(implementation_id), scope=scope)
    if parent is None:
        raise HTTPException(404, "implementation not found")
    return await _get_implementation_evidence(pool, str(implementation_id), scope=scope)


# ---------------------------------------------------------------------------
# GET /{implementation_id}/capability
# ---------------------------------------------------------------------------


def _inline_capability_fallback(evidence: list[dict]) -> dict:
    """
    PROVISIONAL: duplicated inline pending the sibling `app/services/
    capabilities.py` module (being built in parallel this same wave --
    see this task's own briefing note); consolidate later.

    Reuses `procedure_extraction/capability.py`'s own pure Wilson-interval
    math directly (`wilson_interval`, `band_for_p`, `route_for_p`) --
    exactly the pattern `procedure_graph_api.py::_capability_estimate`
    already established for procedures, adjusted for zero new
    machinery: this function computes over `evidence` rows targeting an
    IMPLEMENTATION rather than a procedure. No `environment`-gated L3-L5
    ladder is computed here either, for the identical honest reason
    `_capability_estimate`'s own docstring gives: no such column exists
    on `evidence` for any target type.
    """
    from app.services.procedure_extraction.capability import (
        band_for_p,
        route_for_p,
        wilson_interval,
    )

    outcome_bearing = [
        e for e in evidence
        if e.get("direction") == "supports"
        and e.get("evidence_type") in ("execution_result", "reproduction")
        and e.get("outcome_status") in ("success", "failure")
    ]
    total = len(outcome_bearing)
    successes = sum(1 for e in outcome_bearing if e["outcome_status"] == "success")

    p_lower, p_upper = wilson_interval(successes, total)
    p_estimate = p_lower

    independent_groups = len({
        e["independence_group"] for e in outcome_bearing if e.get("independence_group")
    })

    band = 0
    if total > 0 and successes > 0:
        band = band_for_p(p_estimate)

    return {
        "p_estimate": p_estimate,
        "p_lower": p_lower,
        "p_upper": p_upper,
        "evidence_count": total,
        "success_count": successes,
        "independent_groups": independent_groups,
        "band": band,
        "routing": route_for_p(p_estimate).value,
        "level_gated": None,
        "provisional": True,
    }


@router.get("/{implementation_id}/capability")
async def get_implementation_capability(
    implementation_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    """
    Capability estimate for this implementation. Prefers the sibling
    `app.services.capabilities.get_implementation_capability` when that
    module exists (built in a parallel workstream this same wave); falls
    back to `_inline_capability_fallback` (honest, clearly-labeled
    `provisional: true`) when it does not.
    """
    parent = await implementation_registry.get(pool, str(implementation_id), scope=scope)
    if parent is None:
        raise HTTPException(404, "implementation not found")

    try:
        from app.services.capabilities import (  # type: ignore[import-not-found]
            get_implementation_capability as _sibling_get_capability,
        )
    except ImportError:
        evidence = await _get_implementation_evidence(pool, str(implementation_id), scope=scope)
        return _inline_capability_fallback(evidence)

    # The sibling module's own signature takes no `scope` -- it reads
    # evidence unfiltered by visibility (its own design choice, not
    # altered here). We've already confirmed the parent implementation
    # row itself is visible to `scope` above.
    return await _sibling_get_capability(pool, str(implementation_id))
