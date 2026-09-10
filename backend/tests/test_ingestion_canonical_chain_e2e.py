"""
T3 -- the golden ingestion E2E for the DOCUMENT path (V4-hardening Part VI
"Definition of done"): a real SKILL.md, driven through the real compiler
against a real database, must produce the whole canonical chain --

    Source -> IngestionContext -> Artifact -> artifact_blocks
           -> Observation -> Evidence(document) -> Claim -> procedure_claim_ref
           -> screening_decision -> Procedure (candidate)

with every derived row carrying `ingestion_context_id`, and NOTHING owed to
`task_nodes` (B2 / rule 8). A non-procedural document must produce NO
Procedure.

Same skip/self-cleaning convention as every other `*_e2e.py`: needs a real
DATABASE_URL, skips (never fails) without one. Run it against the local
Postgres with `python scripts/dbtarget.py local -- python -m pytest
tests/test_ingestion_canonical_chain_e2e.py -q`.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.ingestion_sources.skill_md import LocalDirSkillSource
from app.services.skill_ingestion import run_skill_ingestion

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

_FIX = Path(__file__).parent / "fixtures" / "skills"
PROC_FIXTURE = _FIX / "canonical-chain"
REF_FIXTURE = _FIX / "reference-only"

PROC_NAME = "canonical-chain-e2e-procedure"
REF_NAME = "canonical-chain-e2e-reference"
_URI_LIKE = "%canonical-chain%"
_REF_URI_LIKE = "%reference-only%"


async def _cleanup(pool) -> None:
    # Delete leaf-first. procedure_claim_refs / evidence / artifact_blocks /
    # claim_sources have no FK cascade from procedures here, so name them.
    proc_ids = [
        r["id"]
        for r in await pool.fetch(
            "SELECT id FROM procedures WHERE name IN ($1, $2)", PROC_NAME, REF_NAME
        )
    ]
    for pid in proc_ids:
        await pool.execute("DELETE FROM procedure_claim_refs WHERE procedure_id = $1", pid)
        # `evidence` is append-only (mig 24 trigger): the ONE legal mutation
        # is the t_invalid tombstone. Test isolation is by fresh target_id
        # per run anyway, so tombstoning keeps the table clean without
        # tripping the trigger.
        await pool.execute(
            "UPDATE evidence SET t_invalid = now() "
            "WHERE target_type = 'procedure' AND target_id = $1 AND t_invalid IS NULL", pid
        )
        await pool.execute(
            "DELETE FROM edges WHERE source_id = $1 AND source_table = 'procedures'", pid
        )
    await pool.execute("DELETE FROM procedures WHERE name IN ($1, $2)", PROC_NAME, REF_NAME)
    ctx_ids = [
        r["id"]
        for r in await pool.fetch(
            "SELECT id FROM ingestion_contexts WHERE source_uri LIKE $1 OR source_uri LIKE $2",
            _URI_LIKE, _REF_URI_LIKE,
        )
    ]
    for cid in ctx_ids:
        await pool.execute("DELETE FROM artifact_blocks WHERE ingestion_context_id = $1", cid)
        await pool.execute("DELETE FROM screening_decisions WHERE ingestion_context_id = $1", cid)
        await pool.execute(
            "DELETE FROM claim_sources cs USING knowledge_nodes k "
            "WHERE cs.claim_id = k.id AND k.ingestion_context_id = $1", cid
        )
        await pool.execute("DELETE FROM knowledge_nodes WHERE ingestion_context_id = $1", cid)
        await pool.execute("DELETE FROM observations WHERE ingestion_context_id = $1", cid)
        await pool.execute(
            "UPDATE evidence SET t_invalid = now() "
            "WHERE ingestion_context_id = $1 AND t_invalid IS NULL", cid
        )
        await pool.execute("DELETE FROM ingested_artifacts WHERE ingestion_context_id = $1", cid)
    await pool.execute(
        "DELETE FROM ingestion_contexts WHERE source_uri LIKE $1 OR source_uri LIKE $2",
        _URI_LIKE, _REF_URI_LIKE,
    )
    await pool.execute(
        "DELETE FROM ingested_artifacts WHERE uri LIKE $1 OR uri LIKE $2",
        _URI_LIKE, _REF_URI_LIKE,
    )
    await pool.execute(
        "DELETE FROM sources WHERE locator LIKE $1 OR locator LIKE $2",
        _URI_LIKE, _REF_URI_LIKE,
    )


def test_procedural_skill_md_produces_the_full_canonical_chain():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            await _cleanup(pool)

            adapter = LocalDirSkillSource(str(PROC_FIXTURE))
            result = await run_skill_ingestion(
                pool, adapter, embedder=Embedder(),
                created_by="test_ingestion_canonical_chain_e2e",
            )
            m = result["metrics"]
            assert m["errors"] == 0, m
            assert m["accepted"] == 1, m

            proc = await pool.fetchrow(
                "SELECT id, procedure_id, version, verification_state, provenance, "
                "ingestion_context_id "
                "FROM procedures WHERE name = $1 AND t_invalid IS NULL", PROC_NAME
            )
            assert proc is not None, "no procedure row"
            assert proc["verification_state"] == "candidate", "nothing is born verified"
            assert proc["ingestion_context_id"] is not None, "procedure not stamped with a context"
            ctx_id = proc["ingestion_context_id"]
            proc_row_id = proc["id"]

            # 1. IngestionContext -- completed, correct shape.
            ctx = await pool.fetchrow(
                "SELECT source_type, status, extractor_id, extractor_version, source_ref, "
                "source_uri, source_hash "
                "FROM ingestion_contexts WHERE id = $1", ctx_id
            )
            assert ctx is not None
            assert ctx["source_type"] == "skill_md"
            assert ctx["status"] == "completed"
            assert ctx["extractor_id"] and ctx["extractor_version"]
            assert ctx["source_ref"] is not None, "context not linked to a Source"
            src_id = ctx["source_ref"]

            # 2. Source -- document origin, identity fields.
            src = await pool.fetchrow(
                "SELECT source_type::text, locator, publisher, title "
                "FROM sources WHERE id = $1", src_id
            )
            assert src is not None
            assert src["source_type"] == "document"
            assert src["locator"], "Source has no locator"

            # 3. Artifact -- carries both back-links.
            art = await pool.fetchrow(
                "SELECT id, source_ref, ingestion_context_id, content_hash "
                "FROM ingested_artifacts WHERE procedure_row_id = $1", proc_row_id
            )
            assert art is not None, "no ingested_artifacts row for the procedure"
            assert art["source_ref"] == src_id
            assert art["ingestion_context_id"] == ctx_id

            # 4. artifact_blocks -- immutable, offset-preserving, context-stamped.
            blocks = await pool.fetch(
                "SELECT block_type, text, source_start, source_end, artifact_content_hash, "
                "ingestion_context_id "
                "FROM artifact_blocks WHERE artifact_id = $1 ORDER BY block_index", art["id"]
            )
            assert len(blocks) >= 5, f"expected several blocks, got {len(blocks)}"
            raw = (PROC_FIXTURE / "SKILL.md").read_text(encoding="utf-8")
            for b in blocks:
                assert b["ingestion_context_id"] == ctx_id
                assert 0 <= b["source_start"] < b["source_end"] <= len(raw)
                assert raw[b["source_start"]:b["source_end"]] == b["text"] or b["block_type"] == "heading", (
                    f"block offsets do not round-trip: {b['block_type']} "
                    f"[{b['source_start']}:{b['source_end']}]"
                )
            assert any(b["block_type"] == "heading" for b in blocks)

            # 5. Observation -- document_procedure, context-stamped.
            obs = await pool.fetch(
                "SELECT id, observation_type, ingestion_context_id, properties "
                "FROM observations WHERE ingestion_context_id = $1", ctx_id
            )
            assert obs, "no Observation for this ingestion"
            doc_obs = [o for o in obs if o["observation_type"] == "document_procedure"]
            assert doc_obs, "no document_procedure Observation"
            obs_id = doc_obs[0]["id"]

            # 6. Evidence(document) -- supports the procedure, independence group scoped.
            ev = await pool.fetchrow(
                "SELECT evidence_type::text, target_type, direction, independence_group, "
                "ingestion_context_id "
                "FROM evidence WHERE target_type = 'procedure' AND target_id = $1 "
                "AND evidence_type = 'document'", proc_row_id
            )
            assert ev is not None, "no document Evidence row for the procedure"
            assert ev["direction"] == "supports"
            assert (ev["independence_group"] or "").startswith("skill_md:")
            assert ev["ingestion_context_id"] == ctx_id

            # 7. Claim -- context-stamped, linked to the Observation.
            claim = await pool.fetchrow(
                "SELECT id, name, ingestion_context_id "
                "FROM knowledge_nodes WHERE node_type = 'claim' AND ingestion_context_id = $1",
                ctx_id
            )
            assert claim is not None, "no document Claim"
            assert claim["ingestion_context_id"] == ctx_id
            link = await pool.fetchval(
                "SELECT 1 FROM claim_sources WHERE claim_id = $1 AND observation_id = $2",
                claim["id"], obs_id
            )
            assert link == 1, "Claim not linked to its Observation via claim_sources"

            # 8. procedure_claim_refs -- explanatory RATIONALE role.
            ref = await pool.fetchrow(
                "SELECT role, ref_origin, procedure_version "
                "FROM procedure_claim_refs "
                "WHERE procedure_id = $1 AND claim_id = $2 AND t_invalid IS NULL",
                proc["procedure_id"], claim["id"]
            )
            assert ref is not None, "no procedure_claim_ref linking the doc Claim to the procedure"
            assert ref["role"] == "RATIONALE"
            assert ref["ref_origin"] == "derived"

            # 9. screening_decisions -- one auditable record with detector provenance.
            scr = await pool.fetch(
                "SELECT decision, detector, detector_version "
                "FROM screening_decisions WHERE ingestion_context_id = $1", ctx_id
            )
            assert scr, "no screening_decisions row for this ingestion"
            assert all(s["decision"] in ("ALLOW", "QUARANTINE", "REJECT") for s in scr)
            assert all(s["detector"] and s["detector_version"] for s in scr)

            # 10. B2 -- NO task_nodes materialised at ingestion.
            tn_edges = await pool.fetch(
                "SELECT 1 FROM edges WHERE source_id = $1 AND source_table = 'procedures' "
                "AND target_table = 'task_nodes'", proc_row_id
            )
            assert tn_edges == [], f"B2: ingestion manufactured {len(tn_edges)} task_node edges"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_non_procedural_document_produces_no_procedure():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            adapter = LocalDirSkillSource(str(REF_FIXTURE))
            result = await run_skill_ingestion(
                pool, adapter, embedder=Embedder(),
                created_by="test_ingestion_canonical_chain_e2e",
            )
            m = result["metrics"]
            assert m["errors"] == 0, m
            assert m["accepted"] == 0, "a non-procedural document must not be accepted as a procedure"
            assert m["rejected"] == 1, m

            proc = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE name = $1", REF_NAME
            )
            assert proc == 0, "a non-procedural document must produce NO procedure row"

            # No typed claim-refs, no procedure-targeted evidence for a rejected doc.
            refs = await pool.fetchval(
                "SELECT count(*) FROM procedure_claim_refs r "
                "JOIN procedures p ON p.procedure_id = r.procedure_id "
                "WHERE p.name = $1", REF_NAME
            )
            assert refs == 0
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
