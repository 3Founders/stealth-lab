"""
G13 P3 -- `.stealth/` knowledge page fault (`app.stealth.faults`)
against a real Postgres. Skips without DATABASE_URL, self-cleaning.
"""
import asyncio
import json
import os
import tempfile
import uuid

import pytest

from app.db.session import create_pool
from app.stealth import generate_projection, project_knowledge
from app.stealth.faults import read_faulted
from app.stealth.format import parse_idx

from tests.test_stealth_projection_e2e import _capture, _cleanup, _start_run  # reuse helpers

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database page-fault test"
)

_MARK = "t-stealth-fault"


async def _seed_global_claim(pool, text: str) -> str:
    cid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO knowledge_nodes (id, node_type, name, properties, scope_type, "
        " provenance, created_by, t_valid) "
        "VALUES ($1, 'claim', $2, $3::jsonb, 'global', 'prior_library', 'test', now())",
        cid, f"{_MARK}:{text[:40]}",
        json.dumps({"statement": text, "claim_status": "supported", "belief_score": 0.7}),
    )
    return str(cid)


async def _cleanup_claims(pool):
    await pool.execute("DELETE FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1", f"{_MARK}%")


def test_page_fault_merges_a_global_claim_without_clobbering_the_run_set():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        rid = uuid.uuid4().hex[:8]
        name = f"proc-test-stealthfault-{rid}"
        try:
            await _cleanup_claims(pool)
            proc = await _capture(
                pool, name, steps=[{"order": 0, "goal": "do the work"}],
                preconditions=[{"subject": f"svc:{rid}", "predicate": "lang", "object": "python"}],
            )
            exec_run_id = await _start_run(pool, proc)
            claim_id = await _seed_global_claim(pool, f"parallel test execution cuts wall-clock ({rid})")

            with tempfile.TemporaryDirectory() as ws:
                base = await generate_projection(pool, workspace_root=ws, procedure_run_id=exec_run_id)
                pc_rows_before = parse_idx(base["claims_idx"])
                assert len(pc_rows_before) == 1  # the precondition-derived local claim

                res = await project_knowledge(
                    pool, ws,
                    object_ids=[{"kind": "claim", "id": claim_id},
                                {"kind": "claim", "id": str(uuid.uuid4())}],  # one real, one bogus
                )
                assert {"kind": "claim", "id": claim_id} in res["resolved"]
                assert len(res["not_found"]) == 1
                assert res["journal_seq"] > base["journal_seq"]

                sdir = os.path.join(ws, ".stealth")
                claims_idx = open(os.path.join(sdir, "index", "claims.idx"), encoding="utf-8").read()
                rows = parse_idx(claims_idx)
                ids = {r[0] for r in rows}
                assert claim_id in ids                       # global claim merged in
                assert pc_rows_before[0][0] in ids           # run-scoped local claim preserved

                # the merged block is line-addressable
                target = next(r for r in rows if r[0] == claim_id)
                md_lines = open(os.path.join(sdir, "claims.md"), encoding="utf-8").read().splitlines()
                window = md_lines[int(target[6]) - 1:int(target[7])]
                assert window[0].startswith(f"## CLAIM {claim_id}")
                assert any("parallel test execution" in ln for ln in window)

                # root.idx still within budget; context.md now names the global claim
                from app.stealth.format import ROOT_IDX_MAX_BYTES
                root = open(os.path.join(sdir, "index", "root.idx"), encoding="utf-8").read()
                assert len(root.encode("utf-8")) <= ROOT_IDX_MAX_BYTES

                # a knowledge_fault event is journaled
                events = open(os.path.join(sdir, "events.jsonl"), encoding="utf-8").read()
                assert '"knowledge_fault"' in events

                # sidecar membership recorded, survives a full regeneration
                assert {"kind": "claim", "id": claim_id} in read_faulted(ws)
                regen = await generate_projection(pool, workspace_root=ws, procedure_run_id=exec_run_id)
                assert claim_id in {r[0] for r in parse_idx(regen["claims_idx"])}
                assert "RELEVANT GLOBAL CLAIMS" in regen["context_md"]
                assert claim_id in regen["context_md"]
        finally:
            await _cleanup_claims(pool)
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_page_fault_requires_an_existing_projection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as ws:
                from app.stealth.errors import StealthProjectionError
                with pytest.raises(StealthProjectionError):
                    await project_knowledge(pool, ws, object_ids=[{"kind": "claim", "id": str(uuid.uuid4())}])
        finally:
            await pool.close()

    asyncio.run(_run())
