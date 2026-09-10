"""
MCP hardening B37 STRICT CLOSURE: hierarchical retrieval indexes,
extended to Procedures and Implementations (Claims/knowledge_nodes were
already real -- see test_hierarchy_coarse_routing_e2e.py).

Real per-table differences this file proves are handled correctly:
  - `procedures` has a real embedding column, matching Claims' pattern
    exactly -- coarse routing works via real vector similarity.
  - `implementations` (migration 33) has NO embedding column at all --
    per B37/B38's "never fake a semantic embedding" rule, this table's
    hierarchy is built on real LEXICAL similarity only, and
    `coarse_route` honestly returns `None` for it always (never a
    fabricated vector routing decision) -- proven explicitly, not
    silently assumed.
  - Both tables get real, precise group-row markers distinct from
    knowledge_nodes' `node_type` (which neither table has): `provenance
    = 'company_debate'` for procedures, `requirements->>
    '_hierarchy_group'` for implementations -- each additionally
    defended by that table's own REAL exclusion-from-retrieval
    mechanism (`is_engineering_fixture=true` / `status='disabled'`) so
    a group row can never surface as a fabricated candidate in any
    real retrieval path.

Same safety discipline as test_hierarchy_coarse_routing_e2e.py: never
calls `build_hierarchy_for_table` against the shared, populated corpus
(confirmed live: thousands of real procedures/implementations exist) --
constructs one small, fully-owned tree by hand instead, so both
construction and cleanup stay scoped to this test's own fixtures.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution import implementation_registry
from app.services.embeddings import to_pgvector
from app.services.hierarchy import coarse_route, compute_index_freshness
from app.services.procedures import capture_procedure
from app.utils.ids import uuid7

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

_GROUP_A_VEC = [1.0] * 512 + [0.0] * 512
_GROUP_B_VEC = [0.0] * 512 + [1.0] * 512


class FakeEmbedder:
    def __init__(self, vector):
        self._vector = vector

    async def embed_one(self, text, input_type="document"):
        return self._vector


async def _cleanup_procedures(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM procedures WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM procedures WHERE name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _cleanup_implementations(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM implementations WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM implementations WHERE name LIKE $1)",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM procedure_implementations WHERE implementation_id IN "
                       "(SELECT id FROM implementations WHERE name LIKE $1)", f"{name_prefix}%")
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{name_prefix}%")


async def _capture_procedure(pool, name: str, embedding: list[float]) -> str:
    result = await capture_procedure(
        pool, name=name, goal=name, provenance="prior_library", scope_type="global",
        steps=[{"order": 0, "goal": "step"}], created_by="test", embedding=embedding,
    )
    return str(result["procedure_id"])


async def _build_owned_procedure_group(pool, name: str, embedding: list[float], child_procedure_ids: list[str]) -> str:
    """The exact real shape `hierarchy._create_internal_node` produces
    for `procedures` (mirrored, not imported -- module-internal), scoped
    entirely to this test's own prefixed rows."""
    now = datetime.now(timezone.utc)
    row = await pool.fetchrow(
        "INSERT INTO procedures (name, goal, provenance, embedding, is_engineering_fixture, "
        "t_valid, t_created, created_by) "
        "VALUES ($1, $2, 'company_debate', $3::vector, true, $4, $4, $5) RETURNING id",
        name, f"Aggregates {len(child_procedure_ids)} procedures", to_pgvector(embedding), now, "test",
    )
    group_row_id = row["id"]
    for child_pid in child_procedure_ids:
        child_row_id = await pool.fetchval(
            "SELECT id FROM procedures WHERE procedure_id = $1::uuid AND t_invalid IS NULL", child_pid,
        )
        await pool.execute(
            "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, provenance, t_valid, t_created, created_by) "
            "VALUES ('OWNS', 'PARENT_OF', $1::uuid, 'procedures', $2::uuid, 'procedures', "
            "'company_debate', $3, $3, $4)",
            group_row_id, child_row_id, now, "test",
        )
    return str(group_row_id)


