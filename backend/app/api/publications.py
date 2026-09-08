"""Publication records + withdrawal (launch compliance Phase 4 / §24-25).

The publish operation itself lives on POST /v1/procedures/{id}/publish
(app/api/procedures.py) — this router is the read/withdraw surface over
the `publication_records` contribution log.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import AuthenticatedPrincipal, require_authenticated_user

router = APIRouter(prefix="/v1/publications", tags=["publications"])


async def _pool(request: Request):
    return request.app.state.pool


@router.get("")
async def list_my_publications(
    pool=Depends(_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    rows = await pool.fetch(
        """
        SELECT id::text, source_object_id, published_object_id, destination_scope,
               review_state, withdrawal_state, source_license,
               authorization_timestamp, t_created
          FROM publication_records
         WHERE actor_subject = $1
         ORDER BY t_created DESC
         LIMIT 200
        """,
        principal.subject,
    )
    return {"publications": [dict(r) for r in rows]}


@router.post("/{publication_id}/withdraw")
async def withdraw(
    publication_id: UUID,
    pool=Depends(_pool),
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
) -> dict:
    from app.services.publication import (
        PublicationDenied,
        SourceProcedureNotFound,
        withdraw_publication,
    )

    try:
        return await withdraw_publication(
            pool,
            publication_id=str(publication_id),
            actor_subject=principal.subject,
            actor_user_id=principal.user_id,
        )
    except SourceProcedureNotFound:
        raise HTTPException(404, "publication record not found")
    except PublicationDenied as exc:
        raise HTTPException(403, {"detail": "withdrawal refused", "reasons": exc.reasons})
