"""
Structured run collaboration records (NOTE/BLOCKER/HANDOFF/QUESTION/
ANSWER, migration 90) end to end: the `record_run_update` MCP tool,
ANSWER correctly referencing its QUESTION, `.stealth/run.md` rendering
the recorded records after a write, and a fallback/resilience test
proving a projection-write failure never corrupts the canonical DB
record (product spec Case 9).

Same pattern as every other `*_e2e.py` file here (test_declare_file_
intent_mcp_e2e.py is the closest model): skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.execution.run_collaboration import (
    RunCollaborationError,
    list_run_collaboration,
    record_run_update,
)
from app.services.procedures import capture_procedure

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


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _start_run(pool, procedure: dict) -> str:
    steps = procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_run_collaboration_mcp_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
    )


def test_record_each_kind_via_mcp_tool():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-collabkinds-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            note = json.loads(await srv.record_run_update(
                run_id=run_id, kind="NOTE", body="context for the next agent", ctx=ctx,
            ))
            assert note["kind"] == "NOTE" and note["execution_run_id"] == run_id

            blocker = json.loads(await srv.record_run_update(
                run_id=run_id, kind="BLOCKER", body="waiting on external API creds",
                node_order=0, ctx=ctx,
            ))
            assert blocker["kind"] == "BLOCKER" and blocker["node_order"] == 0

            handoff = json.loads(await srv.record_run_update(
                run_id=run_id, kind="HANDOFF", body="please continue node 0",
                node_order=0, target_agent_id="agent-b", ctx=ctx,
            ))
            assert handoff["kind"] == "HANDOFF" and handoff["target_agent_id"] == "agent-b"

            question = json.loads(await srv.record_run_update(
                run_id=run_id, kind="QUESTION", body="is retry safe here?", ctx=ctx,
            ))
            assert question["kind"] == "QUESTION"

            answer = json.loads(await srv.record_run_update(
                run_id=run_id, kind="ANSWER", body="yes, the endpoint is idempotent",
                answers_id=question["id"], ctx=ctx,
            ))
            assert answer["kind"] == "ANSWER" and answer["answers_id"] == question["id"]

            records = await list_run_collaboration(pool, run_id)
            kinds = {r["kind"] for r in records}
            assert kinds == {"NOTE", "BLOCKER", "HANDOFF", "QUESTION", "ANSWER"}
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_answer_refused_without_a_real_question_in_this_run():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-collabbadanswer-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            bad = await srv.record_run_update(
                run_id=run_id, kind="ANSWER", body="an answer to nothing",
                answers_id=str(uuid4()), ctx=ctx,
            )
            assert bad.startswith("REFUSED:")

            bad_kind = await srv.record_run_update(
                run_id=run_id, kind="NOT_A_REAL_KIND", body="x", ctx=ctx,
            )
            assert bad_kind.startswith("REFUSED:")

            with pytest.raises(RunCollaborationError):
                await record_run_update(
                    pool, execution_run_id=run_id, kind="ANSWER", body="orphan answer",
                    actor_agent_id="test",
                )
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_run_md_projection_renders_collaboration_records():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-collabrunmd-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            with tempfile.TemporaryDirectory() as repo_dir:
                result = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="BLOCKER", body="blocked on shared/collab-marker.py",
                    node_order=0, ctx=ctx, repo_path=repo_dir,
                ))
                assert result["stealth_projection"] == "written"

                run_md_path = os.path.join(repo_dir, ".stealth", "run.md")
                with open(run_md_path, encoding="utf-8") as f:
                    run_md = f.read()

                assert "COLLAB_SUMMARY" in run_md
                assert "open_blockers=1" in run_md
                assert "BLOCKER" in run_md
                assert "blocked on shared/collab-marker.py" in run_md
                assert result["id"] in run_md
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_projection_write_failure_does_not_corrupt_canonical_db_record():
    """Case 9 fallback: an unwriteable `repo_path` (a real FILE, not a
    directory, so `.stealth/` cannot be created under it) must surface as
    `stealth_projection: write_failed: ...` -- and the DB row from the
    SAME call must still exist, correct, and durable. The canonical write
    and the best-effort projection write are not one transaction."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-collabfallback-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            with tempfile.NamedTemporaryFile(delete=False) as f:
                unwriteable_repo_path = f.name
            try:
                result = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="NOTE", body="should survive a bad repo_path",
                    ctx=ctx, repo_path=unwriteable_repo_path,
                ))
                assert result["stealth_projection"].startswith("write_failed:")

                row = await pool.fetchrow(
                    "SELECT kind, body, execution_run_id FROM run_collaboration_records WHERE id = $1::uuid",
                    result["id"],
                )
                assert row is not None, "the canonical DB record must survive a projection write failure"
                assert row["kind"] == "NOTE"
                assert row["body"] == "should survive a bad repo_path"
                assert str(row["execution_run_id"]) == str(run_id)
                # The best-effort journal mirror is independent of the DB
                # write too -- same unwriteable repo_path also fails it,
                # surfaced (not swallowed), and the DB row above still
                # proves the canonical write was never touched by it.
                assert result["journal"].startswith("write_failed:")
            finally:
                os.unlink(unwriteable_repo_path)
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_blocker_resolved_and_handoff_accepted_decrement_the_run_md_summary():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-collabresolve-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            with tempfile.TemporaryDirectory() as repo_dir:
                blocker = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="BLOCKER", body="blocked on staging creds",
                    node_order=0, ctx=ctx, repo_path=repo_dir,
                ))
                handoff = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="HANDOFF", body="node 0 ready for review",
                    node_order=0, target_agent_id="agent-c", ctx=ctx, repo_path=repo_dir,
                ))

                run_md_path = os.path.join(repo_dir, ".stealth", "run.md")
                with open(run_md_path, encoding="utf-8") as f:
                    assert "open_blockers=1" in f.read()
                    f.seek(0)
                    assert "pending_handoffs=1" in f.read()

                resolved = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="BLOCKER_RESOLVED", body="creds arrived",
                    answers_id=blocker["id"], ctx=ctx, repo_path=repo_dir,
                ))
                assert resolved["kind"] == "BLOCKER_RESOLVED" and resolved["answers_id"] == blocker["id"]

                accepted = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="HANDOFF_ACCEPTED", body="picked it up",
                    answers_id=handoff["id"], ctx=ctx, repo_path=repo_dir,
                ))
                assert accepted["kind"] == "HANDOFF_ACCEPTED" and accepted["answers_id"] == handoff["id"]

                with open(run_md_path, encoding="utf-8") as f:
                    run_md = f.read()
                assert "open_blockers=0" in run_md
                assert "pending_handoffs=0" in run_md
                assert "BLOCKER_RESOLVED" in run_md and "creds arrived" in run_md
                assert "HANDOFF_ACCEPTED" in run_md and "picked it up" in run_md

                # a resolving kind refuses when answers_id points at the wrong kind
                mismatched = await srv.record_run_update(
                    run_id=run_id, kind="BLOCKER_RESOLVED", body="wrong target",
                    answers_id=handoff["id"], ctx=ctx,
                )
                assert mismatched.startswith("REFUSED:")
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_record_run_update_mirrors_into_the_local_journal_when_repo_path_given():
    """The best-effort `.stealth/events.jsonl` mirror -- purely additive
    local logging, never read back (see `app.stealth.journal.read_events`'s
    only real readers: exploration folding and the P3 page-fault index,
    neither of which this event type participates in)."""
    async def _run():
        from app.stealth.journal import read_events

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-collabjournal-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            with tempfile.TemporaryDirectory() as repo_dir:
                result = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="NOTE", body="mirrored into the journal too",
                    ctx=ctx, repo_path=repo_dir,
                ))
                assert result["journal"] == "written"

                events = read_events(repo_dir)
                matches = [e for e in events if e.get("type") == "run_collaboration_recorded"]
                assert len(matches) == 1
                evt = matches[0]
                assert evt["record_id"] == result["id"]
                assert evt["execution_run_id"] == run_id
                assert evt["kind"] == "NOTE"

            # no repo_path -> no journal write attempted at all (nothing to mirror into)
            no_repo_result = json.loads(await srv.record_run_update(
                run_id=run_id, kind="NOTE", body="no repo_path given", ctx=ctx,
            ))
            assert "journal" not in no_repo_result
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