def test_procedures_coarse_route_and_index_freshness_over_a_real_two_branch_hierarchy():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"proc-test-hier37-{uuid4().hex[:8]}"
        try:
            await _cleanup_procedures(pool, prefix)

            a1 = await _capture_procedure(pool, f"{prefix}-alpha-1", _GROUP_A_VEC)
            a2 = await _capture_procedure(pool, f"{prefix}-alpha-2", _GROUP_A_VEC)
            b1 = await _capture_procedure(pool, f"{prefix}-beta-1", _GROUP_B_VEC)
            b2 = await _capture_procedure(pool, f"{prefix}-beta-2", _GROUP_B_VEC)

            fresh_before = await compute_index_freshness(pool, "procedures")
            assert fresh_before["canonical_revision"] >= 4
            unindexed_before = fresh_before["canonical_revision"] - fresh_before["indexed_revision"]
            assert unindexed_before >= 4

            await _build_owned_procedure_group(pool, f"{prefix}-group-alpha", _GROUP_A_VEC, [a1, a2])
            await _build_owned_procedure_group(pool, f"{prefix}-group-beta", _GROUP_B_VEC, [b1, b2])

            fresh_after = await compute_index_freshness(pool, "procedures")
            assert fresh_after["indexed_revision"] - fresh_before["indexed_revision"] == 4
            # The 2 new group rows are index metadata (provenance=
            # 'company_debate'), excluded from canonical_revision by
            # hierarchy.py's own real group-row filter for procedures.
            assert fresh_after["canonical_revision"] == fresh_before["canonical_revision"]

            routed_a = await coarse_route(
                pool, "procedures", "query about alpha", embedder=FakeEmbedder(_GROUP_A_VEC),
            )
            assert routed_a is not None
            a1_row_id = str(await pool.fetchval(
                "SELECT id FROM procedures WHERE procedure_id = $1::uuid", a1,
            ))
            a2_row_id = str(await pool.fetchval(
                "SELECT id FROM procedures WHERE procedure_id = $1::uuid", a2,
            ))
            assert set(routed_a) == {a1_row_id, a2_row_id}

            routed_b = await coarse_route(
                pool, "procedures", "query about beta", embedder=FakeEmbedder(_GROUP_B_VEC),
            )
            assert routed_b is not None
            b1_row_id = str(await pool.fetchval(
                "SELECT id FROM procedures WHERE procedure_id = $1::uuid", b1,
            ))
            b2_row_id = str(await pool.fetchval(
                "SELECT id FROM procedures WHERE procedure_id = $1::uuid", b2,
            ))
            assert set(routed_b) == {b1_row_id, b2_row_id}
        finally:
            await _cleanup_procedures(pool, prefix)
            await pool.close()

    asyncio.run(_run())


def test_implementations_index_freshness_is_real_and_coarse_route_is_honestly_none():
    """`implementations` has no embedding column -- `coarse_route` must
    never fabricate a vector-based routing decision for it (always
    `None`, proven explicitly), while `compute_index_freshness` (a real,
    non-embedding-dependent count) works correctly."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"impl-test-hier37-{uuid4().hex[:8]}"
        try:
            await _cleanup_implementations(pool, prefix)

            impl_a = await implementation_registry.register(
                pool, name=f"{prefix}-impl-a", kind="tool", provider="hier37-e2e", created_by="test",
            )
            impl_b = await implementation_registry.register(
                pool, name=f"{prefix}-impl-b", kind="tool", provider="hier37-e2e", created_by="test",
            )

            fresh_before = await compute_index_freshness(pool, "implementations")
            assert fresh_before["canonical_revision"] >= 2
            unindexed_before = fresh_before["canonical_revision"] - fresh_before["indexed_revision"]
            assert unindexed_before >= 2

            now = datetime.now(timezone.utc)
            group_row = await pool.fetchrow(
                "INSERT INTO implementations (name, description, kind, provider, requirements, "
                "status, t_created, created_by) "
                "VALUES ($1, $2, 'deterministic', 'hierarchy_builder', $3::jsonb, 'disabled', $4, $5) "
                "RETURNING id",
                f"{prefix}-group", "Aggregates 2 implementations",
                {"_hierarchy_group": True, "_member_count": 2}, now, "test",
            )
            group_id = group_row["id"]
            for child_id in (impl_a["id"], impl_b["id"]):
                await pool.execute(
                    "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                    "target_id, target_table, provenance, t_valid, t_created, created_by) "
                    "VALUES ('OWNS', 'PARENT_OF', $1::uuid, 'implementations', $2::uuid, "
                    "'implementations', 'company_debate', $3, $3, $4)",
                    group_id, child_id, now, "test",
                )

            fresh_after = await compute_index_freshness(pool, "implementations")
            assert fresh_after["indexed_revision"] - fresh_before["indexed_revision"] == 2
            # The group row itself (requirements->>'_hierarchy_group')
            # must never inflate canonical_revision.
            assert fresh_after["canonical_revision"] == fresh_before["canonical_revision"]

            # Never a fabricated vector routing decision -- no embedding
            # column exists on this table at all.
            routed = await coarse_route(
                pool, "implementations", "anything",
                embedder=FakeEmbedder([1.0] * 1024),
            )
            assert routed is None
        finally:
            await _cleanup_implementations(pool, prefix)
            await pool.close()

    asyncio.run(_run())
