"""
Procedure Graph API (directive §41).

Read-only REST surface over `app.services.procedure_graph_api` --
mirrors `app/api/graph.py`'s precedent exactly: `get_scope` resolves the
viewer, every service call carries that scope, and a row the viewer
can't see is a plain 404, not a labelled placeholder (same
anti-enumeration posture `graph.py` already documents).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import AuthenticatedPrincipal, get_scope, require_authenticated_user
from app.execution.procedure_graph import ProcedureCompositionError
from app.services.access import AccessScope
from app.services.domain_search import search_global
from app.services.procedure_graph_api import (
    get_procedure_claims,
    get_procedure_detail,
    get_procedure_evidence,
    get_procedure_graph,
    get_procedure_versions,
)

router = APIRouter(prefix="/v1/procedures", tags=["procedures"])


async def get_pool(request: Request):
    return request.app.state.pool


@router.get("/search")
async def search_procedures(
    q: str,
    repository_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict[str, Any]:
    """
    Directive Sec16 convenience endpoint -- a thin, procedure-only leg of
    `domain_search.search_global` (`object_types=["procedure"]`), NOT a
    second retrieval engine: same cascade+RRF ranking `/v1/search` itself
    uses for its procedure bucket, reused verbatim. Registered before
    `/{procedure_row_id}` for readability (the UUID path convertor on
    that route already rejects the literal segment `search` on its own).
    """
    return await search_global(
        pool, q,
        object_types=["procedure"],
        repository_id=repository_id,
        project_id=project_id,
        limit=limit,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Fast contribution path (launch: "a signed-in user adds a procedure as
# fast as possible"). Two write endpoints, both authenticated, both
# private-by-default and 'candidate' — nothing a user contributes is born
# global or verified (data-flow spec INV-01/INV-03). They compose the
# existing capture path (services/procedures.capture_procedure,
# services/skill_ingestion.ingest_skill_md); NOT a second procedure store.
# ---------------------------------------------------------------------------


class ProcedureCreateBody(BaseModel):
    """The minimum a procedure needs to be useful: a name, a goal, and the
    ordered steps. Everything else is optional and derived deterministically
    (display text, retrieval document) when omitted."""

    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4000)
    steps: list[str] = Field(default_factory=list, max_length=200)
    applicability: list[str] = Field(
        default_factory=list, max_length=50,
        description="Plain-language preconditions — when this procedure applies.",
    )
    tools: list[str] = Field(default_factory=list, max_length=50)
    failure_conditions: list[str] = Field(default_factory=list, max_length=50)
    domain: Optional[str] = Field(default=None, max_length=120)
    embed: bool = Field(
        default=True,
        description="Embed inline for immediate semantic search (one provider "
        "call). Set false for a faster write; the row is indexed later by the "
        "embedding backfill.",
    )


class ProcedureFromTextBody(BaseModel):
    """Paste a whole SKILL.md-style document (frontmatter + prose + a
    'Steps'/'Workflow' list). Parsed deterministically — no model call."""

    text: str = Field(min_length=1, max_length=100_000)
    name: Optional[str] = Field(default=None, max_length=200)
    domain: Optional[str] = Field(default=None, max_length=120)
    embed: bool = Field(
        default=True,
        description="Embed inline for immediate semantic search (one provider "
        "call). Set false for a sub-100ms write; the row is indexed later by "
        "the embedding backfill.",
    )


def _created_response(result: dict, principal: AuthenticatedPrincipal) -> dict:
    return {
        "procedure_id": result["procedure_id"],
        "id": result["id"],
        "scope": "PRIVATE",
        "verification": "candidate",
        "owner_id": principal.user_id,
        "next": f"/v1/procedures/{result['id']}",
    }


@router.post("", status_code=201)
async def create_procedure(
    body: ProcedureCreateBody,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Create a private procedure owned by the authenticated caller.

    Identity is the verified token subject — the request body carries no
    owner/user field and could not set one. Starts `candidate` / `private`;
    promotion to verified needs execution evidence, publication to global
    is a separate explicit action.
    """
    from app.services.procedures import capture_procedure
    from app.services.retrieval_document import (
        RETRIEVAL_DOCUMENT_IMPORT_VERSION,
        RETRIEVAL_DOCUMENT_VERSION,
        build_procedure_retrieval_document,
        retrieval_document_sha256,
    )

    preconditions = [p for p in body.applicability if p.strip()]
    failure_conditions = [f for f in body.failure_conditions if f.strip()]
    steps = [{"order": i, "goal": s} for i, s in enumerate(body.steps) if s.strip()]

    # Deterministic retrieval document, built here so the row is never
    # stored embedded-but-unrepresented and the embedding backfill has a
    # canonical text to work from.
    retrieval_doc = build_procedure_retrieval_document(
        {
            "name": body.name, "goal": body.goal, "steps": steps,
            "preconditions": preconditions, "invariants": [],
            "postconditions": [], "failure_conditions": failure_conditions,
            "domain": body.domain, "domain_payload": {"tools": body.tools},
        }
    )
    capture_kwargs: dict = dict(
        preconditions=preconditions,
        failure_conditions=failure_conditions,
        domain=body.domain,
        domain_payload={"tools": [t for t in body.tools if t.strip()]},
        provenance="system_pending_review",
        scope_type="user",
        scope_entity_id=principal.user_id,
        owner_id=principal.user_id,
        visibility="private",
        created_by="user_submission",
        retrieval_document=retrieval_doc,
        retrieval_document_sha256=retrieval_document_sha256(retrieval_doc),
        retrieval_document_version=RETRIEVAL_DOCUMENT_IMPORT_VERSION,
    )

    embedded = False
    if body.embed:
        try:
            from app.services.embeddings import Embedder

            vec, meta = await Embedder().embed_one_with_metadata(
                retrieval_doc, input_type="document"
            )
            capture_kwargs.update(
                embedding=vec,
                embedding_model_id=meta.model_id,
                embedding_provider=meta.provider,
                embedding_input_type=meta.input_type,
                embedding_text_hash=meta.text_sha256,
                retrieval_document_version=RETRIEVAL_DOCUMENT_VERSION,
            )
            embedded = True
        except Exception:  # noqa: BLE001 — indexing is best-effort; the backfill covers a miss
            embedded = False

    try:
        result = await capture_procedure(
            pool, name=body.name, goal=body.goal, steps=steps, **capture_kwargs
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _created_response(result, principal) | {"embedded": embedded}


@router.post("/from_text", status_code=201)
async def create_procedure_from_text(
    body: ProcedureFromTextBody,
    pool=Depends(get_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    """Create a private procedure from a pasted SKILL.md-style document.

    The document is untrusted user input: parsed deterministically, screened
    for prompt-injection, and always recorded as unreviewed
    (`system_pending_review`) private content owned by the caller.
    """
    from app.services.skill_ingestion import ingest_skill_md

    try:
        result = await ingest_skill_md(
            pool,
            body.text,
            fallback_name=body.name or "untitled-procedure",
            domain=body.domain,
            created_by="user_submission",
            owner_id=principal.user_id,
            visibility="private",
            scope_type="user",
            scope_entity_id=principal.user_id,
            embed=body.embed,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if result.get("status") == "duplicate":
        raise HTTPException(
            409,
            {
                "detail": "a very similar procedure already exists",
                "existing_procedure_id": result["existing_procedure_id"],
                "similarity": result.get("similarity"),
            },
        )
    return _created_response(result, principal) | {
        "embedded": result.get("embedded", False),
    }


@router.get("/{procedure_row_id}")
async def read_procedure(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return detail


@router.get("/{procedure_row_id}/versions")
async def read_procedure_versions(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    """`procedure_row_id` names any one version row of the family --
    versions are looked up by that row's own `procedure_id` (the stable
    cross-version handle), so any live/visible member of the chain is a
    valid entry point. 404 only if THAT row itself is missing/invisible;
    an empty version list for a row that does exist is not possible
    (a row is always at least its own version)."""
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return await get_procedure_versions(pool, detail["procedure_id"], scope=scope)


@router.get("/{procedure_row_id}/graph")
async def read_procedure_graph(
    procedure_row_id: UUID,
    depth: int = Query(default=2, ge=1, le=8),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> dict:
    try:
        graph = await get_procedure_graph(pool, str(procedure_row_id), depth=depth, scope=scope)
    except ProcedureCompositionError as exc:
        raise HTTPException(422, str(exc)) from exc
    if graph is None:
        raise HTTPException(404, "procedure not found")
    return graph


@router.get("/{procedure_row_id}/claims")
async def read_procedure_claims(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return await get_procedure_claims(pool, str(procedure_row_id), scope=scope)


@router.get("/{procedure_row_id}/evidence")
async def read_procedure_evidence(
    procedure_row_id: UUID,
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> list[dict]:
    detail = await get_procedure_detail(pool, str(procedure_row_id), scope=scope)
    if detail is None:
        raise HTTPException(404, "procedure not found")
    return await get_procedure_evidence(pool, str(procedure_row_id), scope=scope)
