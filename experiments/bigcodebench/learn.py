"""Kel learns from the fit split -- through its own pipelines, into the demo database only.

    python learn.py extract      # one Procedure per fit task, from a verified success (Kel extraction)
    python learn.py knowledge    # runs/knowledge_fit.json: each fit task's own Procedure, as a prompt block
    (then: python run_models.py --split fit --condition kel     -- validation runs that USE the Procedures)
    python learn.py evidence     # validation outcomes -> the Procedures' verified execution evidence
    python learn.py observe      # every fit attempt -> recommender observations (visible tests = check,
                                 #   full suite = gold label)
    python learn.py refit        # recommender joint fit

The raw fit runs are NOT counted as evidence for any Procedure (none existed when they ran).
Reference solutions are never used: Procedures come only from attempts the local harness graded.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import demo_env

from run_models import load_attempts, sample_tasks

PREFERENCE = ("gemma-4-31B-it", "deepseek-v3.2", "gpt-oss-120b", "claude-sonnet-5")   # cheapest verified success first
PROCS = demo_env.RUNS / "procedures_fit.json"
UNIT_SCAFFOLD = {"claude-sonnet-5": "claude-code-subagent"}


def manifest() -> dict[str, dict]:
    return {m["external_id"]: m for m in json.loads((demo_env.RUNS / "manifest_fit.json").read_text(encoding="utf-8"))}


def gc_client() -> Any:
    from openai import OpenAI

    from app.config import settings
    return OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url)


async def extract(pool: Any) -> None:
    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    tasks, man = sample_tasks("fit"), manifest()
    done = json.loads(PROCS.read_text(encoding="utf-8")) if PROCS.exists() else {}
    wins: dict[str, dict] = {}
    for r in load_attempts():
        if r["split"] == "fit" and r["condition"] == "raw" and r["gold_pass"]:
            best = wins.get(r["task_id"])
            if best is None or PREFERENCE.index(r["model"]) < PREFERENCE.index(best["model"]):
                wins[r["task_id"]] = r
    client = gc_client()
    for task_id, attempt in sorted(wins.items()):
        if task_id in done:
            continue
        task = tasks[task_id]
        source = AgentRunEvidenceSource(
            goal_text=task.goal_name, outcome="success", tool_sequence=["write_code", "run_tests"], steps_used=2,
            observations=[
                {"observation_type": "task_statement", "label": "task", "properties": {"text": task.goal_description}},
                {"observation_type": "code_solution", "label": "verified solution",
                 "properties": {"code": attempt["code"], "verified": True, "language": "python",
                                "verified_by": "full unittest suite (local harness)"}},
            ])
        try:
            result = await extract_procedure(pool, source, client=client, visibility="public")
        except Exception as exc:  # noqa: BLE001 -- report and continue with the next task
            print(f"{task_id:<18} extraction failed: {exc!r}"[:200])
            continue
        if not result.procedure_id:
            print(f"{task_id:<18} no procedure: {result.validation_failures or 'abstained'}"[:200])
            continue
        goal_of = await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id = $1::uuid",
                                      str(result.version_row_id or result.procedure_id))
        done[task_id] = {"procedure_id": str(result.procedure_id), "version_row_id": str(result.version_row_id),
                         "extracted_by": result.extracted_by, "from_model": attempt["model"],
                         "goal_id": goal_of, "expected_goal_id": man[task_id]["goal_id"]}
        PROCS.write_text(json.dumps(done, indent=1), encoding="utf-8")
        print(f"{task_id:<18} procedure from {attempt['model']:<16} goal_matches={goal_of == man[task_id]['goal_id']}")
    print(f"procedures: {len(done)} of {len(tasks)} fit tasks ({len(wins)} had a verified success)")


async def procedure_text(pool: Any, procedure_id: str) -> str:
    row = await pool.fetchrow(
        "SELECT name, capability_statement, steps, failure_conditions FROM procedures "
        "WHERE procedure_id = $1::uuid AND t_invalid IS NULL ORDER BY version DESC LIMIT 1", procedure_id)
    steps = row["steps"] if isinstance(row["steps"], list) else json.loads(row["steps"] or "[]")
    pitfalls = row["failure_conditions"] if isinstance(row["failure_conditions"], list) \
        else json.loads(row["failure_conditions"] or "[]")
    lines = [f"Way: {row['capability_statement'] or row['name']}"]
    for s in steps:
        apis = ", ".join(i.get("name", "") for i in (s.get("allowed_implementations") or []) if i.get("name"))
        lines.append(f"  {s.get('order')}. {s.get('action')}" + (f"  [uses: {apis}]" if apis else ""))
    if pitfalls:
        lines.append("Pitfalls the tests check:")
        lines += [f"  - {p}" for p in pitfalls]
    return "\n".join(lines)


async def knowledge(pool: Any) -> None:
    procs = json.loads(PROCS.read_text(encoding="utf-8"))
    out = {tid: {"text": await procedure_text(pool, p["procedure_id"]), "ref": p["procedure_id"]}
           for tid, p in procs.items()}
    (demo_env.RUNS / "knowledge_fit.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"knowledge blocks: {len(out)}")


async def evidence(pool: Any) -> None:
    from app.services.procedures import record_execution_outcome

    procs = json.loads(PROCS.read_text(encoding="utf-8"))
    recorded = 0
    for r in load_attempts():
        if r["split"] != "fit" or r["condition"] != "kel" or not r.get("knowledge_ref"):
            continue
        p = next((v for v in procs.values() if v["procedure_id"] == r["knowledge_ref"]), None)
        if p is None or r.get("_evidence_recorded"):
            continue
        await record_execution_outcome(
            pool, procedure_row_id=p["version_row_id"], success=bool(r["gold_pass"]),
            context_key=f"bigcodebench:{r['task_id']}:{r['model']}",
            success_criteria={"predicate": "the task's full unittest suite passes"} if r["gold_pass"] else None,
            failure_class=None, execution_verified=True)
        recorded += 1
    print(f"verified procedure outcomes recorded: {recorded}")


async def observe(pool: Any) -> None:
    from app.routing import store

    man = manifest()
    procs = json.loads(PROCS.read_text(encoding="utf-8")) if PROCS.exists() else {}
    rows = []
    for r in load_attempts():
        if r["split"] != "fit" or r.get("call_error"):
            continue
        rows.append({
            "source": "sweep", "goal_id": man[r["task_id"]]["goal_id"],
            "procedure_id": procs.get(r["task_id"], {}).get("procedure_id") if r["condition"] == "kel" else None,
            "model_key": r["model"], "scaffold": UNIT_SCAFFOLD.get(r["model"], "direct-prompt"),
            "instance_key": f"bigcodebench:{r['task_id']}", "check_kind": "tests",
            "accepted": bool(r["visible_pass"]), "gold_correct": bool(r["gold_pass"]),
            "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "reporter": None,
            "visibility": "public", "occurred_at": None,
        })
    existing = await (await __import__("app.services.shards", fromlist=["search_pool"]).search_pool(pool)).fetchval(
        "SELECT count(*) FROM routing_observations WHERE source = 'sweep'")
    if existing:
        print(f"observations already imported ({existing}); skipping")
        return
    ids = await store.insert_observations(pool, rows)
    print(f"observations imported: {len(ids)}")


async def refit(pool: Any) -> None:
    from app.routing.config import RoutingDefaults
    from app.routing.fit import nightly_refit

    cfg = RoutingDefaults(draws=128, nightly_warmup=400, nightly_chains=2, nightly_samples=300, embedding_dims=4)
    print(json.dumps(await nightly_refit(pool, cfg, seed=1), indent=1, default=str))


async def main(action: str) -> None:
    demo_env.verify_after_import()
    from app.db.session import create_pool

    pool = await create_pool(demo_env.DEMO_DSN, min_size=1, max_size=4)
    try:
        await {"extract": extract, "knowledge": knowledge, "evidence": evidence, "observe": observe,
               "refit": refit}[action](pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
