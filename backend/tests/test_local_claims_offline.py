"""
Offline (SQLite-only, no Postgres) tests for
`app/local_agent/local_claims.py` -- mirrors
`test_local_agent_runner_offline.py`'s structural-boundary test and
`local_store.py`'s own capture/publish-link contract, applied to claims.
"""
from __future__ import annotations

import ast
import inspect
import tempfile
from pathlib import Path

import pytest

from app.local_agent.local_claims import (
    LocalClaimNotFound,
    LocalClaimStore,
    capture_local_claim,
)
from app.services.v0_gate import V0Violation

LOCAL_CLAIMS_SOURCE_PATH = Path(inspect.getfile(
    __import__("app.local_agent.local_claims", fromlist=["local_claims"])
))


def test_local_claims_module_never_imports_the_database():
    """Structural, not conventional -- same AST-parsed proof
    `test_local_agent_runner_offline.py::test_runner_module_never_imports_the_database`
    uses for `runner.py`, applied to `local_claims.py`."""
    tree = ast.parse(LOCAL_CLAIMS_SOURCE_PATH.read_text(encoding="utf-8"))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    forbidden = {"asyncpg", "app.db.session", "app.db"}
    hit = forbidden & imported_names
    assert not hit, f"local_agent.local_claims must not import the database, found: {hit}"


def test_capture_local_claim_writes_a_row_scoped_to_repository():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        result = capture_local_claim(
            store,
            statement="this repository centralizes DB access in src/db",
            subject="src/db",
            predicate="centralizes",
            object="database access",
            repo_root=tmp_dir,
            created_by="tester@example.com",
        )
        row = store.get_local_claim(result["id"])
        assert row is not None
        assert row["statement"] == "this repository centralizes DB access in src/db"
        assert row["subject"] == "src/db"
        assert row["predicate"] == "centralizes"
        assert row["object"] == "database access"
        assert row["truth_state"] == "IN"
        assert row["scope_type"] == "repository"
        # scope_entity_id defaults to repo_root when not explicitly given
        assert row["scope_entity_id"] == tmp_dir
        assert row["created_by"] == "tester@example.com"
        assert row["repo_root"] == tmp_dir
        assert row["created_at"]
        # never published on capture
        assert row["published_claim_id"] is None
        assert row["published_claim_row_id"] is None
        assert row["published_by"] is None
        assert row["published_at"] is None


def test_capture_local_claim_requires_created_by():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        with pytest.raises(ValueError):
            capture_local_claim(
                store, statement="x", repo_root=tmp_dir, created_by="",
            )


def test_capture_local_claim_rejects_bad_truth_state():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        with pytest.raises(ValueError):
            capture_local_claim(
                store, statement="x", repo_root=tmp_dir,
                created_by="tester@example.com", truth_state="MAYBE",
            )


def test_capture_local_claim_reuses_the_real_v0_scope_gate():
    """An explicitly bad scope_type is rejected by the SAME V0Violation
    capture_procedure()/LocalProcedureStore raise -- not a locally
    re-invented, weaker check."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        with pytest.raises(V0Violation):
            capture_local_claim(
                store, statement="x", repo_root=tmp_dir,
                created_by="tester@example.com", scope_type="not-a-real-scope-type",
            )


def test_get_local_claim_unknown_row_returns_none():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        assert store.get_local_claim("does-not-exist") is None


def test_mark_local_claim_published_unknown_row_raises_not_found():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        with pytest.raises(LocalClaimNotFound):
            store.mark_local_claim_published(
                "does-not-exist",
                global_claim_id="g1",
                global_claim_row_id="g1",
                published_by="tester@example.com",
            )


def test_list_local_claims_returns_captured_rows():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalClaimStore(repo_root=tmp_dir)
        capture_local_claim(
            store, statement="claim one", repo_root=tmp_dir,
            created_by="tester@example.com",
        )
        capture_local_claim(
            store, statement="claim two", repo_root=tmp_dir,
            created_by="tester@example.com",
        )
        rows = store.list_local_claims()
        statements = {r["statement"] for r in rows}
        assert statements == {"claim one", "claim two"}


def test_local_claim_migration_is_idempotent_across_reopen():
    """Reopening the same db file (a second LocalClaimStore against the
    same path) must not error re-adding the publish-link columns --
    mirrors `local_store.py`'s own PRAGMA-table_info-then-ALTER
    idempotency guarantee."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "claims.db")
        store_a = LocalClaimStore(db_path=db_path)
        result = capture_local_claim(
            store_a, statement="x", repo_root=tmp_dir,
            created_by="tester@example.com",
        )
        store_b = LocalClaimStore(db_path=db_path)
        row = store_b.get_local_claim(result["id"])
        assert row is not None
        assert row["statement"] == "x"


def test_no_automatic_local_to_global_publish_path_exists():
    """Proves the audit's own flagged question ('local claims must not
    automatically become global knowledge') structurally: nothing in
    LocalClaimStore or capture_local_claim imports or calls
    publish_local_claim. The only way a local claim reaches the global
    table is an explicit, separate call to publish_local_claim -- which
    itself requires an explicit published_by (see
    test_local_claims_publish_e2e.py for the live-DB half of this
    proof)."""
    import re

    from app import local_agent
    module_source = LOCAL_CLAIMS_SOURCE_PATH.read_text(encoding="utf-8")

    # capture_local_claim / LocalClaimStore.capture_local_claim never
    # invoke publish_local_claim themselves.
    capture_section = module_source.split("async def publish_local_claim")[0]
    call_re = re.compile(r"publish_local_claim\s*\(")
    assert not call_re.search(capture_section), (
        "capture_local_claim / LocalClaimStore must never call "
        "publish_local_claim themselves -- publication is explicit-only"
    )
