"""Phase 7 (launch compliance) — audit_events wiring (LC-011).

Offline. The cross-cutting audit assertions live in the Phase 4/5/6
suites (publication_approved / provider_call_denied / export_completed /
deletion_completed ...). This pins the remaining transition — a private
object entering storage — and that the required action vocabulary is
actually emitted somewhere in the service layer.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from app.api import procedures as proc_api
from app.api.deps import AuthenticatedPrincipal

BACKEND = Path(__file__).resolve().parents[1]


class _Pool:
    def __init__(self):
        self.audits = []

    async def fetchrow(self, sql, *a):
        f = " ".join(sql.split())
        if "INSERT INTO procedures" in f:
            return {"id": str(uuid4()), "procedure_id": str(uuid4())}
        if "INSERT INTO audit_events" in f:
            self.audits.append({"actor_subject": a[0], "action": a[2],
                                "object_type": a[3], "object_id": a[4], "details": a[6]})
            return {"id": len(self.audits)}
        raise AssertionError(f[:70])


PRINCIPAL = AuthenticatedPrincipal(user_id="u1", subject="alice", email="a@x")


def test_structured_create_emits_private_object_created():
    pool = _Pool()
    body = proc_api.ProcedureCreateBody(name="x", goal="y", steps=["s"], embed=False)
    asyncio.run(proc_api.create_procedure(body, pool=pool, principal=PRINCIPAL))
    actions = [a["action"] for a in pool.audits]
    assert "private_object_created" in actions
    ev = next(a for a in pool.audits if a["action"] == "private_object_created")
    assert ev["actor_subject"] == "alice"
    assert ev["object_type"] == "procedure"
    # details carry context, never a secret
    assert "password" not in ev["details"] and "api_key" not in ev["details"].lower()


def test_from_text_create_emits_private_object_created():
    pool = _Pool()
    body = proc_api.ProcedureFromTextBody(
        text="---\nname: p\n---\nUse when.\n\n## Steps\n1. do it\n", embed=False
    )
    asyncio.run(proc_api.create_procedure_from_text(body, pool=pool, principal=PRINCIPAL))
    assert "private_object_created" in [a["action"] for a in pool.audits]


def test_required_audit_action_vocabulary_is_emitted_somewhere():
    """Every LC-011 transition name appears as an action literal in the
    service/api layer (a cheap completeness net, not a runtime proof)."""
    required = {
        "private_object_created", "private_object_deleted",
        "publication_approved", "publication_rejected",
        "global_candidate_created",
        "export_requested", "export_completed",
        "deletion_requested", "deletion_completed",
        "provider_call_allowed", "provider_call_denied",
        "workspace_registered",
    }
    src = ""
    for p in list((BACKEND / "app" / "services").glob("*.py")) + list(
        (BACKEND / "app" / "api").glob("*.py")
    ):
        src += p.read_text(encoding="utf-8")
    missing = {a for a in required if f'"{a}"' not in src}
    assert not missing, f"audit actions never emitted: {sorted(missing)}"
