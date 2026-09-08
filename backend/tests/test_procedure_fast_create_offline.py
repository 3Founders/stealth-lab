"""Phase 1 (launch compliance) — the fast contribution path.

Offline: a FakePool records the procedures INSERT so the test proves what
would be persisted. Covers both write endpoints
(POST /v1/procedures, POST /v1/procedures/from_text):

- the row is owned by the authenticated principal's user_id (server-
  derived — the body has no owner field);
- it is private and 'candidate' — never born global or verified;
- provenance is the unreviewed value, scope_type is 'user';
- both routes are gated by require_authenticated_user.
"""
from __future__ import annotations

import asyncio
import inspect
from uuid import uuid4

import pytest

from app.api import procedures as proc_api
from app.api.deps import AuthenticatedPrincipal, require_authenticated_user


class _FakePool:
    def __init__(self):
        self.insert_sql = None
        self.insert_args = None

    async def fetchrow(self, sql, *args):
        self.insert_sql = sql
        self.insert_args = args
        return {"id": uuid4(), "procedure_id": uuid4()}


def _col_value(pool, column: str):
    head = pool.insert_sql.split("INSERT INTO procedures (", 1)[1]
    col_str, rest = head.split(")", 1)
    columns = [c.strip() for c in col_str.replace("\n", " ").split(",")]
    values_str = rest.split("VALUES", 1)[1].split(")", 1)[0]
    placeholders = [
        p.strip() for p in values_str.replace("(", "").replace("\n", " ").split(",")
    ]
    ph = placeholders[columns.index(column)]
    n = int(ph.lstrip("$").split("::")[0])
    return pool.insert_args[n - 1]


PRINCIPAL = AuthenticatedPrincipal(
    user_id="user-uuid-1", subject="supabase-uid-1", email="c@x.dev"
)


def test_structured_create_is_private_candidate_owned_by_principal():
    pool = _FakePool()
    body = proc_api.ProcedureCreateBody(
        name="rebase onto main before pushing",
        goal="Keep a linear history and catch conflicts early.",
        steps=["git fetch origin", "git rebase origin/main", "run the tests", "git push"],
        applicability=["working on a shared branch"],
        embed=False,
    )
    out = asyncio.run(proc_api.create_procedure(body, pool=pool, principal=PRINCIPAL))

    assert out["scope"] == "PRIVATE"
    assert out["verification"] == "candidate"
    assert out["owner_id"] == "user-uuid-1"
    assert out["embedded"] is False

    assert _col_value(pool, "owner_id") == "user-uuid-1"
    assert _col_value(pool, "visibility") == "private"
    assert _col_value(pool, "provenance") == "system_pending_review"
    assert _col_value(pool, "scope_type") == "user"
    assert _col_value(pool, "scope_entity_id") == "user-uuid-1"
    assert _col_value(pool, "created_by") == "user_submission"
    # a canonical retrieval document is stored even without an inline embed
    assert _col_value(pool, "retrieval_document")
    assert _col_value(pool, "display_name")


def test_from_text_parses_a_pasted_doc_into_a_private_candidate():
    pool = _FakePool()
    body = proc_api.ProcedureFromTextBody(
        text=(
            "---\nname: clean-merge-conflicts\n---\n"
            "Use when a rebase stops on a conflict.\n\n"
            "## Steps\n1. open the conflicted file\n2. resolve the markers\n"
            "3. git add the file\n4. git rebase --continue\n"
        ),
        embed=False,
    )
    out = asyncio.run(
        proc_api.create_procedure_from_text(body, pool=pool, principal=PRINCIPAL)
    )
    assert out["scope"] == "PRIVATE"
    assert out["owner_id"] == "user-uuid-1"
    assert _col_value(pool, "owner_id") == "user-uuid-1"
    assert _col_value(pool, "visibility") == "private"
    assert _col_value(pool, "provenance") == "system_pending_review"
    assert _col_value(pool, "scope_type") == "user"


def test_both_write_routes_require_authentication():
    for fn in (proc_api.create_procedure, proc_api.create_procedure_from_text):
        dep = inspect.signature(fn).parameters["principal"].default
        assert dep.dependency is require_authenticated_user


def test_empty_steps_are_dropped_not_stored_as_blank():
    pool = _FakePool()
    body = proc_api.ProcedureCreateBody(
        name="x", goal="y", steps=["real step", "   ", ""], embed=False
    )
    asyncio.run(proc_api.create_procedure(body, pool=pool, principal=PRINCIPAL))
    stored = _col_value(pool, "steps")
    assert stored == [{"order": 0, "goal": "real step"}]
