"""
Real, live-database e2e tests for local_retrieval.py (ticket 14). Same
pattern as the other e2e test files: requires a real DATABASE_URL,
skips (not fails) without one.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.local_retrieval import (
    StructuralContext,
    get_current_working_set,
    get_recent_commit_files,
    retrieve_local_first,
)
from app.services.observations import persist_observation

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, session_id: str, name_prefix: str = "") -> None:
    await pool.execute(
        "DELETE FROM observation_events WHERE event_id IN "
        "(SELECT id FROM trace_events WHERE session_id = $1)", session_id,
    )
    await pool.execute(
        "DELETE FROM observations WHERE id NOT IN "
        "(SELECT observation_id FROM observation_events)"
    )
    await pool.execute("DELETE FROM trace_events WHERE session_id = $1", session_id)
    await pool.execute("DELETE FROM agent_traces WHERE session_id = $1", session_id)
    if name_prefix:
        await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{name_prefix}%")
        await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{name_prefix}%")


async def _insert_trace_event(pool, session_id: str, sequence: int) -> str:
    trace_id = await pool.fetchval(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
        "VALUES ($1, $2, now(), '1') ON CONFLICT (trace_id) DO NOTHING RETURNING trace_id",
        session_id, session_id,
    )
    if trace_id is None:
        trace_id = session_id
    event_id = await pool.fetchval(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
        "\"timestamp\", tool_name, dedup_key, schema_version) "
        "VALUES ($1,$2,$3,'PostToolUse',now(),'Edit',$4,'1') RETURNING id",
        trace_id, session_id, sequence, f"local-retr-dedup-{session_id}-{sequence}",
    )
    return str(event_id)


def test_get_current_working_set_returns_real_file_touched_paths():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "local-retr-test-session-cws"
        try:
            await _cleanup(pool, session_id)
            for i, path in enumerate(["app/a.py", "app/b.py", "app/a.py"]):
                event_id = await _insert_trace_event(pool, session_id, i)
                await persist_observation(
                    pool, observation_type="file_touched", label=f"Modified {path}",
                    extractor_kind="deterministic", event_ids=[event_id],
                    properties={"file_path": path, "tool_name": "Edit"},
                )

            files = await get_current_working_set(pool, session_id=session_id)
            assert set(files) == {"app/a.py", "app/b.py"}, "duplicates must collapse to distinct paths"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_get_current_working_set_orders_most_recent_first():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "local-retr-test-session-order"
        try:
            await _cleanup(pool, session_id)
            for i, path in enumerate(["first.py", "second.py", "third.py"]):
                event_id = await _insert_trace_event(pool, session_id, i)
                await persist_observation(
                    pool, observation_type="file_touched", label=f"Modified {path}",
                    extractor_kind="deterministic", event_ids=[event_id],
                    properties={"file_path": path, "tool_name": "Edit"},
                )
                await asyncio.sleep(0.01)  # real, small gap so extracted_at actually differs

            files = await get_current_working_set(pool, session_id=session_id)
            assert files[0] == "third.py", "most recently touched file must come first"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_get_current_working_set_scoped_to_its_own_session():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_a = "local-retr-test-session-a"
        session_b = "local-retr-test-session-b"
        try:
            await _cleanup(pool, session_a)
            await _cleanup(pool, session_b)
            event_a = await _insert_trace_event(pool, session_a, 0)
            await persist_observation(
                pool, observation_type="file_touched", label="Modified a_only.py",
                extractor_kind="deterministic", event_ids=[event_a],
                properties={"file_path": "a_only.py", "tool_name": "Edit"},
            )
            event_b = await _insert_trace_event(pool, session_b, 0)
            await persist_observation(
                pool, observation_type="file_touched", label="Modified b_only.py",
                extractor_kind="deterministic", event_ids=[event_b],
                properties={"file_path": "b_only.py", "tool_name": "Edit"},
            )

            files_a = await get_current_working_set(pool, session_id=session_a)
            assert files_a == ["a_only.py"]
        finally:
            await _cleanup(pool, session_a)
            await _cleanup(pool, session_b)
            await pool.close()

    asyncio.run(_run())


def test_get_recent_commit_files_returns_files_touched_before_the_commit():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "local-retr-test-session-commit"
        try:
            await _cleanup(pool, session_id)
            event1 = await _insert_trace_event(pool, session_id, 0)
            await persist_observation(
                pool, observation_type="file_touched", label="Modified committed.py",
                extractor_kind="deterministic", event_ids=[event1],
                properties={"file_path": "committed.py", "tool_name": "Edit"},
            )
            event2 = await _insert_trace_event(pool, session_id, 1)
            await persist_observation(
                pool, observation_type="commit_made", label="Committed: git commit -m x",
                extractor_kind="deterministic", event_ids=[event2],
                properties={"command": "git commit -m x"},
            )

            files = await get_recent_commit_files(pool, session_id=session_id)
            assert "committed.py" in files
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.3] * 1024


def test_retrieve_local_first_structural_tier_finds_nodes_semantic_search_would_miss():
    """The real union guarantee: a node matched ONLY via the structural
    (open_files) signal -- deliberately far from the query in both
    embedding space and lexical overlap -- must still appear in the
    assembled context, because structural is a genuinely independent
    tier, not a filter/boost applied only to what semantic search
    already found."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-union")
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-union-structural-only', "
                "'touches app/services/completely_unrelated_path.py', 's1')"
            )

            result = await retrieve_local_first(
                pool, "totally different query text with no overlap",
                embedder=FakeEmbedder(),
                structural=StructuralContext(
                    open_files=["app/services/completely_unrelated_path.py"],
                ),
            )
            assert "local-retr-test-union-structural-only" in result.text
            assert result.tiers_included.get("structural", 0) >= 1
        finally:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-union")
            await pool.close()

    asyncio.run(_run())


