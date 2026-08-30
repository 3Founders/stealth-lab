"""
Real, live-database tests for Phase 5 (memory-substrate map, gap #8):
procedure-level canonicalization/dedup. Same pattern as the other
e2e test files this session: requires a real DATABASE_URL, skips
(not fails) without one.

Covers two things:
  1. dedup.py::find_duplicate_clusters now accepts "procedures" as a
     real table option (embedding-similarity / lexical-fallback
     clustering, same primitive task_nodes/knowledge_nodes already use).
  2. procedures.py::merge_duplicate_procedures -- the real merge path
     check_novelty() (skill_ingestion.py) cannot provide, because it
     only ever sees one not-yet-written candidate at a time: survivor
     selection off the ticket-13 verification ladder, the loser
     tombstoned (t_invalid set, never deleted), and the loser's
     provenance/evidence_refs/source_episode_ids preserved onto the
     survivor, with a real ChangeSet audit row.
"""
import asyncio
import os
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.dedup import find_duplicate_clusters
from app.services.procedures import (
    capture_procedure,
    merge_duplicate_procedures,
    record_execution_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    # Same test-only convenience hard DELETE the rest of this suite uses
    # (test_procedures_e2e.py::_cleanup) -- bypasses the real
    # invalidate-and-append convention deliberately, for isolated test
    # fixtures only, never a production code path.
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_find_duplicate_clusters_covers_procedures_table():
    """dedup.py's real clustering primitive, extended to "procedures":
    two rows with near-identical name+goal text (no embedding, so this
    exercises the lexical-overlap fallback -- same fallback
    reuse_detection.py already uses, no Voyage key required) must land
    in the same complete-linkage cluster."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-dedup-cluster-test")
            a = await capture_procedure(
                pool, name="proc-dedup-cluster-test zqxwidget837 fixture",
                goal="zqxwidget837 dedup fixture procedure for handling widget retries in staging",
                provenance="prior_library", scope_type="global",
            )
            b = await capture_procedure(
                pool, name="proc-dedup-cluster-test zqxwidget837 fixture two",
                goal="zqxwidget837 dedup fixture procedure for handling widget retries in staging again",
                provenance="system_pending_review", scope_type="global",
            )

            clusters = await find_duplicate_clusters(pool, "procedures", AccessScope.unrestricted())
            ids_in_some_cluster = [
                {m["id"] for m in cluster} for cluster in clusters
                if {a["id"], b["id"]} <= {m["id"] for m in cluster}
            ]
            assert ids_in_some_cluster, (
                f"expected {a['id']} and {b['id']} to land in the same complete-linkage "
                f"cluster; got clusters={clusters}"
            )
        finally:
            await _cleanup(pool, "proc-dedup-cluster-test")
            await pool.close()

    asyncio.run(_run())


def test_merge_duplicate_procedures_survivor_tombstone_and_provenance():
    """The real proving test: the stronger (more recorded successes)
    procedure survives, the weaker one is tombstoned (t_invalid set,
    NOT deleted -- still queryable), the survivor's evidence_refs/
    source_episode_ids reflect BOTH sources' provenance, and a real
    ChangeSet audit row exists."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-dedup-merge-test")

            weak_episode_id = str(uuid4())
            strong_episode_id = str(uuid4())

            weak = await capture_procedure(
                pool, name="proc-dedup-merge-test-weak", goal="g",
                provenance="prior_library", scope_type="global",
                evidence_refs=[{"source": "skill_md_seed"}],
                source_episode_ids=[weak_episode_id],
            )
            strong = await capture_procedure(
                pool, name="proc-dedup-merge-test-strong", goal="g",
                provenance="system_pending_review", scope_type="global",
                evidence_refs=[{"source": "organic_capture"}],
                source_episode_ids=[strong_episode_id],
            )

            # Give the "strong" candidate real recorded successes the
            # "weak" one never gets -- both stay 'candidate' (well below
            # MIN_SUCCESSES_FOR_VERIFIED), so the survivor rule falls
            # through to the successes tie-break, not the state rank.
            for i in range(3):
                await record_execution_outcome(
                    pool, procedure_row_id=strong["id"], success=True, context_key=f"ctx-{i}",
                )

            report = await merge_duplicate_procedures(
                pool, cluster_ids=[weak["id"], strong["id"]], merged_by="tester_dedup",
            )
            assert report is not None
            assert report["table"] == "procedures"
            assert report["canonical_id"] == strong["id"], (
                "the procedure with more recorded successes must survive"
            )
            assert report["merged_ids"] == [weak["id"]]

            survivor = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", strong["id"])
            assert survivor["t_invalid"] is None, "the survivor must not be tombstoned"
            assert survivor["verification_stats"]["successes"] == 3

            loser = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", weak["id"])
            assert loser is not None, "the loser must still be queryable -- never hard-deleted"
            assert loser["t_invalid"] is not None, "the loser must be tombstoned via t_invalid"

            # Both sources' provenance must be honestly reflected on the
            # survivor: its own original evidence_refs entry, the loser's
            # original evidence_refs entry (unioned), and a merge audit
            # entry naming the loser's own provenance string (which
            # cannot be losslessly written into the survivor's single
            # provenance TEXT column, so it must not be silently dropped).
            refs = survivor["evidence_refs"]
            assert {"source": "organic_capture"} in refs
            assert {"source": "skill_md_seed"} in refs
            merge_entries = [r for r in refs if isinstance(r, dict) and "merged_from_procedure_row_id" in r]
            assert len(merge_entries) == 1
            assert merge_entries[0]["merged_from_procedure_row_id"] == weak["id"]
            assert merge_entries[0]["merged_from_provenance"] == "prior_library"

            survivor_episode_ids = {str(x) for x in survivor["source_episode_ids"]}
            assert weak_episode_id in survivor_episode_ids
            assert strong_episode_id in survivor_episode_ids

            # Survivor's OWN provenance column is untouched by the merge
            # (single TEXT field -- it stays the survivor's real origin,
            # never silently overwritten by the loser's).
            assert survivor["provenance"] == "system_pending_review"

            # A DUPLICATE_OF edge records the merge for graph-side
            # discoverability (procedures.merge_duplicate_procedures'
            # own docstring), same convention dedup.py::merge_cluster
            # uses for task_nodes/knowledge_nodes.
            edge = await pool.fetchrow(
                "SELECT * FROM edges WHERE source_id = $1 AND target_id = $2 "
                "AND source_table = 'procedures' AND custom_edge_type = 'DUPLICATE_OF'",
                strong["id"], weak["id"],
            )
            assert edge is not None

            # Real ChangeSet audit row.
            change_set_id = report["change_set_id"]
            assert change_set_id
            cs = await pool.fetchrow("SELECT * FROM change_sets WHERE id = $1::uuid", change_set_id)
            assert cs is not None
            assert "procedure dedup merge" in cs["reason"]

            ops = await pool.fetch(
                "SELECT operation, target_table, target_id FROM change_set_operations "
                "WHERE change_set_id = $1::uuid ORDER BY operation",
                change_set_id,
            )
            ops_by_target = {(o["operation"], o["target_id"]) for o in ops}
            assert ("invalidate", weak["id"]) in ops_by_target
            assert ("revise", strong["id"]) in ops_by_target
        finally:
            await _cleanup(pool, "proc-dedup-merge-test")
            await pool.close()

    asyncio.run(_run())


def test_merge_duplicate_procedures_is_a_noop_when_cluster_already_reconciled():
    """Same contract as dedup.py::merge_cluster: fewer than 2 members
    still live (t_invalid IS NULL) is a valid no-op (None), not an
    error -- a concurrent sweep or edit already reconciled this
    cluster."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-dedup-noop-test")
            only = await capture_procedure(
                pool, name="proc-dedup-noop-test-only", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            report = await merge_duplicate_procedures(
                pool, cluster_ids=[only["id"], str(uuid4())], merged_by="tester_dedup",
            )
            assert report is None
        finally:
            await _cleanup(pool, "proc-dedup-noop-test")
            await pool.close()

    asyncio.run(_run())
