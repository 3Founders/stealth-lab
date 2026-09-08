"""
Offline: capture_procedure()'s retrieval-representation + display-metadata
contract (plan Part 18). DB-free -- a FakePool records the INSERT so the
test proves what would be persisted.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from app.services.procedures import capture_procedure
from app.services.retrieval_document import RETRIEVAL_DOCUMENT_IMPORT_VERSION


class _FakePool:
    def __init__(self):
        self.insert_sql = None
        self.insert_args = None

    async def fetchrow(self, sql, *args):
        self.insert_sql = sql
        self.insert_args = args
        return {"id": uuid4(), "procedure_id": uuid4()}

    async def execute(self, *a, **k):  # pragma: no cover - unused here
        return "OK"


def _col_value(pool, column: str):
    """Pull one column's bound value out of the captured INSERT by mapping
    the column-list position to its ``$N`` placeholder (the INSERT does not
    bind columns in positional order -- ``id`` is listed first but bound as
    ``$24``)."""
    head = pool.insert_sql.split("INSERT INTO procedures (", 1)[1]
    col_str, rest = head.split(")", 1)
    columns = [c.strip() for c in col_str.replace("\n", " ").split(",")]
    values_str = rest.split("VALUES", 1)[1].split(")", 1)[0]
    placeholders = [
        p.strip() for p in values_str.replace("(", "").replace("\n", " ").split(",")
    ]
    ph = placeholders[columns.index(column)]           # "$33" or "$22::visibility_level"
    n = int(ph.lstrip("$").split("::")[0])
    return pool.insert_args[n - 1]


def test_display_metadata_never_null_even_without_caller_supplying_it():
    pool = _FakePool()
    asyncio.run(capture_procedure(
        pool, name="mcp-lazy-tool-schema-loading",
        goal="Use when an agent's tool surface is large and per-turn context is costly.",
        steps=[{"order": 0, "goal": "list tool names only"}],
        provenance="prior_library", scope_type="global",
    ))
    assert _col_value(pool, "display_name") == "MCP Lazy Tool Schema Loading"
    assert _col_value(pool, "display_description")
    assert _col_value(pool, "display_metadata_version")


def test_embedding_without_version_gets_import_sentinel_and_a_built_document():
    pool = _FakePool()
    asyncio.run(capture_procedure(
        pool, name="published-local-procedure",
        goal="Do a concrete useful thing for a concrete reason.",
        steps=[{"order": 0, "goal": "step one"}],
        provenance="system_pending_review", scope_type="global",
        embedding=[0.01] * 1024,
    ))
    assert _col_value(pool, "retrieval_document_version") == RETRIEVAL_DOCUMENT_IMPORT_VERSION
    doc = _col_value(pool, "retrieval_document")
    assert doc and "Purpose: Do a concrete useful thing" in doc
    assert _col_value(pool, "retrieval_document_sha256")


def test_caller_supplied_canonical_representation_is_trusted_verbatim():
    pool = _FakePool()
    asyncio.run(capture_procedure(
        pool, name="x", goal="g", steps=[],
        provenance="prior_library", scope_type="global",
        embedding=[0.02] * 1024,
        retrieval_document="Name: x\nPurpose: g",
        retrieval_document_version="procdoc_v1",
        retrieval_document_sha256="c" * 64,
        display_name="Do X well", display_description="A clear human sentence about X.",
        display_metadata_version="disp_v1",
    ))
    assert _col_value(pool, "retrieval_document_version") == "procdoc_v1"
    assert _col_value(pool, "retrieval_document") == "Name: x\nPurpose: g"
    assert _col_value(pool, "display_name") == "Do X well"
