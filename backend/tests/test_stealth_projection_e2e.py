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
                # B35 STRICT CLOSURE: the compact trio is the literal,
                # UNCONDITIONAL default -- no addressable pages/index
                # unless a caller explicitly opts in (see the dedicated
                # test below for that opt-in path). B35's own text: "Do
                # not maintain large duplicated claims.md/procedures.md/
                # implementations.md/run.md files unless an existing
                # integration strictly requires them" -- no real caller
                # anywhere in this codebase reads them, so the default
                # must not write them.
                entries = set(os.listdir(stealth_dir))
                assert entries == {"context.md", "run.json", "meta.json"}
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


def test_generate_projection_surfaces_real_relevant_claims_and_live_coordination():
    """B35 STRICT CLOSURE: the two remaining projection gaps, closed
    against the now-real B30 (`get_relevant_claims`) and B36
    (`coordination.py`'s live file-intent declarations) systems --
    `context.md`'s `[RELEVANT GLOBAL CLAIMS]`/`[COORDINATION]` sections
    and `run.json`'s `node_owners`/`file_intents` now surface real data,
    not permanent placeholders."""
    async def _run():
        from app.execution.coordination import declare_file_intent
        from app.services.claims import capture_claim
        from app.services.embeddings import Embedder

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-stealthproj-claims-{run_id}"
        claim_id = None
        claim_subject = f"b35-projection-claims-{run_id}"
        try:
            goal_text = f"provision the b35 projection claims cluster ({run_id})"
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")

            # capture_claim() requires >=1 live task_node to link to
            # (task_ids resolve against task_nodes.skill_ref) -- same
            # minimal real fixture test_relevant_claims_e2e.py's own
            # _capture_standalone_claim helper uses.
            await pool.execute("INSERT INTO task_nodes (name, skill_ref) VALUES ('t', $1)", claim_subject)
            claim_id = await capture_claim(
                pool, statement=f"the {claim_subject} service uses postgres",
                task_ids=[claim_subject], subject=claim_subject,
                predicate="uses", object="postgres", claim_type="fact",
                epistemic_status="observed", created_by="tester", scope_type="global",
                embedder=embedder,
            )
            assert claim_id is not None, "capture_claim silently dropped the claim"

            # get_run_context's own `objective` field is the CURRENT
            # NODE's real step goal (`current.get("goal")`), not the
            # top-level procedure goal -- the step goal must itself
            # share real words with the claim for get_relevant_claims'
            # hybrid retrieval to have a genuine lexical/semantic reason
            # to surface it over the rest of a large, real corpus.
            procedure = await _capture(
                pool, name, goal=goal_text, embedding=vec,
                embedding_model_id=embedder.embedding_model_id(),
                steps=[{"order": 0, "goal": goal_text}],
            )
            exec_run_id = await _start_run(pool, procedure)

            # A real, live file-intent declaration on this run's node 0.
            await declare_file_intent(
                pool, execution_run_id=exec_run_id, node_order=0, owner_agent_id="agent-b35-e2e",
                write_exact=[f"src/b35_{run_id}.py"], symbols_expected_to_modify=[f"B35Handler.{run_id}"],
            )

            with tempfile.TemporaryDirectory() as workspace:
                result = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                )
                context_md = result["context_md"]
                claims_section = context_md.split("[RELEVANT GLOBAL CLAIMS]")[1].split("[SELECTED PROCEDURES]")[0]
                assert "postgres" in claims_section, claims_section

                coord_section = context_md.split("[COORDINATION]")[1]
                assert "agent-b35-e2e" in coord_section
                assert f"src/b35_{run_id}.py" in coord_section
                assert f"B35Handler.{run_id}" in coord_section

                run_json = result["run_json"]
                assert run_json["node_owners"] == {0: "agent-b35-e2e"}
                assert len(run_json["file_intents"]) == 1
                fi = run_json["file_intents"][0]
                assert fi["write_exact"] == [f"src/b35_{run_id}.py"]
                assert fi["symbols_expected_to_modify"] == [f"B35Handler.{run_id}"]
        finally:
            if claim_id:
                await pool.execute("DELETE FROM knowledge_nodes WHERE id = $1", claim_id)
            await pool.execute("DELETE FROM task_nodes WHERE skill_ref = $1", claim_subject)
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
                    include_addressable_pages=True,
                )
                second = await sp.generate_projection(
                    pool, workspace_root=workspace, procedure_run_id=exec_run_id,
                    include_addressable_pages=True,
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
                    include_addressable_pages=True,
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
                    include_addressable_pages=True,
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
