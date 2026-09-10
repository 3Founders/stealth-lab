"""
MCP hardening B35: live-DB half of the `.stealth/` projection --
`generate_projection` end to end against a real ProcedureRun, writing
real files, plus the byte-budget truncation and nonexistent-run refusal.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution import stealth_projection as sp
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _start_run(pool, procedure: dict) -> str:
    steps = procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_stealth_projection_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
    )


def test_generate_projection_writes_real_files_with_valid_content():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-stealthproj-{run_id}"
        try:
            never_asserted = f"project:stealthproj-{run_id}"
            procedure = await _capture(
                pool, name, postconditions=["it works"],
                preconditions=[{"subject": never_asserted, "predicate": "quota", "object": "ok"}],
                steps=[{"order": 0, "goal": "do the work"}],
            )
            exec_run_id = await _start_run(pool, procedure)

            with tempfile.TemporaryDirectory() as workspace:
                result = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                for key in ("context_md", "run_json", "meta_json"):
                    assert key in result

                stealth_dir = os.path.join(workspace, ".stealth")
                # The compact B35 trio is always present; the generator now
                # also writes the addressable pages + index/ (app.stealth).
                entries = set(os.listdir(stealth_dir))
                assert {"context.md", "run.json", "meta.json"} <= entries
                assert {"claims.md", "procedures.md", "implementations.md", "run.md", "index"} <= entries
                assert set(os.listdir(os.path.join(stealth_dir, "index"))) == {
                    "root.idx", "claims.idx", "procedures.idx", "implementations.idx", "run.idx",
                }
                assert not [n for n in entries if n.startswith(".tmp-stealth-")]

                with open(os.path.join(stealth_dir, "context.md"), encoding="utf-8") as f:
                    context_md = f.read()
                assert "[SELECTED PROCEDURES]" in context_md
                assert never_asserted in context_md

                with open(os.path.join(stealth_dir, "run.json"), encoding="utf-8") as f:
                    run_json = json.load(f)
                assert run_json["procedure_run_id"] == exec_run_id
                assert run_json["verification_state"] == "inconclusive"

                with open(os.path.join(stealth_dir, "meta.json"), encoding="utf-8") as f:
                    meta_json = json.load(f)
                assert meta_json["workspace_root"] == workspace
                assert "projection_revision" in meta_json
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_generate_projection_raises_for_nonexistent_run():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as workspace:
                with pytest.raises(sp.StealthProjectionError):
                    await sp.generate_projection(
                        pool, workspace_root=workspace, procedure_run_id=str(uuid4()),
                    )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_generate_projection_regeneration_is_idempotent_and_overwrites():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-stealthproj-regen-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "do it"}])
            exec_run_id = await _start_run(pool, procedure)

            with tempfile.TemporaryDirectory() as workspace:
                first = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                second = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                # Same canonical state -> same run.json content (minus
                # nothing time-dependent in this fixture's node states).
                assert first["run_json"]["node_states"] == second["run_json"]["node_states"]
                assert first["root_idx"] == second["root_idx"]
                stealth_dir = os.path.join(workspace, ".stealth")
                entries = set(os.listdir(stealth_dir))
                assert {"context.md", "run.json", "meta.json", "claims.md", "procedures.md",
                        "implementations.md", "run.md", "index"} <= entries
                assert not [n for n in entries if n.startswith(".tmp-stealth-")]
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_context_md_truncates_when_it_would_exceed_the_configured_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-stealthproj-budget-{run_id}"
        monkeypatch.setattr(sp, "CONTEXT_MD_MAX_BYTES", 50)
        try:
            procedure = await _capture(pool, name, postconditions=["it works"])
            exec_run_id = await _start_run(pool, procedure)
            with tempfile.TemporaryDirectory() as workspace:
                result = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                assert "TRUNCATED" in result["context_md"]
                assert len(result["context_md"].encode("utf-8")) <= 50 + 200  # truncation marker itself
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_every_index_row_resolves_to_exactly_its_block_and_regen_is_stable():
    """T11 navigation contract against a real run: each `.idx` row's
    (file, start, end) slices out exactly the object it names, the root
    router stays within budget, regeneration is byte-identical, and
    meta.json carries the staleness signal."""
    from app.stealth.format import ROOT_IDX_MAX_BYTES, parse_idx

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-stealthproj-nav-{run_id}"
        try:
            procedure = await _capture(
                pool, name, postconditions=["it works"],
                preconditions=[
                    {"subject": f"svc:{run_id}", "predicate": "lang", "object": "python"},
                    {"subject": f"db:{run_id}", "predicate": "engine", "object": "postgres"},
                ],
                steps=[{"order": 0, "goal": "enumerate callers"},
                       {"order": 1, "goal": "classify deps"}],
            )
            exec_run_id = await _start_run(pool, procedure)
            with tempfile.TemporaryDirectory() as workspace:
                first = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                sdir = os.path.join(workspace, ".stealth")

                root = open(os.path.join(sdir, "index", "root.idx"), encoding="utf-8").read()
                assert len(root.encode("utf-8")) <= ROOT_IDX_MAX_BYTES

                checked = 0
                for idx_name in ("claims.idx", "procedures.idx", "implementations.idx"):
                    text = open(os.path.join(sdir, "index", idx_name), encoding="utf-8").read()
                    for fields in parse_idx(text):
                        obj_id, _, _, _, _, mdfile, start, end, _ = fields
                        md_lines = open(os.path.join(sdir, mdfile), encoding="utf-8").read().splitlines()
                        window = md_lines[int(start) - 1:int(end)]
                        assert window and window[0].startswith("## ")
                        assert obj_id in window[0], (obj_id, window[0])
                        assert not any(ln.startswith("## ") for ln in window[1:]), "block bled into next"
                        checked += 1
                assert checked >= 3

                second = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                for k in ("root_idx", "claims_idx", "procedures_idx", "implementations_idx", "run_idx"):
                    assert first[k] == second[k]

                meta = json.loads(open(os.path.join(sdir, "meta.json"), encoding="utf-8").read())
                assert meta["change_cursor"].startswith(exec_run_id)
                assert set(meta["revisions"]) == {"claims", "procedures", "implementations", "run"}
                assert meta["counts"]["run_nodes"] == 2
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
