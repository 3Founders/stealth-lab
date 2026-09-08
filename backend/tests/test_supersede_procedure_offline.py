"""
Offline proving tests for procedures.supersede_procedure() -- the
next-version writer prompts.md Phase 2 §10 requires and that
capture_procedure()'s docstring has referenced since Band 1 without it
existing.

DB-free: a FakePool/FakeConn records every statement so the test proves
SQL CONTENT and WRITE BEHAVIOUR --
  * a new `procedures` row reusing `procedure_id`, `version = prior + 1`
  * carry-forward of every non-overridden column
  * `changed_fields` overriding exactly the columns it names
  * the prior row's validity window closed (t_invalid), never deleted
  * a SUPERSEDES edge new -> prior
  * a ChangeSet with a create_version + an invalidate op
  * a gone prior row -> None (no writes)
-- without a database. `record_change_set` is monkeypatched to capture
its call rather than emit SQL of its own (its own offline test covers
emission).
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.services.procedures import _SUPERSEDE_CARRY_COLUMNS, supersede_procedure

PRIOR_ROW_ID = "00000000-0000-4000-8000-0000000000a1"
PROCEDURE_ID = "00000000-0000-4000-8000-0000000000bb"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _prior_row(**over):
    row = {
        "id": UUID(PRIOR_ROW_ID),
        "procedure_id": UUID(PROCEDURE_ID),
        "version": 4,
        "family_id": None,
        "name": "fix-pandas-append-removal",
        "goal": "Fix AttributeError from pandas DataFrame.append() removal",
        "steps": [{"order": 0, "goal": "locate call sites"}],
        "parameter_schema": {"slots": []},
        "preconditions": [],
        "required_state": {},
        "expected_effects": [],
        "postconditions": [],
        "invariants": [{"kind": "numeric", "expr": "pandas_version >= 2.0"}],
        "failure_conditions": [],
        "scope": {},
        "exclusions": [],
        "verification_state": "verified",
        "staleness": "fresh",
        "availability": "active",
        "verification_stats": {"attempts": 12, "successes": 12, "distinct_contexts": 4},
        "evidence_refs": [{"kind": "prior_library"}],
        "source_episode_ids": [],
        "migrated_from_task_node_id": None,
        "provenance": "prior_library",
        "domain": None,
        "domain_payload": {"source": {"source_type": "skill_md_dir"}},
        "created_by": "skill_md_ingestion",
        "visibility": "public",
        "owner_id": None,
        "embedding": "[" + ",".join(["0.01"] * 8) + "]",
        "embedding_model_id": "voyage-x",
        "embedding_dim": 1024,
        "embedding_provider": "voyage",
        "embedding_input_type": "document",
        "embedding_text_hash": "a" * 64,
        "scope_type": "global",
        "scope_entity_id": None,
        "approval_status": "approved",
        "approved_by": "founder",
        "approved_at": None,
        "capability_statement": "Migrate a removed DataFrame API to its supported replacement",
        "extracted_by": None,
        "retrieval_document": "Name: fix pandas append removal\nPurpose: ...",
        "retrieval_document_version": "procdoc_v1",
        "retrieval_document_sha256": "b" * 64,
        "display_name": "Fix Pandas Append Removal",
        "display_description": "Migrate a removed DataFrame API to its supported replacement.",
        "display_metadata_version": "disp_v1",
    }
    row.update(over)
    return row


class _Row(dict):
    pass


class FakeConn:
    def __init__(self, prior_row):
        self._prior_row = prior_row
        self.statements: list[tuple[str, tuple]] = []
        self.committed = False
        self.rolled_back = False
        self.insert_cols: list[str] = []
        self.insert_params: tuple = ()

    class _TxnCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            self._conn.committed = exc_type is None
            self._conn.rolled_back = exc_type is not None
            return False

    def transaction(self):
        return FakeConn._TxnCM(self)

    async def fetchrow(self, sql, *args):
        norm = _norm(sql)
        self.statements.append((norm, args))
        if "FOR UPDATE" in norm:
            return _Row(self._prior_row) if self._prior_row is not None else None
        if norm.startswith("INSERT INTO procedures"):
            cols_blob = norm.split("(", 1)[1].split(")", 1)[0]
            self.insert_cols = [c.strip() for c in cols_blob.split(",")]
            self.insert_params = args
            return _Row({
                "id": args[0], "procedure_id": args[1], "version": args[2],
            })
        raise AssertionError(f"unexpected fetchrow: {norm[:120]}")

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "OK"

    def one(self, needle: str) -> tuple[str, tuple]:
        hits = [s for s in self.statements if needle in s[0]]
        assert len(hits) == 1, f"expected exactly one {needle!r}, got {len(hits)}"
        return hits[0]

    def none(self, needle: str) -> None:
        assert not [s for s in self.statements if needle in s[0]], f"unexpected {needle!r}"

    def inserted(self, col: str):
        assert col in self.insert_cols, f"{col!r} not in INSERT column list"
        return self.insert_params[self.insert_cols.index(col)]


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    class _AcquireCM:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakePool._AcquireCM(self._conn)


@pytest.fixture
def captured_changeset(monkeypatch):
    calls: list[dict] = []

    async def fake_record_change_set(pool, *, author, reason, operations, **kw):
        calls.append({"author": author, "reason": reason, "operations": operations})
        return UUID("00000000-0000-4000-8000-0000000000cc")

    monkeypatch.setattr(
        "app.services.changeset_record.record_change_set", fake_record_change_set
    )
    return calls


def _run(pool, **kw):
    return asyncio.run(supersede_procedure(pool, prior_row_id=PRIOR_ROW_ID, **kw))


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_new_version_reuses_procedure_id_and_increments_version(captured_changeset):
    conn = FakeConn(_prior_row())
    result = _run(FakePool(conn), changed_fields={"goal": "new goal text"})

    assert result["procedure_id"] == PROCEDURE_ID
    assert result["version"] == 5
    assert conn.inserted("procedure_id") == UUID(PROCEDURE_ID)
    assert conn.inserted("version") == 5
    assert str(conn.inserted("id")) != PRIOR_ROW_ID
    assert conn.committed and not conn.rolled_back


def test_unchanged_columns_are_carried_forward(captured_changeset):
    conn = FakeConn(_prior_row())
    _run(FakePool(conn), changed_fields={"goal": "new goal text"})

    # not named in changed_fields -> copied verbatim from the prior row
    assert conn.inserted("verification_stats") == {
        "attempts": 12, "successes": 12, "distinct_contexts": 4,
    }
    assert conn.inserted("invariants") == [
        {"kind": "numeric", "expr": "pandas_version >= 2.0"}
    ]
    assert conn.inserted("capability_statement") == (
        "Migrate a removed DataFrame API to its supported replacement"
    )
    assert conn.inserted("provenance") == "prior_library"


def test_changed_fields_win_over_the_prior_value(captured_changeset):
    conn = FakeConn(_prior_row())
    new_steps = [{"order": 0, "goal": "a"}, {"order": 1, "goal": "b"}]
    _run(
        FakePool(conn),
        changed_fields={"goal": "new goal text", "steps": new_steps,
                        "staleness": "fresh"},
    )
    assert conn.inserted("goal") == "new goal text"
    assert conn.inserted("steps") == new_steps


def test_prior_row_validity_window_is_closed_not_deleted(captured_changeset):
    conn = FakeConn(_prior_row())
    _run(FakePool(conn), changed_fields={"goal": "x"})

    sql, args = conn.one("UPDATE procedures SET t_invalid")
    assert "t_expired = $2" in sql
    assert args[0] == PRIOR_ROW_ID
    conn.none("DELETE FROM procedures")


def test_supersedes_edge_points_new_to_prior(captured_changeset):
    conn = FakeConn(_prior_row())
    result = _run(FakePool(conn), changed_fields={"goal": "x"})

    sql, args = conn.one("INSERT INTO edges")
    assert "'SUPERSEDES'" in sql
    assert args[0] == result["id"]          # source_id = new version row
    assert args[1] == PRIOR_ROW_ID          # target_id = prior version row


def test_records_a_changeset_with_create_version_and_invalidate(captured_changeset):
    conn = FakeConn(_prior_row())
    _run(FakePool(conn), superseded_by="skill_md_ingestion",
         changed_fields={"goal": "x"})

    assert len(captured_changeset) == 1
    call = captured_changeset[0]
    assert call["author"] == "skill_md_ingestion"
    ops = {op.operation for op in call["operations"]}
    assert ops == {"create_version", "invalidate"}
    cv = next(op for op in call["operations"] if op.operation == "create_version")
    assert cv.detail["version"] == 5
    assert cv.detail["supersedes_row_id"] == PRIOR_ROW_ID


def test_every_carry_column_is_present_in_the_insert(captured_changeset):
    conn = FakeConn(_prior_row())
    _run(FakePool(conn), changed_fields={"goal": "x"})
    for col in _SUPERSEDE_CARRY_COLUMNS:
        assert col in conn.insert_cols, f"carry column {col!r} missing from INSERT"


# ---------------------------------------------------------------------
# No-op / guard paths
# ---------------------------------------------------------------------


def test_gone_prior_row_returns_none_and_writes_nothing(captured_changeset):
    conn = FakeConn(None)
    result = _run(FakePool(conn), changed_fields={"goal": "x"})

    assert result is None
    conn.none("INSERT INTO procedures")
    conn.none("INSERT INTO edges")
    assert captured_changeset == []


def test_unknown_changed_field_is_rejected(captured_changeset):
    conn = FakeConn(_prior_row())
    with pytest.raises(ValueError, match="non-carry columns"):
        _run(FakePool(conn), changed_fields={"not_a_real_column": 1})