def test_retrieve_local_first_structural_tier_outranks_semantic_in_budget_fill():
    """Direct confirmation of priority-ordered fill through the real
    pipeline, not just the pure assemble_context tests: with a tight
    token budget, a structural-tier match must be included before a
    semantic-tier match."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-priority")
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-priority-structural', "
                "'touches app/priority_test_path.py', 's1')"
            )
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-priority-semantic-match query terms here', "
                "'a semantic match', 's2')"
            )

            result = await retrieve_local_first(
                pool, "local-retr-test-priority-semantic-match query terms here",
                embedder=FakeEmbedder(),
                structural=StructuralContext(open_files=["app/priority_test_path.py"]),
                token_budget=40,  # tight -- forces a real choice between tiers
            )
            assert "local-retr-test-priority-structural" in result.text, (
                "structural tier must be filled before semantic under a tight budget"
            )
        finally:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-priority")
            await pool.close()

    asyncio.run(_run())


def test_related_test_files_wired_end_to_end_into_structural_tier(tmp_path):
    """Real, live confirmation that related_tests.py's output plugs
    directly into StructuralContext.related_tests and works through the
    full retrieve_local_first pipeline -- a real repo checkout (tmp_path
    fixture), a real source file, a real matching test file, and a node
    in the DB whose description names that test file, findable ONLY via
    the related_tests structural signal (no semantic/lexical overlap
    with the query at all)."""
    async def _run():
        from app.services.related_tests import related_test_files

        source_dir = tmp_path / "app" / "services"
        source_dir.mkdir(parents=True)
        (source_dir / "widget.py").write_text("x = 1\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_widget.py").write_text("def test_x(): pass\n")

        related = related_test_files(str(tmp_path), "app/services/widget.py")
        assert related == ["tests/test_widget.py"]

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-relwired")
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-relwired-node', "
                "'covered by tests/test_widget.py', 's1')"
            )

            result = await retrieve_local_first(
                pool, "completely unrelated query text",
                embedder=FakeEmbedder(),
                structural=StructuralContext(related_tests=related),
            )
            assert "local-retr-test-relwired-node" in result.text
            assert result.tiers_included.get("structural", 0) >= 1
        finally:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-relwired")
            await pool.close()

    asyncio.run(_run())


def test_relevant_symbols_wired_end_to_end_into_structural_tier(tmp_path):
    """Real confirmation that get_relevant_symbols() (wrapping
    code_index.outline()) plugs into StructuralContext and works through
    the full pipeline -- a real Python file with a real defined function,
    found via the structural tier with zero semantic/lexical overlap."""
    async def _run():
        from app.services.local_retrieval import get_relevant_symbols

        source_dir = tmp_path / "app"
        source_dir.mkdir(parents=True)
        (source_dir / "gadget.py").write_text("def compute_gadget_value():\n    return 1\n")

        symbols = get_relevant_symbols(str(tmp_path), ["app/gadget.py"])
        assert "compute_gadget_value" in symbols

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-symwired")
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-symwired-node', "
                "'implements compute_gadget_value for the report', 's1')"
            )
            result = await retrieve_local_first(
                pool, "totally unrelated query text",
                embedder=FakeEmbedder(),
                structural=StructuralContext(relevant_symbols=symbols),
            )
            assert "local-retr-test-symwired-node" in result.text
            assert result.tiers_included.get("structural", 0) >= 1
        finally:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-symwired")
            await pool.close()

    asyncio.run(_run())


def test_import_deps_wired_end_to_end_into_structural_tier(tmp_path):
    """Real confirmation that import_targets_for_many() (import_deps.py)
    plugs into StructuralContext and works through the full pipeline."""
    async def _run():
        from app.services.import_deps import import_targets_for_many

        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "dep.py").write_text("y = 1\n")
        (tmp_path / "app" / "consumer.py").write_text("from app.dep import y\n")

        deps = import_targets_for_many(str(tmp_path), ["app/consumer.py"])
        assert "app/dep.py" in deps

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-depwired")
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-depwired-node', "
                "'defines the value read by app/dep.py', 's1')"
            )
            result = await retrieve_local_first(
                pool, "totally unrelated query text",
                embedder=FakeEmbedder(),
                structural=StructuralContext(import_deps=deps),
            )
            assert "local-retr-test-depwired-node" in result.text
        finally:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-depwired")
            await pool.close()

    asyncio.run(_run())


def test_call_graph_ranked_names_boosts_semantic_tier_ranking(tmp_path):
    """Real confirmation of get_call_graph_ranked_names() through the
    full pipeline. Unlike the FILTER-tier producers above, this is a
    RANK signal -- it boosts ordering WITHIN the semantic tier, it does
    not add independent candidates. Confirmed by checking that a node
    matched by BOTH a real query overlap AND the call-graph signal ranks
    at or above one matched by query overlap alone."""
    async def _run():
        from app.services.local_retrieval import get_call_graph_ranked_names

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "entry.py").write_text(
            "from app.helper import helper_func\n\ndef entry_func():\n    return helper_func()\n"
        )
        (app_dir / "helper.py").write_text("def helper_func():\n    return 1\n")

        ranked_files = get_call_graph_ranked_names(str(tmp_path), ["app/entry.py"])
        assert "app/helper.py" in ranked_files, f"expected app/helper.py reachable, got {ranked_files}"

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-cgboost")
            # Both nodes share query words (real semantic/lexical match),
            # but only ONE also matches the call-graph signal.
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-cgboost shared query words boosted', "
                "'touches app/helper.py directly', 's1')"
            )
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) "
                "VALUES ('local-retr-test-cgboost shared query words plain', "
                "'no call graph relation', 's2')"
            )

            result = await retrieve_local_first(
                pool, "local-retr-test-cgboost shared query words",
                embedder=FakeEmbedder(),
                structural=StructuralContext(call_graph_ranked_names=ranked_files),
            )
            names_in_order = [n for n in result.text.split("\n")]
            boosted_idx = next(i for i, l in enumerate(names_in_order) if "boosted" in l)
            plain_idx = next(i for i, l in enumerate(names_in_order) if "plain" in l)
            assert boosted_idx <= plain_idx, (
                "the call-graph-matched node should rank at or above the plain match"
            )
        finally:
            await _cleanup(pool, "local-retr-test-nosession", name_prefix="local-retr-test-cgboost")
            await pool.close()

    asyncio.run(_run())
