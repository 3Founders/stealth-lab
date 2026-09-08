"""ExportService + DeletionService (launch compliance Phase 6 / LC-006,
LC-007, data-flow spec §20-22).

Both are dependency-aware and both record their lifecycle in
`data_requests`. Neither touches independently-sourced global knowledge:
a procedure the user PUBLISHED became a fresh Global Candidate with its
own provenance and (once other people run it) its own evidence -- the
user leaving does not delete the commons. Conversely nothing is relabeled
to hide private material.

Deletion is TOMBSTONE by default (the row stops being retrievable; its
history stays queryable) and PHYSICAL only for a private row with no
downstream publication. A legal hold on the subject blocks deletion
entirely.
"""
from __future__ import annotations

from typing import Any, Optional

# ---- what "owned by this subject" means across the substrate ----------
_USER_ROW = "SELECT id::text, issuer, external_subject, display_name, email, is_active, t_created FROM users WHERE external_subject = $1"
_PRIV_PROCS = (
    "SELECT id::text, name, display_name, visibility, availability, t_created "
    "FROM procedures WHERE owner_id = $1 AND visibility <> 'public' AND t_invalid IS NULL"
)
_PUBLICATIONS = (
    "SELECT id::text, source_object_id, published_object_id, destination_scope, "
    "review_state, withdrawal_state, t_created "
    "FROM publication_records WHERE actor_subject = $1"
)
_LEGAL_HOLD = "SELECT 1 FROM data_requests WHERE subject = $1 AND legal_hold IS TRUE LIMIT 1"
_REQ_INSERT = (
    "INSERT INTO data_requests (request_type, subject, actor_user_id, status, scope) "
    "VALUES ($1, $2, $3::uuid, $4, $5::jsonb) RETURNING id::text"
)
_REQ_COMPLETE = (
    "UPDATE data_requests SET status = $2, result_summary = $3::jsonb, "
    "t_completed = now() WHERE id = $1::uuid"
)


def _json(o: Any) -> str:
    import json

    return json.dumps(o, default=str)


async def export_user_data(
    pool: Any, *, subject: str, actor_user_id: Optional[str] = None
) -> dict:
    """Machine-readable bundle of everything the user is entitled to.

    Global Commons knowledge is NOT included as owned property -- only the
    user's publication *actions* (references), per data-flow §22.
    """
    from app.services.audit import record_audit_event

    req_id = await pool.fetchval(
        _REQ_INSERT, "export", subject, actor_user_id, "processing",
        _json({"requested": "all"}),
    )
    await record_audit_event(
        pool, actor_subject=subject, action="export_requested",
        object_type="data_request", object_id=req_id, actor_user_id=actor_user_id,
    )

    user = await pool.fetchrow(_USER_ROW, subject)
    procs = await pool.fetch(_PRIV_PROCS, subject)
    pubs = await pool.fetch(_PUBLICATIONS, subject)

    bundle = {
        "format": "stealthlab.export/v1",
        "subject": subject,
        "account": dict(user) if user else None,
        "private_procedures": [dict(r) for r in procs],
        "publication_actions": [dict(r) for r in pubs],   # references, not the published objects
        "preferences": {},                                # none stored yet
        "notes": (
            "Global Commons procedures you published are listed under "
            "publication_actions as references; they are not private property "
            "and are not included as owned rows."
        ),
    }

    summary = {
        "private_procedures": len(procs),
        "publication_actions": len(pubs),
        "has_account_row": user is not None,
    }
    await pool.execute(_REQ_COMPLETE, req_id, "completed", _json(summary))
    await record_audit_event(
        pool, actor_subject=subject, action="export_completed",
        object_type="data_request", object_id=req_id, actor_user_id=actor_user_id,
        details=summary,
    )
    bundle["request_id"] = req_id
    return bundle


async def plan_user_deletion(pool: Any, *, subject: str) -> dict:
    """What a deletion would do. Never mutates."""
    procs = [dict(r) for r in await pool.fetch(_PRIV_PROCS, subject)]
    pubs = [dict(r) for r in await pool.fetch(_PUBLICATIONS, subject)]
    on_hold = await pool.fetchval(_LEGAL_HOLD, subject) is not None

    published_source_ids = {p["source_object_id"] for p in pubs}
    physical, tombstone = [], []
    for p in procs:
        (tombstone if p["id"] in published_source_ids else physical).append(p["id"])

    return {
        "subject": subject,
        "legal_hold": on_hold,
        "private_procedures_physical_delete": physical,
        "private_procedures_tombstone": tombstone,  # published source -> keep history, stop retrieval
        "publication_records_retained": [p["id"] for p in pubs],
        "global_objects_preserved": [
            p["published_object_id"] for p in pubs if p["published_object_id"]
        ],
        "blocked": on_hold,
    }


async def delete_user_data(
    pool: Any, *, subject: str, actor_user_id: Optional[str] = None,
    dry_run: bool = True,
) -> dict:
    """Dependency-aware deletion. `dry_run=True` returns the plan only."""
    from app.services.audit import record_audit_event

    plan = await plan_user_deletion(pool, subject=subject)
    if dry_run:
        plan["dry_run"] = True
        return plan
    if plan["legal_hold"]:
        raise PermissionError("subject is under legal hold; deletion refused")

    req_id = await pool.fetchval(
        _REQ_INSERT, "deletion", subject, actor_user_id, "processing", _json(plan)
    )
    await record_audit_event(
        pool, actor_subject=subject, action="deletion_requested",
        object_type="data_request", object_id=req_id, actor_user_id=actor_user_id,
        details={"physical": len(plan["private_procedures_physical_delete"]),
                 "tombstone": len(plan["private_procedures_tombstone"])},
    )

    # Physical delete: private rows with no downstream publication. This
    # removes the row AND its embedding (same row) -- covers the private
    # vector too (INV-17). Retrieval caches are read-through, so a deleted
    # row simply stops appearing.
    for pid in plan["private_procedures_physical_delete"]:
        await pool.execute("DELETE FROM procedures WHERE id = $1::uuid", pid)
        await record_audit_event(
            pool, actor_subject=subject, action="private_object_deleted",
            object_type="procedure", object_id=pid, actor_user_id=actor_user_id,
            details={"mode": "physical", "request_id": req_id},
        )

    # Tombstone: a published source stays as immutable history but stops
    # being retrievable and its vector is cleared.
    for pid in plan["private_procedures_tombstone"]:
        await pool.execute(
            "UPDATE procedures SET availability = 'deleted', embedding = NULL, "
            "t_invalid = now() WHERE id = $1::uuid",
            pid,
        )

    summary = {
        "physically_deleted": len(plan["private_procedures_physical_delete"]),
        "tombstoned": len(plan["private_procedures_tombstone"]),
        "global_objects_preserved": len(plan["global_objects_preserved"]),
        "publication_records_retained": len(plan["publication_records_retained"]),
    }
    await pool.execute(_REQ_COMPLETE, req_id, "completed", _json(summary))
    await record_audit_event(
        pool, actor_subject=subject, action="deletion_completed",
        object_type="data_request", object_id=req_id, actor_user_id=actor_user_id,
        details=summary,
    )
    return {"request_id": req_id, **summary, "dry_run": False}
