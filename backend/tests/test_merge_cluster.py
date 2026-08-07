"""
Tests dedup.py's merge_cluster -- the actual DB-writing half of Part A,
which (as flagged during synthetic Experiment 3 testing) had never been
exercised even against a fake DB before. test_dedup.py only covers
dedupe_changeset_ops, the in-memory pre-persistence half.

This matters specifically for Experiment 3 (Debate + Update): its core
claim is "once superseded, the old node stops being served and the new
one is." That's a real DB-write-and-then-read-again behavior, not
something the dedupe_changeset_ops tests (or a code-inspection check of
`t_invalid IS NULL` filters, which only proves reads are FILTERED
correctly, not that writes correctly SET the filtered flag in the first
place) can confirm on their own.

Uses a real, timestamp-aware in-memory store (not just pattern-matched
canned responses) so t_invalid state actually changes and gets read
back correctly across the merge -- this is exercising real invalidation
semantics, not just query-shape recognition.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import numpy as np

from app.services.dedup import merge_cluster


def unit(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


class FakeConn:
    def __init__(self, store):
        self.store = store  # shared mutable dict: {"nodes": {table: {id: row}}, "edges": [row, ...]}

    async def fetch(self, query, *params):
        if "FOR UPDATE" in query:  # merge_cluster's initial row lock+read
            ids = {str(i) for i in params[0]}
            table = query.split("FROM ")[1].split(" ")[0]
            return [
                dict(row) for nid, row in self.store["nodes"][table].items()
                if nid in ids and row["t_invalid"] is None
            ]
        if "FROM edges" in query and "source_id = $1" in query:  # edges touching a duplicate
            dup_id, table = str(params[0]), params[1]
            return [
                dict(e) for e in self.store["edges"]
                if e["t_invalid"] is None
                and ((str(e["source_id"]) == dup_id and e["source_table"] == table)
                     or (str(e["target_id"]) == dup_id and e["target_table"] == table))
            ]
        raise AssertionError(f"FakeConn.fetch: unrecognized query shape\n{query}")

    async def fetchrow(self, query, *params):
        rows = await self.fetch(query, *params)
        return rows[0] if rows else None

    async def execute(self, query, *params):
        if query.strip().startswith("UPDATE edges SET t_invalid"):
            edge_id, now = str(params[0]), params[1]
            for e in self.store["edges"]:
                if str(e["id"]) == edge_id:
                    e["t_invalid"] = now
                    e["t_expired"] = now
            return
        if query.strip().startswith("INSERT INTO edges") and "'SUPERSEDES', 'DUPLICATE_OF'" in query:
            # merge_cluster's canonical-vs-duplicate marker edge: fixed
            # literal edge_type/custom_edge_type, table reused for both
            # source_table/target_table (source and target are always
            # the same table in a merge).
            canonical_id, table, dup_id, properties, t_valid, created_by = params
            self.store["edges"].append({
                "id": uuid4(), "edge_type": "SUPERSEDES", "custom_edge_type": "DUPLICATE_OF",
                "source_id": canonical_id, "source_table": table,
                "target_id": dup_id, "target_table": table,
                "properties": properties, "t_valid": t_valid, "t_created": t_valid,
                "t_invalid": None, "t_expired": None, "created_by": created_by,
            })
            return
        if query.strip().startswith("INSERT INTO edges"):
            (edge_type, custom_edge_type, source_id, source_table,
             target_id, target_table, properties, t_valid, created_by) = params
            self.store["edges"].append({
                "id": uuid4(), "edge_type": edge_type, "custom_edge_type": custom_edge_type,
                "source_id": source_id, "source_table": source_table,
                "target_id": target_id, "target_table": target_table,
                "properties": properties, "t_valid": t_valid, "t_created": t_valid,
                "t_invalid": None, "t_expired": None, "created_by": created_by,
            })
            return
        if query.strip().startswith("UPDATE") and "SET t_invalid" in query:
            table = query.split("UPDATE ")[1].split(" ")[0]
            node_id, now = str(params[0]), params[1]
            self.store["nodes"][table][node_id]["t_invalid"] = now
            self.store["nodes"][table][node_id]["t_expired"] = now
            return
        raise AssertionError(f"FakeConn.execute: unrecognized query shape\n{query}")

    def transaction(self):
        @asynccontextmanager
        async def _txn():
            yield
        return _txn()


class FakePool:
    def __init__(self):
        self.store = {"nodes": {"task_nodes": {}, "knowledge_nodes": {}}, "edges": []}

    def add_node(self, table, name, t_created=None) -> str:
        nid = str(uuid4())
        self.store["nodes"][table][nid] = {
            "id": UUID(nid), "name": name,
            "t_created": t_created or datetime.now(timezone.utc), "t_invalid": None,
        }
        return nid

    def add_edge(self, source_id, source_table, target_id, target_table, edge_type="REQUIRES") -> str:
        eid = str(uuid4())
        self.store["edges"].append({
            "id": UUID(eid), "edge_type": edge_type, "custom_edge_type": None,
            "source_id": UUID(source_id), "source_table": source_table,
            "target_id": UUID(target_id), "target_table": target_table,
            "properties": {}, "t_valid": datetime.now(timezone.utc), "t_created": datetime.now(timezone.utc),
            "t_invalid": None, "t_expired": None, "created_by": "test_setup",
        })
        return eid

    def acquire(self):
        @asynccontextmanager
        async def _acquire():
            yield FakeConn(self.store)
        return _acquire()

    def live_nodes(self, table):
        return {nid: r for nid, r in self.store["nodes"][table].items() if r["t_invalid"] is None}

    def live_edges(self):
        return [e for e in self.store["edges"] if e["t_invalid"] is None]


def test_merge_cluster_invalidates_duplicates_and_keeps_canonical():
    pool = FakePool()
    import time
    t0 = datetime.now(timezone.utc)
    canonical_id = pool.add_node("task_nodes", "Validate schema conformance", t_created=t0)
    time.sleep(0.001)
    dup_id = pool.add_node("task_nodes", "Validate the schema conformance")  # later t_created

    async def run():
        async with pool.acquire() as conn:
            async with conn.transaction():
                report = await merge_cluster(conn, "task_nodes", [canonical_id, dup_id], "tester", datetime.now(timezone.utc))
        return report

    report = asyncio.run(run())

    assert report is not None
    assert report.canonical_id == canonical_id  # earliest t_created wins
    assert report.merged_ids == [dup_id]

    live = pool.live_nodes("task_nodes")
    assert canonical_id in live, "canonical must remain live"
    assert dup_id not in live, "duplicate must be invalidated (t_invalid set), not deleted"
    # not deleted -- still present in the store, just filtered by t_invalid
    assert pool.store["nodes"]["task_nodes"][dup_id]["t_invalid"] is not None


def test_edges_pointing_at_duplicate_get_rewired_to_canonical_not_lost():
    pool = FakePool()
    canonical_id = pool.add_node("task_nodes", "Extract fields")
    time_module = __import__("time")
    time_module.sleep(0.001)
    dup_id = pool.add_node("task_nodes", "Extract the fields")
    downstream_id = pool.add_node("task_nodes", "Send to review")
    pool.add_edge(dup_id, "task_nodes", downstream_id, "task_nodes", edge_type="PRODUCES")

    async def run():
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await merge_cluster(conn, "task_nodes", [canonical_id, dup_id], "tester", datetime.now(timezone.utc))

    asyncio.run(run())

    live_edges = pool.live_edges()
    rewired = [e for e in live_edges if str(e["source_id"]) == canonical_id and str(e["target_id"]) == downstream_id]
    assert len(rewired) == 1, "the PRODUCES edge must now originate from the canonical node, not be dropped"
    stale = [e for e in live_edges if str(e["source_id"]) == dup_id]
    assert stale == [], "the old edge from the now-invalid duplicate must not still be live"


def test_post_merge_query_only_sees_canonical():
    """
    The actual Experiment-3-relevant claim: after a merge/supersession,
    does a fresh read see only the surviving node? This is what
    t_invalid IS NULL filtering (already verified by code inspection
    elsewhere) is FOR -- this test confirms the WRITE side actually
    produces the state the read side depends on, closing the loop.

    Uses canonical_rule="latest" -- the fix added after this exact test
    first exposed that the default "earliest" rule keeps the STALE
    policy live for a supersession scenario, which is backwards.
    """
    pool = FakePool()
    old_id = pool.add_node("task_nodes", "Refunds require a receipt")
    import time
    time.sleep(0.001)
    new_id = pool.add_node("task_nodes", "Refunds under $50 do not require a receipt")

    async def run():
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await merge_cluster(
                    conn, "task_nodes", [old_id, new_id], "tester", datetime.now(timezone.utc),
                    canonical_rule="latest",
                )

    asyncio.run(run())
    live = pool.live_nodes("task_nodes")
    assert new_id in live and old_id not in live, (
        "with canonical_rule='latest', the NEW policy must survive and the OLD one must be "
        "invalidated -- this is the actual behavior Experiment 3 depends on"
    )


def test_default_earliest_rule_is_backwards_for_supersession():
    """
    Documents the gap explicitly, rather than leaving it implicit: the
    DEFAULT rule ("earliest", correct for Part A's accidental-duplicate
    case) is the WRONG choice for a supersession scenario -- it keeps
    the old node. Any caller doing supersession (Experiment 3, or a
    future debate-triggered update path) MUST pass canonical_rule="latest"
    explicitly; the default will silently do the wrong thing otherwise.
    """
    pool = FakePool()
    old_id = pool.add_node("task_nodes", "Refunds require a receipt")
    import time
    time.sleep(0.001)
    new_id = pool.add_node("task_nodes", "Refunds under $50 do not require a receipt")

    async def run():
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await merge_cluster(conn, "task_nodes", [old_id, new_id], "tester", datetime.now(timezone.utc))

    asyncio.run(run())
    live = pool.live_nodes("task_nodes")
    assert old_id in live and new_id not in live, (
        "confirms the default is earliest-wins, which a supersession caller must override"
    )
