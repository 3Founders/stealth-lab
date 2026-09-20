"""Legacy implementation snapshots fold into step bindings / one-step procedures through the normal capture path."""
import json
import os
import uuid

import pytest
import pytest_asyncio

DB = os.environ.get("DATABASE_URL")
T = "fold" + uuid.uuid4().hex[:5]
pytestmark = pytest.mark.skipif(not DB, reason="requires DATABASE_URL")


@pytest_asyncio.fixture
async def pool():
    from app.db.session import create_pool
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM legacy_implementation_fold WHERE implementation->>'name' LIKE $1", f"{T}%")
    await p.execute("DELETE FROM procedures WHERE name LIKE $1 OR source_key LIKE $2", f"{T}%", "legacy-implementation:%")
    await p.execute("DELETE FROM goals WHERE canonical_name LIKE $1", f"{T}%")
    await p.close()


def _impl(name, **kw):
    base = {"name": name, "kind": "deterministic", "provider": "skill-package", "version": 1, "goal": f"{name} goal",
            "description": f"legacy {name}", "locator": {"path": f"scripts/{name}.py", "url": f"https://example.test/{name}.py"},
            "requirements": {}, "verification_contract": {"type": "deterministic", "check": "exit_code_zero"},
            "scope_type": "global", "visibility": "public", "content_hash": "abc"}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_unlinked_implementation_becomes_a_one_step_procedure_with_binding_and_locator(pool):
    from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge
    from app.services.fold_implementations import fold_all
    iid = uuid.uuid4()
    await pool.execute("INSERT INTO legacy_implementation_fold (implementation_id, implementation) VALUES ($1, $2::jsonb)", iid, json.dumps(_impl(f"{T}-a")))
    rep = await fold_all(pool, embedder=ConceptEmbedder(), judge=make_judge(FrozenProvider({})))
    assert rep["failed"] == 0, rep
    row = await pool.fetchrow("SELECT folded_procedure_id, fold_note FROM legacy_implementation_fold WHERE implementation_id=$1", iid)
    assert row["fold_note"] == "one_step_procedure"
    proc = await pool.fetchrow("SELECT steps, source_locator FROM procedures WHERE procedure_id=$1 AND t_invalid IS NULL", row["folded_procedure_id"])
    steps = proc["steps"] if isinstance(proc["steps"], list) else json.loads(proc["steps"])
    assert len(steps) == 1 and steps[0]["binding"]["kind"] == "command"
    assert steps[0]["source_locator"]["uri"] == f"https://example.test/{T}-a.py"
    # re-running is a no-op (already folded)
    assert (await fold_all(pool))["folded"] == 0


@pytest.mark.asyncio
async def test_linked_implementation_becomes_a_binding_on_the_procedure_steps(pool):
    from tests.identity_fakes import ConceptEmbedder, FrozenProvider, make_judge
    from app.services.fold_implementations import fold_all
    from app.services.procedures import capture_procedure
    emb, judge = ConceptEmbedder(), make_judge(FrozenProvider({}))
    p = await capture_procedure(pool, name=f"{T} multi", goal=f"{T} multi goal", provenance="prior_library", scope_type="global",
                                steps=[{"order": 0, "description": "prepare"}, {"order": 1, "description": "check"}],
                                goal_embedder=emb, goal_judge=judge)
    iid = uuid.uuid4()
    await pool.execute("INSERT INTO legacy_implementation_fold (implementation_id, implementation, links) VALUES ($1, $2::jsonb, $3::jsonb)",
                       iid, json.dumps(_impl(f"{T}-b")), json.dumps([{"procedure_id": str(p["procedure_id"]), "supported_steps": [1]}]))
    rep = await fold_all(pool, embedder=emb, judge=judge)
    assert rep["failed"] == 0, rep
    live = await pool.fetchrow("SELECT steps FROM procedures WHERE procedure_id=$1 AND t_invalid IS NULL ORDER BY version DESC LIMIT 1", p["procedure_id"])
    steps = live["steps"] if isinstance(live["steps"], list) else json.loads(live["steps"])
    assert "binding" not in steps[0] and steps[1]["binding"]["kind"] == "command"          # only the supported step is bound
