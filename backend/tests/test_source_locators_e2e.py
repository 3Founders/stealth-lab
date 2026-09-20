"""Procedure + per-step source locators, and execution bindings bundled into steps (no Implementation object)."""
import os
import uuid

import pytest
import pytest_asyncio

from app.services.source_locators import (
    SourceLocatorError, binding_from_implementation, normalize_steps, validate_binding, validate_locator,
)

DB = os.environ.get("DATABASE_URL")
T = "loc" + uuid.uuid4().hex[:5]


# ------------------------------------------------------------------ offline
def test_locator_validation():
    ok = validate_locator({"uri": "https://x/y.md", "path": "y.md", "line_start": 3, "line_end": 9, "granularity": "span"})
    assert ok["line_end"] == 9
    for bad in ({}, {"path": "only-a-path"}, {"uri": "u", "line_start": 9, "line_end": 3}, {"uri": "u", "granularity": "word"},
                {"uri": "u", "nope": 1}, "not-an-object", {"uri": "u", "page": -1}):
        with pytest.raises(SourceLocatorError):
            validate_locator(bad)


def test_steps_inherit_the_procedure_locator_but_are_marked_and_strict_refuses_orphans():
    proc = {"source_id": "s", "uri": "u"}
    steps = normalize_steps([{"description": "a", "source_locator": {"uri": "u", "line_start": 1, "line_end": 2, "granularity": "span"}},
                             {"description": "b"}], proc, strict=True)
    assert steps[0]["source_locator"]["granularity"] == "span" and "inherited" not in steps[0]["source_locator"]
    assert steps[1]["source_locator"] == {"source_id": "s", "uri": "u", "granularity": "document", "inherited": True, "step_index": 1}
    with pytest.raises(SourceLocatorError):
        normalize_steps([{"description": "orphan"}], None, strict=True)           # nothing to cite
    with pytest.raises(SourceLocatorError):
        normalize_steps(["plain string step"], proc, strict=True)                  # a string cannot carry a locator
    assert normalize_steps([{"description": "x"}], None, strict=False) == [{"description": "x"}]


def test_binding_vocabulary_is_closed():
    b = validate_binding({"kind": "mcp_tool", "mcp_tool": "github.search", "server_url": "https://mcp.example/mcp", "parameters": {"q": "x"}, "verifier": {"type": "exit_code"},
                          "resources": {"cpu": 1}})
    assert b["kind"] == "mcp_tool"
    for bad in ({"kind": "teleport", "teleport": "x"}, {"kind": "tool"}, {"tool": "t", "weird": 1}, {"parameters": "nope"}):
        with pytest.raises(SourceLocatorError):
            validate_binding(bad)


def test_legacy_implementation_rows_fold_into_bindings():
    b = binding_from_implementation({"kind": "deterministic", "locator": {"path": "scripts/check.py"}, "requirements": {"python": ">=3.11"}})
    assert b == {"kind": "command", "command": "scripts/check.py", "locator": "scripts/check.py", "resources": {"python": ">=3.11"}}


# --------------------------------------------------------------------- live
pytestmark_live = pytest.mark.skipif(not DB, reason="requires DATABASE_URL")


@pytest_asyncio.fixture
async def pool():
    if not DB:
        pytest.skip("requires DATABASE_URL")
    from app.db.session import create_pool
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{T}%")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{T}%")
    await p.close()


@pytest.mark.asyncio
async def test_capture_stores_the_locator_on_the_procedure_and_on_every_step_and_versions_keep_it(pool):
    from app.services.procedures import capture_procedure, supersede_procedure
    r = await capture_procedure(
        pool, name=f"{T} p", goal=f"{T} do the thing", provenance="prior_library", scope_type="global",
        steps=[{"description": "one", "source_locator": {"uri": "u", "line_start": 4, "line_end": 6, "granularity": "span"},
                "binding": {"kind": "command", "command": "make test"}}, {"description": "two"}],
        source_locator={"source_id": "src", "uri": "u", "content_hash": "abc"}, require_source_locators=True)
    row = await pool.fetchrow("SELECT source_locator, steps FROM procedures WHERE id=$1::uuid", r["id"])
    assert row["source_locator"]["content_hash"] == "abc"
    assert row["steps"][0]["source_locator"]["line_start"] == 4 and row["steps"][0]["binding"]["command"] == "make test"
    assert row["steps"][1]["source_locator"]["inherited"] is True and row["steps"][1]["source_locator"]["step_index"] == 1
    v2 = await supersede_procedure(pool, prior_row_id=r["id"], changed_fields={"name": f"{T} p2"})
    assert (await pool.fetchval("SELECT source_locator->>'content_hash' FROM procedures WHERE id=$1::uuid", v2["id"])) == "abc"


@pytest.mark.asyncio
async def test_strict_capture_refuses_a_step_that_cannot_be_cited_and_writes_nothing(pool):
    from app.services.procedures import capture_procedure
    with pytest.raises(SourceLocatorError):
        await capture_procedure(pool, name=f"{T} bad", goal=f"{T} bad goal", provenance="prior_library", scope_type="global",
                                steps=[{"description": "orphan"}], require_source_locators=True)
    assert await pool.fetchval("SELECT count(*) FROM procedures WHERE name=$1", f"{T} bad") == 0
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name=$1", f"{T} bad goal") == 0   # refused BEFORE any write
    with pytest.raises(SourceLocatorError):
        await capture_procedure(pool, name=f"{T} bad2", goal=f"{T} bad goal 2", provenance="prior_library", scope_type="global",
                                steps=[{"description": "x", "binding": {"kind": "teleport", "teleport": "y"}}],
                                source_locator={"uri": "u"})


@pytest.mark.asyncio
async def test_bundle_handler_requires_and_derives_locators(pool):
    from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge
    from app.ingestion.handlers import Dependencies, handle_ingest_candidate_bundle
    Dependencies.configure(embedder=ConceptEmbedder(), judge=make_judge(FrozenProvider({})))
    try:
        out = await handle_ingest_candidate_bundle(pool, {
            "source_key": f"{T}-b1", "source_uri": f"https://example.test/{T}/b1", "goal": f"{T} bundle goal",
            "procedure": {"name": f"{T} bundle proc", "steps": [{"description": "s1"}, {"description": "s2", "source_locator": {"uri": "https://x", "anchor": "#s2", "granularity": "section"}}]},
            "claims": [], "_job": {"id": 1, "scope_type": "global", "visibility": "public"}})
        row = await pool.fetchrow("SELECT source_locator, steps FROM procedures WHERE id=$1::uuid", out["procedure"]["id"])
        assert row["source_locator"]["uri"] == f"https://example.test/{T}/b1"
        assert row["steps"][0]["source_locator"]["inherited"] is True and row["steps"][1]["source_locator"]["anchor"] == "#s2"
    finally:
        Dependencies.configure()
        await pool.execute("DELETE FROM ingestion_contexts WHERE source_uri LIKE $1", f"https://example.test/{T}/%")
