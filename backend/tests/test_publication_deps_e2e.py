"""
G24 -- publication dependency traversal against a real Postgres.

The offline suite (test_publication_deps_offline.py) proves the verdict
logic with a routing FakePool. This file proves the SQL actually matches
the live schema (migrations 26 / 64 / 66) and that the blocking verdict
holds end to end: a private Source in a procedure's claim lineage blocks
publication; an all-public lineage does not.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.claims import capture_claim
from app.services.procedure_claim_refs import add_procedure_claim_ref
from app.services.procedures import capture_procedure
from app.services.publication_deps import traverse_publication_dependencies
from app.services.sources import register_source

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live publication-traversal test"
)

_MARK = "test-pubdeps-e2e"


async def _cleanup(pool) -> None:
    ids = [r["id"] for r in await pool.fetch(
        "SELECT id FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{_MARK}%")]
    for cid in ids:
        await pool.execute("DELETE FROM procedure_claim_refs WHERE claim_id=$1", cid)
        await pool.execute("DELETE FROM claim_sources WHERE claim_id=$1", cid)
    await pool.execute("DELETE FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{_MARK}%")
    await pool.execute("UPDATE sources SET t_invalid = now() WHERE locator LIKE $1 AND t_invalid IS NULL",
                       f"{_MARK}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{_MARK}%")


async def _procedure_with_claim(pool, *, source_visibility: str) -> dict:
    tag = uuid.uuid4().hex[:8]
    proc = await capture_procedure(
        pool, name=f"{_MARK}-{source_visibility}-{tag}", goal="g",
        provenance="system_pending_review", scope_type="global",
        steps=[{"order": 0, "goal": "x"}],
    )
    src = await register_source(
        pool, source_type="document", locator=f"{_MARK}://{source_visibility}/{tag}",
        publisher="pub", created_by="t", provenance="prior_library",
        visibility=source_visibility, scope_type="global",
        owner_id=("owner-1" if source_visibility != "public" else None),
        reliability_score=0.9, reliability_method="manual",
    )
    claim_id = await capture_claim(
        pool, statement=f"{_MARK}: {source_visibility} lineage {tag}", task_ids=[],
        source_ref=src["id"], created_by="t", scope_type="global", visibility="public",
    )
    assert claim_id, "claim must be written"
    await add_procedure_claim_ref(
        pool, procedure_id=str(proc["procedure_id"]), procedure_version=1,
        claim_id=claim_id, role="RATIONALE", ref_origin="derived", created_by="t",
    )
    return {"row_id": proc["id"], "procedure_id": str(proc["procedure_id"]),
            "version": 1, "source_id": src["id"], "claim_id": claim_id}


def test_private_source_in_claim_lineage_blocks_publication():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)
            p = await _procedure_with_claim(pool, source_visibility="private")
            report = await traverse_publication_dependencies(
                pool, procedure_row_id=str(p["row_id"]),
                procedure_id=p["procedure_id"], procedure_version=p["version"],
            )
            # the SQL ran against the real schema -> the claim leg is reached
            assert any(str(c["id"]) == p["claim_id"] for c in report["claims"]), report["counts"]
            # and the private source is a blocker
            src_blockers = [b for b in report["blocking"] if b["kind"] == "source"]
            assert src_blockers, f"private source must block; blocking={report['blocking']}"
            assert any(str(b["id"]) == p["source_id"] for b in src_blockers)
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_all_public_lineage_does_not_block():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)
            p = await _procedure_with_claim(pool, source_visibility="public")
            report = await traverse_publication_dependencies(
                pool, procedure_row_id=str(p["row_id"]),
                procedure_id=p["procedure_id"], procedure_version=p["version"],
            )
            assert any(str(c["id"]) == p["claim_id"] for c in report["claims"])
            assert [b for b in report["blocking"] if b["kind"] in ("claim", "source")] == [], report["blocking"]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
