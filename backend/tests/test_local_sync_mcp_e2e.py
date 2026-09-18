"""
Selective local -> global sync (workflow C): `preview_local_sync` /
`commit_local_sync` MCP tools end to end, against a hand-built local
`.stealth/claims.md` (+ meta.json) -- exercising every classification
(`NEW` / `CHANGED` / `ALREADY_SYNCED` / `LOCAL_ONLY` / `CONFLICTING`), the
"never sync everything implicitly" contract, the "never auto-promote
LOCAL_ONLY" contract, and the commit-time race/staleness re-validation
(mutate the backend between preview and commit; commit must re-classify,
never blindly trust the stale preview).

Same pattern as `test_run_collaboration_mcp_e2e.py`/`test_stealth_
exploration_e2e.py`: skips (never fails) without a real DATABASE_URL,
self-cleaning by name prefix.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.stealth.pipe_format import ClaimLine, render_claims_md

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

_MARK = "test-local-sync-e2e"


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, statement_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{statement_prefix}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND name LIKE $1", f"{statement_prefix}%")


def _write_meta(repo_dir: str, generated_at: str) -> None:
    stealth_dir = os.path.join(repo_dir, ".stealth")
    os.makedirs(stealth_dir, exist_ok=True)
    with open(os.path.join(stealth_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "stealth-projection/2", "generated_at": generated_at}, f)


def _write_claims_md(repo_dir: str, lines: list[ClaimLine]) -> None:
    stealth_dir = os.path.join(repo_dir, ".stealth")
    os.makedirs(stealth_dir, exist_ok=True)
    with open(os.path.join(stealth_dir, "claims.md"), "w", encoding="utf-8") as f:
        f.write(render_claims_md(lines))


async def _capture_real_claim(pool, statement: str, owner_id: str) -> str:
    from app.services.claims import capture_claim
    from app.services.sources import register_source

    src = await register_source(
        pool, source_type="agent_execution", locator=f"test-local-sync:{uuid.uuid4().hex}",
        provenance="company_ingested", created_by="test_local_sync", visibility="public",
    )
    new_id = await capture_claim(
        pool, statement=statement, task_ids=[], source_ref=src["id"],
        created_by="test_local_sync", owner_id=owner_id, visibility="public", scope_type="global",
    )
    assert new_id is not None
    return new_id


def test_preview_classifies_every_case():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        prefix = f"{_MARK}-{tag}"
        try:
            unchanged_id = await _capture_real_claim(pool, f"{prefix} unchanged statement", "owner-x")
            changed_id = await _capture_real_claim(pool, f"{prefix} original statement", "owner-x")
            dangling_id = str(uuid.uuid4())  # never written -- simulates a stale/deleted backend row

            with tempfile.TemporaryDirectory() as repo_dir:
                _write_meta(repo_dir, generated_at="2020-01-01T00:00:00+00:00")
                _write_claims_md(repo_dir, [
                    ClaimLine(claim_id=unchanged_id, status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} unchanged statement", source="unknown"),
                    ClaimLine(claim_id=changed_id, status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} EDITED statement", source="unknown"),
                    ClaimLine(claim_id="local-hand-note-1", status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} a hand-added claim with no backend id", source="unknown"),
                    ClaimLine(claim_id=str(uuid.uuid4()), status="UNKNOWN", topic="fact", scope="local",
                              statement=f"{prefix} a private local-only note", source="unknown"),
                    ClaimLine(claim_id=dangling_id, status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} references a claim id that no longer exists", source="unknown"),
                ])

                ctx = _FakeContext(pool)
                candidates = json.loads(await srv.preview_local_sync(repo_path=repo_dir, ctx=ctx))
                by_backend = {c["backend_id"]: c for c in candidates}

                assert by_backend[unchanged_id]["classification"] == "ALREADY_SYNCED"
                assert by_backend[changed_id]["classification"] == "CHANGED"
                assert by_backend[dangling_id]["classification"] == "CONFLICTING"

                new_ones = [c for c in candidates if c["classification"] == "NEW"]
                assert any("hand-added" in c["local_summary"] for c in new_ones)

                local_only = [c for c in candidates if c["classification"] == "LOCAL_ONLY"]
                assert any("private local-only" in c["local_summary"] for c in local_only)
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_commit_only_syncs_selected_ids_and_never_auto_publishes_local_only():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        prefix = f"{_MARK}-commit-{tag}"
        try:
            with tempfile.TemporaryDirectory() as repo_dir:
                _write_meta(repo_dir, generated_at="2020-01-01T00:00:00+00:00")
                local_only_id = str(uuid.uuid4())
                _write_claims_md(repo_dir, [
                    ClaimLine(claim_id="local-new-1", status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} new claim to sync", source="unknown"),
                    ClaimLine(claim_id="local-new-2", status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} new claim NOT selected", source="unknown"),
                    ClaimLine(claim_id=local_only_id, status="UNKNOWN", topic="fact", scope="private",
                              statement=f"{prefix} local only claim", source="unknown"),
                ])

                ctx = _FakeContext(pool)
                candidates = json.loads(await srv.preview_local_sync(repo_path=repo_dir, ctx=ctx))
                by_summary = {c["local_summary"]: c for c in candidates}
                selected_cid = by_summary[f"{prefix} new claim to sync"]["candidate_id"]
                not_selected_cid = by_summary[f"{prefix} new claim NOT selected"]["candidate_id"]
                local_only_cid = by_summary[f"{prefix} local only claim"]["candidate_id"]
                assert by_summary[f"{prefix} local only claim"]["classification"] == "LOCAL_ONLY"

                # commit ONLY the first + attempt the local-only one without permission
                results = json.loads(await srv.commit_local_sync(
                    repo_path=repo_dir, selected_ids_json=json.dumps([selected_cid, local_only_cid]), ctx=ctx,
                ))
                by_cid = {r["candidate_id"]: r for r in results}

                assert by_cid[selected_cid]["outcome"] == "committed"
                new_claim_id = by_cid[selected_cid]["id"]
                row = await pool.fetchrow("SELECT id, name FROM knowledge_nodes WHERE id = $1::uuid", new_claim_id)
                assert row is not None

                assert by_cid[local_only_cid]["outcome"] == "refused"

                # the un-selected candidate must never appear as committed
                assert not_selected_cid not in by_cid
                unselected_row = await pool.fetchrow(
                    "SELECT id FROM knowledge_nodes WHERE name LIKE $1", f"%{prefix} new claim NOT selected%",
                )
                assert unselected_row is None

                # now explicitly allow the local-only item
                results2 = json.loads(await srv.commit_local_sync(
                    repo_path=repo_dir, selected_ids_json=json.dumps([local_only_cid]), ctx=ctx,
                    allow_local_only=True,
                ))
                assert results2[0]["outcome"] == "committed"
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_commit_refuses_empty_selection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as repo_dir:
                ctx = _FakeContext(pool)
                result = await srv.commit_local_sync(repo_path=repo_dir, selected_ids_json="[]", ctx=ctx)
                assert result.startswith("REFUSED:")
        finally:
            await pool.close()

    asyncio.run(_run())


def test_commit_reclassifies_at_commit_time_when_backend_changed_since_preview():
    """The race this feature exists to close: preview shows CHANGED (a
    real, syncable diff); before commit runs, the backend claim is
    superseded (simulating a second writer / a second agent). Commit must
    re-derive classification from CURRENT state -- CONFLICTING now -- and
    skip, never blindly acting on the stale preview's CHANGED verdict."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        prefix = f"{_MARK}-race-{tag}"
        try:
            from app.services.claims import relate_claims

            original_id = await _capture_real_claim(pool, f"{prefix} original text", "owner-race")

            with tempfile.TemporaryDirectory() as repo_dir:
                _write_meta(repo_dir, generated_at="2020-01-01T00:00:00+00:00")
                _write_claims_md(repo_dir, [
                    ClaimLine(claim_id=original_id, status="UNKNOWN", topic="fact", scope="global",
                              statement=f"{prefix} EDITED text", source="unknown"),
                ])
                ctx = _FakeContext(pool)

                preview = json.loads(await srv.preview_local_sync(repo_path=repo_dir, ctx=ctx))
                assert preview[0]["classification"] == "CHANGED"
                cid = preview[0]["candidate_id"]

                # a second writer supersedes the backend claim, dated after
                # this local projection's own meta.json generated_at.
                superseding_id = await _capture_real_claim(pool, f"{prefix} superseding text", "owner-race")
                await relate_claims(pool, from_claim_id=superseding_id, to_claim_id=original_id,
                                     relation="SUPERSEDES", created_by="test_local_sync", propagate=False)

                results = json.loads(await srv.commit_local_sync(
                    repo_path=repo_dir, selected_ids_json=json.dumps([cid]), ctx=ctx,
                ))
                assert results[0]["outcome"] == "skipped"
                assert "changed" in results[0]["reason"].lower() or "conflict" in results[0]["reason"].lower() \
                    or "supersed" in results[0]["reason"].lower()

                # and no new claim was written from the stale CHANGED verdict
                stray = await pool.fetchrow(
                    "SELECT id FROM knowledge_nodes WHERE name LIKE $1", f"%{prefix} EDITED text%",
                )
                assert stray is None
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())
