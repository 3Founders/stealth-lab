"""Kel learns from the TRAIN POOL only, through its own production pipelines, into the local
kel_swebench database. Run after the train pool is generated AND graded.

    python learn.py name        # a Goal name per train instance (experiment model, temperature 0)
    python learn.py import      # train instances -> Goals: embedded, production identity (judge_mode="model")
    python learn.py worker      # the REAL ingestion Worker: goal_abstraction_placement + the jobs it enqueues
    python learn.py extract     # every RESOLVED train attempt -> Procedure via extract_procedure (up to 5 attempts)
    python learn.py evidence    # each such success -> the Procedure's verified execution evidence

The verified patch is the agent's own (graded by the official harness); gold patches are never read.
Writes runs/goal_names.json, runs/manifest_train.json, runs/procedures_train.json.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

import swe_env
from generate import design, instances, load_jsonl

NAMES = swe_env.RUNS / "goal_names.json"
MANIFEST = swe_env.RUNS / "manifest_train.json"
PROCS = swe_env.RUNS / "procedures_train.json"
NAMING_SYSTEM = ("You name goals for a knowledge base of software changes. Given a GitHub issue from a repository, "
                 "write the goal of the change as ONE short imperative sentence (at most 16 words), specific to the "
                 "behaviour to fix or add, without issue numbers or user names. Reply with the sentence only.")


def resolved_train() -> dict[str, dict]:
    grades = json.loads((swe_env.RUNS / "grades_train_A0.json").read_text(encoding="utf-8"))
    return {r["instance_id"]: r for r in load_jsonl(swe_env.RUNS / "attempts_train_A0.jsonl")
            if not r.get("environmental_failure") and grades.get(r["instance_id"], {}).get("resolved")}


def name() -> None:
    from generate import client

    names = json.loads(NAMES.read_text(encoding="utf-8")) if NAMES.exists() else {}
    c, inst = client(), instances()
    for iid in design()["train"]:
        if iid in names:
            continue
        r = c.chat.completions.create(
            model=swe_env.CONFIG["model"]["id"], temperature=0, max_tokens=swe_env.CONFIG["query_writer"]["max_tokens"],
            messages=[{"role": "system", "content": NAMING_SYSTEM},
                      {"role": "user", "content": f"Repository: {inst[iid]['repo']}\n\n{inst[iid]['problem_statement'][:4000]}"}])
        text = re.sub(r"\s+", " ", (r.choices[0].message.content or "").strip().strip('"')).rstrip(".")
        if text:
            names[iid] = text
            NAMES.write_text(json.dumps(names, indent=1), encoding="utf-8")
            print(f"{iid:<45} {text}", flush=True)
    print(f"names: {len(names)} of {len(design()['train'])}")


async def do_import(pool) -> None:
    import hashlib

    from app.benchmarks.importer import import_tasks
    from app.benchmarks.tasks import BenchmarkTask
    from app.services.embeddings import Embedder

    names, inst = json.loads(NAMES.read_text(encoding="utf-8")), instances()
    tasks = []
    for iid in design()["train"]:
        r = inst[iid]
        f2p = r["FAIL_TO_PASS"] if isinstance(r["FAIL_TO_PASS"], list) else json.loads(r["FAIL_TO_PASS"] or "[]")
        tasks.append(BenchmarkTask(
            source="swebench_verified", source_version=str(swe_env.CONFIG["dataset"].get("revision") or "pinned"),
            external_id=iid, goal_name=f"{names[iid]} ({r['repo']})", goal_description=r["problem_statement"][:6000],
            domains=[], test_code="", test_names=list(f2p), visible_tests=[], entry_point="",
            libs=[r["repo"]], split="fit", reference_sha256=hashlib.sha256(iid.encode()).hexdigest(),
            extra={"repo": r["repo"], "base_commit": r["base_commit"]}))
    report = await import_tasks(pool, tasks, embedder=Embedder(rate_limit_pool=pool), judge_mode="model",
                                domain_edges=False)
    MANIFEST.write_text(json.dumps(report.pop("manifest"), indent=1), encoding="utf-8")
    report.pop("summary", None)
    print(json.dumps(report, indent=1, default=str))


async def worker(pool) -> None:
    from app.ingestion.config import WorkerConfig
    from app.ingestion.worker import Worker
    from app.services.shards import ShardPools

    print(await Worker(pool, WorkerConfig(concurrency=4), worker_id="swebench-experiment", pools=ShardPools(pool),
                       service=None, job_types=["goal_abstraction_placement", "goal_abstraction_audit",
                                                "benchmark_transfer"]).run(loop=False))


async def extract(pool) -> None:
    from openai import OpenAI

    from app.config import settings
    from app.services.procedure_extraction import extract_procedure
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource

    names, inst = json.loads(NAMES.read_text(encoding="utf-8")), instances()
    done = json.loads(PROCS.read_text(encoding="utf-8")) if PROCS.exists() else {}
    gc = OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url)
    wins = resolved_train()
    for attempt_no in range(1, 6):                 # the production queue's max_attempts
        for iid, run in sorted(wins.items()):
            if iid in done:
                continue
            source = AgentRunEvidenceSource(
                goal_text=f"{names[iid]} ({inst[iid]['repo']})", outcome="success",
                tool_sequence=[str(t).split("(")[0] for t in (run.get("tool_calls") or [])][:200],
                steps_used=run.get("steps"), observations=[
                    {"observation_type": "task_statement", "label": "issue",
                     "properties": {"text": inst[iid]["problem_statement"][:6000]}},
                    {"observation_type": "code_solution", "label": "verified patch",
                     "properties": {"code": run["patch"], "verified": True, "language": "diff",
                                    "verified_by": "SWE-bench FAIL_TO_PASS + PASS_TO_PASS (official harness)"}}])
            try:
                result = await extract_procedure(pool, source, client=gc, visibility="public")
            except Exception as exc:  # noqa: BLE001 -- transient: retried in the next pass
                print(f"{iid}: attempt {attempt_no} failed: {exc!r}"[:200])
                continue
            if result.procedure_id:
                done[iid] = {"procedure_id": str(result.procedure_id), "version_row_id": str(result.version_row_id),
                             "extracted_by": result.extracted_by}
                PROCS.write_text(json.dumps(done, indent=1), encoding="utf-8")
        print(f"pass {attempt_no}: procedures {len(done)} of {len(wins)} resolved train instances")


async def evidence(pool) -> None:
    from app.services.procedures import record_execution_outcome

    marker = swe_env.RUNS / "evidence_recorded.json"
    if marker.exists():
        print("evidence already recorded")
        return
    procs = json.loads(PROCS.read_text(encoding="utf-8"))
    for iid, p in procs.items():
        await record_execution_outcome(pool, procedure_row_id=p["version_row_id"], success=True,
                                       context_key=f"swebench:{iid}",
                                       success_criteria={"predicate": "SWE-bench FAIL_TO_PASS and PASS_TO_PASS pass"},
                                       execution_verified=True)
    marker.write_text(json.dumps({"recorded": len(procs)}), encoding="utf-8")
    print(f"evidence recorded: {len(procs)}")


async def main(action: str) -> None:
    from check_env import require_pinned

    require_pinned(scored=True)
    swe_env.verify_after_import()
    if action == "name":
        return name()
    from app.db.session import create_pool

    pool = await create_pool(swe_env.DSN, min_size=1, max_size=12)
    try:
        await {"import": do_import, "worker": worker, "extract": extract, "evidence": evidence}[action](pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
