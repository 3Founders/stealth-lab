"""Live-database proof of `license_takedown` (needs migration 126). Skipped unless DATABASE_URL is set AND local.

Each run uses its own unique license id, so it never matches (and can never remove) real CC-BY-4.0 content, and the
rows it writes cannot collide with an earlier run's.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
_LOCAL = bool(DATABASE_URL) and (urlparse(DATABASE_URL).hostname in ("127.0.0.1", "localhost", "::1"))

pytestmark = pytest.mark.skipif(not _LOCAL, reason="needs a LOCAL DATABASE_URL with migration 126 (this test writes)")

VECTOR = "[" + ",".join(["0.01"] * 1024) + "]"


def test_takedown_tombstones_exactly_the_tagged_content_and_is_repeatable():
    from app.db.session import create_pool
    from app.services import license_takedown as lt
    from app.services.ingestion_context import open_ingestion_context

    spdx = f"LicenseRef-takedown-test-{uuid.uuid4().hex[:8]}"

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            base = dict(source_type="skill_md", extractor_id="e2e", extractor_version="v1", actor_id="e2e",
                        scope_type="global")
            tagged = await open_ingestion_context(pool, license_spdx=spdx, attribution={"license": spdx, "notice": "n"}, **base)
            control = await open_ingestion_context(pool, **base)

            async def seed(ctx: str, label: str) -> dict:
                proc = await pool.fetchrow(
                    "INSERT INTO procedures (name, goal, embedding, ingestion_context_id) "
                    "VALUES ($1, $2, $3::vector, $4::uuid) RETURNING id::text, procedure_id::text",
                    f"e2e-proc-{label}", "e2e goal", VECTOR, ctx)
                claim = await pool.fetchrow(
                    "INSERT INTO knowledge_nodes (node_type, name, embedding, ingestion_context_id) "
                    "VALUES ('claim', $1, $2::vector, $3::uuid) RETURNING id::text", f"e2e-claim-{label}", VECTOR, ctx)
                ev = await pool.fetchrow(
                    "INSERT INTO evidence (evidence_type, target_type, target_id, direction, strength_score, "
                    "strength_method, ingestion_context_id) VALUES ('document', 'claim', $1::uuid, 'supports', 0.5, "
                    "'e2e', $2::uuid) RETURNING id::text", claim["id"], ctx)
                art = await pool.fetchrow(
                    "INSERT INTO ingested_artifacts (source_type, uri, content_hash, ingestion_context_id) "
                    "VALUES ('skill_md', $1, $2, $3::uuid) RETURNING id::text", f"e2e://{label}/{uuid.uuid4()}",
                    uuid.uuid4().hex, ctx)
                return {"proc": proc["id"], "proc_logical": proc["procedure_id"], "claim": claim["id"],
                        "evidence": ev["id"], "artifact": art["id"]}

            hit, keep = await seed(tagged, "tagged"), await seed(control, "control")

            plan = await lt.plan(pool, spdx)
            assert plan["applied"] is False and plan["contexts"] == 1
            assert plan["totals"] == {"procedures": 1, "claims": 1, "evidence": 1, "artifacts": 1}
            # plan wrote nothing
            assert await pool.fetchval("SELECT t_invalid FROM procedures WHERE id = $1::uuid", hit["proc"]) is None

            out = await lt.apply(pool, spdx, actor="e2e")
            assert out["applied"] is True and out["totals"] == plan["totals"]
            assert out["projection_refreshes_queued"] == 2                       # the Procedure and the Claim

            proc = await pool.fetchrow(
                "SELECT availability::text AS a, embedding IS NULL AS no_vec, t_invalid IS NOT NULL AS gone "
                "FROM procedures WHERE id = $1::uuid", hit["proc"])
            assert (proc["a"], proc["no_vec"], proc["gone"]) == ("disabled", True, True)
            claim = await pool.fetchrow("SELECT embedding IS NULL AS no_vec, t_invalid IS NOT NULL AS gone "
                                        "FROM knowledge_nodes WHERE id = $1::uuid", hit["claim"])
            assert (claim["no_vec"], claim["gone"]) == (True, True)
            assert await pool.fetchval("SELECT t_invalid IS NOT NULL FROM evidence WHERE id = $1::uuid", hit["evidence"])
            assert await pool.fetchval(
                "SELECT (license_metadata ->> 'license_takedown') = 'true' FROM ingested_artifacts WHERE id = $1::uuid",
                hit["artifact"])

            # the untagged control set is untouched
            ctl = await pool.fetchrow(
                "SELECT availability::text AS a, embedding IS NOT NULL AS has_vec, t_invalid IS NULL AS live "
                "FROM procedures WHERE id = $1::uuid", keep["proc"])
            assert (ctl["a"], ctl["has_vec"], ctl["live"]) == ("active", True, True)
            assert await pool.fetchval("SELECT t_invalid IS NULL FROM knowledge_nodes WHERE id = $1::uuid", keep["claim"])
            assert await pool.fetchval("SELECT t_invalid IS NULL FROM evidence WHERE id = $1::uuid", keep["evidence"])

            # the search-index refresh is queued, and the action is audited
            queued = await pool.fetch(
                "SELECT object_type, object_id::text FROM projection_outbox WHERE status = 'pending' "
                "AND object_id = ANY($1::uuid[])", [hit["proc_logical"], hit["claim"]])
            assert {(r["object_type"], r["object_id"]) for r in queued} == {
                ("procedure", hit["proc_logical"]), ("claim", hit["claim"])}
            assert await pool.fetchval(
                "SELECT count(*) FROM audit_events WHERE action = 'license_takedown' AND object_id = $1", spdx) == 1

            # repeatable: a second run finds nothing left to remove
            again = await lt.apply(pool, spdx, actor="e2e")
            assert again["totals"]["procedures"] == 0 and again["totals"]["claims"] == 0 and again["totals"]["evidence"] == 0
        finally:
            await pool.close()

    asyncio.run(_run())
