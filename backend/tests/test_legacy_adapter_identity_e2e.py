"""Judged Procedure identity for the OLDER adapters (opt-in via capture_procedure(procedure_dedup=True)),
and the guarantee that the local tier is untouched."""
import os
import pathlib
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services.procedures import capture_procedure
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires DATABASE_URL")
T = "leg" + uuid.uuid4().hex[:5]
EMB = ConceptEmbedder()
GOAL = f"{T} find callers of a function"


def fn(kind, a, b):
    al, bl = a.lower(), b.lower()
    if T not in bl:
        return ("distinct", .9)
    if kind == "goal":
        return ("distinct", .9)
    if "grep for callers" in al and "grep callers" in bl and "-n" not in bl:
        return ("same", .95)
    if "-n" in al and "grep callers" in bl:
        return ("refinement", .9)
    return ("distinct", .9)


JUDGE = make_judge(CallbackProvider(fn, name="jev"))
NO_PROVIDERS = make_judge()


@pytest_asyncio.fixture
async def pool():
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM identity_decisions WHERE candidate_text LIKE $1", f"{T}%")
    await p.execute("DELETE FROM procedures WHERE name LIKE $1", f"{T}%")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{T}%")
    await p.close()


async def cap(pool, name, src, **kw):
    return await capture_procedure(pool, name=f"{T} {name}", goal=GOAL, steps=[{"description": name}], provenance="prior_library",
                                   scope_type="global", goal_embedder=EMB, goal_judge=kw.pop("judge", JUDGE),
                                   source_key=f"{T}-{src}", procedure_dedup=kw.pop("dedup", True), **kw)


@pytest.mark.asyncio
async def test_same_method_from_another_source_is_reused_and_the_source_attached(pool):
    a = await cap(pool, "grep callers", "s1")
    b = await cap(pool, "grep for callers", "s2")
    assert b["reused"] and b["procedure_id"] == a["procedure_id"] and b["id"] == a["id"]
    assert await pool.fetchval("SELECT count(*) FROM procedures WHERE name LIKE $1", f"{T}%") == 1
    refs = await pool.fetchval("SELECT evidence_refs FROM procedures WHERE id=$1::uuid", a["id"])
    assert any(r.get("source_key") == f"{T}-s2" for r in refs)


@pytest.mark.asyncio
async def test_refinement_becomes_a_new_version_and_a_different_method_stays_separate(pool):
    a = await cap(pool, "grep callers", "s1")
    v = await cap(pool, "grep callers -n", "s2")
    assert v["new_version"] and v["procedure_id"] == a["procedure_id"] and v["id"] != a["id"]
    alt = await cap(pool, "lsp find references", "s3")
    assert alt["procedure_id"] != a["procedure_id"] and not alt.get("reused")
    link = {r["achieves_goal_id"] for r in await pool.fetch("SELECT achieves_goal_id FROM procedures WHERE name LIKE $1 AND t_invalid IS NULL", f"{T}%")}
    assert len(link) == 1                                                              # both methods hang off the one goal


@pytest.mark.asyncio
async def test_without_a_semantic_provider_or_without_opt_in_behaviour_is_unchanged(pool):
    a = await cap(pool, "grep callers", "s1")
    b = await cap(pool, "grep for callers", "s2", judge=NO_PROVIDERS)                   # nothing can judge -> plain capture
    c = await cap(pool, "grep for callers", "s3", dedup=False)                          # local-tier style caller: never judged
    assert not b.get("reused") and not c.get("reused")
    assert len({a["procedure_id"], b["procedure_id"], c["procedure_id"]}) == 3


def test_the_canonical_adapters_opt_in_and_the_local_tier_does_not():
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    assert "procedure_dedup=True" in (root / "services/skill_ingestion.py").read_text(encoding="utf-8")
    assert "procedure_dedup=True" in (root / "services/publication.py").read_text(encoding="utf-8")
    for local in ("stealth/local_sync.py", "services/trajectory_semantics.py", "services/procedure_extraction/__init__.py"):
        assert "procedure_dedup" not in (root / local).read_text(encoding="utf-8"), local
