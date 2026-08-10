"""
Tests for method_library.py's find_reusable_plan/persist_plan, and the Rule
1 gate (touch_tags/query_postconditions) added on top of them.

FakeMethodPool answers the two query shapes method_library.py actually
issues (a top-1 vector-similarity fetchrow, an unfiltered lexical fetch)
using real cosine similarity in Python -- same style test_subtask_reuse.py
already uses for hierarchy.py's queries, simplified since method_library.py
has no beam search or batching to simulate.
"""
from __future__ import annotations

import asyncio
import os
import sys

import numpy as np
import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from method_library import find_reusable_plan, persist_plan  # noqa: E402


def unit(v):
    v = np.array(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n else v


class FakeMethodPool:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.inserted: list[tuple] = []

    def add_row(self, row_id, name, embedding, description="", io_schema=None,
               success_criteria=None):
        self.rows[row_id] = {
            "id": row_id, "name": name, "embedding": unit(embedding),
            "description": description, "io_schema": io_schema or {},
            "success_criteria": success_criteria or {},
        }

    async def fetchrow(self, query, *params):
        if "1 - (embedding" in query:
            vec_str = params[0]
            qvec = unit([float(x) for x in vec_str.strip("[]").split(",")])
            best_id, best_sim = None, -1.0
            for rid, r in self.rows.items():
                sim = float(np.dot(qvec, r["embedding"]))
                if sim > best_sim:
                    best_id, best_sim = rid, sim
            if best_id is None:
                return None
            r = self.rows[best_id]
            return {"id": r["id"], "name": r["name"], "io_schema": r["io_schema"],
                    "success_criteria": r["success_criteria"], "similarity": best_sim}
        raise AssertionError(f"FakeMethodPool.fetchrow got an unrecognized query:\n{query}")

    async def fetch(self, query, *params):
        if "description FROM task_nodes" in query:
            return [
                {"id": r["id"], "name": r["name"], "io_schema": r["io_schema"],
                 "success_criteria": r["success_criteria"], "description": r["description"]}
                for r in self.rows.values()
            ]
        raise AssertionError(f"FakeMethodPool.fetch got an unrecognized query:\n{query}")

    async def execute(self, query, *params):
        if query.strip().startswith("INSERT"):
            self.inserted.append(params)
        elif "success_criteria = success_criteria ||" in query:
            row_id = params[0]
            if row_id in self.rows:
                sc = self.rows[row_id]["success_criteria"]
                sc["times_reused"] = sc.get("times_reused", 0) + 1


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed_one(self, text, input_type="document"):
        return self._vectors[text]


class TestFindReusablePlan:
    def test_confident_vector_match_is_returned(self):
        pool = FakeMethodPool()
        direction = unit(np.ones(8))
        pool.add_row("m1", "fix the auth bug", direction, io_schema={
            "kind": "htn_method",
            "decomposition": [{"id": 1, "goal": "patch middleware", "deps": []}]})
        embedder = FakeEmbedder(
            {"fix the auth bug again": unit(direction + 0.001 * np.ones(8))})

        match = asyncio.run(find_reusable_plan(pool, embedder, "fix the auth bug again"))

        assert match is not None
        assert match["id"] == "m1"
        assert match["method"] == "vector"
        assert match["decomposition"] == [{"id": 1, "goal": "patch middleware", "deps": []}]

    def test_no_match_returns_none(self):
        pool = FakeMethodPool()
        pool.add_row("m1", "an unrelated topic", unit(np.ones(8)),
                     io_schema={"decomposition": []})
        embedder = FakeEmbedder({"a totally different query": unit(-np.ones(8))})

        match = asyncio.run(find_reusable_plan(pool, embedder, "a totally different query"))

        assert match is None

    def test_gate_blocks_a_touch_set_mismatch(self):
        pool = FakeMethodPool()
        direction = unit(np.ones(8))
        pool.add_row("m1", "fix the auth bug", direction, io_schema={"decomposition": []},
                     success_criteria={"postconditions": ["touches:internal/auth/middleware.go"]})
        embedder = FakeEmbedder(
            {"fix the auth bug again": unit(direction + 0.001 * np.ones(8))})

        match = asyncio.run(find_reusable_plan(
            pool, embedder, "fix the auth bug again",
            query_postconditions=["touches:completely/different/file.go"]))

        assert match is None

    def test_gate_allows_overlapping_touch_sets(self):
        pool = FakeMethodPool()
        direction = unit(np.ones(8))
        pool.add_row("m1", "fix the auth bug", direction, io_schema={
            "decomposition": [{"id": 1, "goal": "patch it", "deps": []}]},
            success_criteria={"postconditions": [
                "touches:internal/auth/middleware.go", "lang:go"]})
        embedder = FakeEmbedder(
            {"fix the auth bug again": unit(direction + 0.001 * np.ones(8))})

        match = asyncio.run(find_reusable_plan(
            pool, embedder, "fix the auth bug again",
            query_postconditions=["touches:internal/auth/middleware.go", "lang:python"]))

        assert match is not None
        assert match["id"] == "m1"

    def test_omitted_query_postconditions_behaves_as_before(self):
        """Backward compatibility: a caller that never passes
        query_postconditions must see the gate trivially pass, same as
        before this parameter existed."""
        pool = FakeMethodPool()
        direction = unit(np.ones(8))
        pool.add_row("m1", "fix it", direction, io_schema={"decomposition": []},
                     success_criteria={"postconditions": ["touches:some/file.go"]})
        embedder = FakeEmbedder({"fix it now": unit(direction + 0.001 * np.ones(8))})

        match = asyncio.run(find_reusable_plan(pool, embedder, "fix it now"))

        assert match is not None


class TestPersistPlan:
    def test_writes_internal_proxy_and_touch_tags(self):
        pool = FakeMethodPool()
        embedder = FakeEmbedder({"goal text": [0.1] * 8})

        asyncio.run(persist_plan(
            pool, embedder, "goal text", [{"id": 1, "goal": "step", "deps": []}],
            steps_used=5, touch_tags=["touches:a.py", "touches:b.py"]))

        assert len(pool.inserted) == 1
        success_criteria = pool.inserted[0][3]   # positional: name, desc, io_schema, success_criteria, vec, created_by
        assert success_criteria["internal_proxy"] is True
        assert success_criteria["postconditions"] == ["touches:a.py", "touches:b.py"]

    def test_without_touch_tags_still_sets_internal_proxy(self):
        pool = FakeMethodPool()
        embedder = FakeEmbedder({"goal text": [0.1] * 8})

        asyncio.run(persist_plan(pool, embedder, "goal text", None, steps_used=3))

        success_criteria = pool.inserted[0][3]
        assert success_criteria["internal_proxy"] is True
        assert "postconditions" not in success_criteria
