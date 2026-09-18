"""
The stealth edit ledger (migration 92): the `record_stealth_edit` /
`list_stealth_edits` MCP tools, `.stealth/ledger.md` rendering after a
write, filtering by `file_path`, newest-first ordering, and refusal of an
unrecognized `file_path`.

Same pattern as every other `*_e2e.py` file here (test_run_collaboration_
mcp_e2e.py is the closest model, both being append-only-log MCP tools):
skips without a real DATABASE_URL, self-cleaning by `project_id` (derived
deterministically from each test's own throwaway `repo_path`, so cleanup
never touches another test's rows or real workspace data).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.procedure_extraction import _project_id_from_repo_root
from app.stealth.edit_ledger import EditLedgerError, list_stealth_edits, record_stealth_edit

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, project_id: str) -> None:
    await pool.execute("DELETE FROM stealth_edit_ledger WHERE project_id = $1", project_id)


def test_record_and_list_via_mcp_tools():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            project_id = _project_id_from_repo_root(repo_dir)
            ctx = _FakeContext(pool)
            try:
                entry = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="claims.md", summary="corrected a claim statement",
                    ctx=ctx, actor="anuj",
                ))
                assert entry["project_id"] == project_id
                assert entry["file_path"] == "claims.md"
                assert entry["actor"] == "anuj"
                assert entry["summary"] == "corrected a claim statement"
                assert entry["ledger_projection"] == "written"

                listed = json.loads(await srv.list_stealth_edits(repo_path=repo_dir, ctx=ctx))
                assert len(listed) == 1
                assert listed[0]["id"] == entry["id"]
            finally:
                await _cleanup(pool, project_id)
                await pool.close()

    asyncio.run(_run())


def test_unrecognized_file_path_is_refused():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            project_id = _project_id_from_repo_root(repo_dir)
            ctx = _FakeContext(pool)
            try:
                refused = await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="not_a_real_file.md", summary="x", ctx=ctx, actor="anuj",
                )
                assert refused.startswith("REFUSED:")

                refused_ledger = await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="ledger.md", summary="x", ctx=ctx, actor="anuj",
                )
                assert refused_ledger.startswith("REFUSED:")

                with pytest.raises(EditLedgerError):
                    await record_stealth_edit(
                        pool, project_id=project_id, file_path="bogus.md", actor="anuj", summary="x",
                    )

                listed = json.loads(await srv.list_stealth_edits(repo_path=repo_dir, ctx=ctx))
                assert listed == []
            finally:
                await _cleanup(pool, project_id)
                await pool.close()

    asyncio.run(_run())


def test_ledger_md_projection_renders_after_a_write():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            project_id = _project_id_from_repo_root(repo_dir)
            ctx = _FakeContext(pool)
            try:
                entry = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="run.md", summary="tweaked a node note",
                    ctx=ctx, actor="agent-x",
                ))
                assert entry["ledger_projection"] == "written"

                ledger_path = os.path.join(repo_dir, ".stealth", "ledger.md")
                with open(ledger_path, encoding="utf-8") as f:
                    ledger_md = f.read()

                assert "ledger.md -- GENERATED, not canonical" in ledger_md
                assert f"EDIT|{entry['id']}|agent-x|run.md|" in ledger_md
                assert "tweaked a node note" in ledger_md
            finally:
                await _cleanup(pool, project_id)
                await pool.close()

    asyncio.run(_run())


def test_filter_by_file_path():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            project_id = _project_id_from_repo_root(repo_dir)
            ctx = _FakeContext(pool)
            try:
                claims_entry = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="claims.md", summary="edit 1", ctx=ctx, actor="anuj",
                ))
                json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="goals.md", summary="edit 2", ctx=ctx, actor="anuj",
                ))

                only_claims = json.loads(await srv.list_stealth_edits(
                    repo_path=repo_dir, ctx=ctx, file_path="claims.md",
                ))
                assert len(only_claims) == 1
                assert only_claims[0]["id"] == claims_entry["id"]
                assert only_claims[0]["file_path"] == "claims.md"

                unfiltered = json.loads(await srv.list_stealth_edits(repo_path=repo_dir, ctx=ctx))
                assert len(unfiltered) == 2
            finally:
                await _cleanup(pool, project_id)
                await pool.close()

    asyncio.run(_run())


def test_ordering_is_newest_first():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            project_id = _project_id_from_repo_root(repo_dir)
            ctx = _FakeContext(pool)
            try:
                first = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="claims.md", summary="first edit", ctx=ctx, actor="anuj",
                ))
                second = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="claims.md", summary="second edit", ctx=ctx, actor="anuj",
                ))
                third = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="claims.md", summary="third edit", ctx=ctx, actor="anuj",
                ))

                listed = json.loads(await srv.list_stealth_edits(repo_path=repo_dir, ctx=ctx))
                assert [e["id"] for e in listed] == [third["id"], second["id"], first["id"]]

                # service-layer function directly too, same ordering guarantee
                direct = await list_stealth_edits(pool, project_id=project_id)
                assert [str(r["id"]) for r in direct] == [third["id"], second["id"], first["id"]]
            finally:
                await _cleanup(pool, project_id)
                await pool.close()

    asyncio.run(_run())


def test_actor_falls_back_to_resolved_caller_identity_when_omitted():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            project_id = _project_id_from_repo_root(repo_dir)
            ctx = _FakeContext(pool)
            try:
                entry = json.loads(await srv.record_stealth_edit(
                    repo_path=repo_dir, file_path="claims.md", summary="no actor given", ctx=ctx,
                ))
                assert entry["actor"]  # non-empty, resolved rather than omitted/None
            finally:
                await _cleanup(pool, project_id)
                await pool.close()

    asyncio.run(_run())
