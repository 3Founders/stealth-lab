"""The verified-solutions pipeline.

Per task, in order; the first gate that says no decides and the ledger records which:

  1. ledger     -- written/rejected items are never redone; failed ones are retried
  2. validity   -- instance id, repository, base commit, issue, gold patch, test patch, failing tests  -> missing_*
  3. quality    -- the gold patch breaks tests that passed before (PASS_TO_FAIL)                      -> gold_*
  4. held-out   -- the task id or any task of a held-out repository                                  -> held_out_*
  5. identity   -- this task's solution already written by any source (first source wins)            -> duplicate_identity
  6. license    -- the row's license, or GitHub's detection at the base commit when the row has none -> license_*
  7. write      -- the shared task Goal, one Procedure (steps with roles and checks, preconditions,
                   pitfalls) with the gold patch as its verified solution, facts and pitfalls as linked
                   Claims, the frozen Benchmark when the task is runnable, the dataset's grade as testimony
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from app.ingest.common.benchmarks import TaskTests, ensure_task_benchmark
from app.ingest.common.evidence import record_benchmark_support
from app.ingest.common.github import GitHub, GitHubUnavailable, RepoGone
from app.ingest.common.hf import iter_rows
from app.ingest.common.parallel import DEFAULT_CONCURRENCY, Stop, run_units
from app.ingest.common.ledger import FAILED, REJECTED, WRITTEN, ItemRef, Ledger
from app.ingest.common.licenses import decide, spdx_for_github_name
from app.ingest.common.tasks import TaskGoalUnavailable, ensure_task_goal, task_key
from app.ingest.verified import extract as ex
from app.ingest.verified.sources import GITHUB_LOOKUP, ORDER, SOURCES, Source, Task, columns_for, to_task

log = logging.getLogger(__name__)

PIPELINE = "verified"
EXTRACTOR = f"ingest:{PIPELINE}"
EXTRACTOR_VERSION = "2026-09-29.1"
REPO_ROOT = Path(__file__).resolve().parents[4]
DATASET_LICENSE_NOTE = "CC-BY-4.0 compilation"


def gate_row(task: Task, held: Any) -> Optional[tuple[str, dict]]:
    """Pure checks before any network or model call: (reason, detail) to reject, or None."""
    for field, value in (("instance_id", task.instance_id), ("repo", task.repo), ("base_commit", task.base_commit),
                         ("problem_statement", task.problem_statement), ("patch", task.patch),
                         ("test_patch", task.test_patch)):
        if not value.strip():
            return f"missing_{field}", {}
    if not task.fail_to_pass:
        return "missing_fail_to_pass", {}
    # FAIL_TO_FAIL is NOT a rejection: those tests fail before AND after the fix (network, credentials, missing
    # dependencies, already broken) and say nothing about it -- FAIL_TO_PASS proves the fix. Rejecting them dropped
    # 25% of SWE-rebench (5,242 tasks); the other sources do not even record the field. They are carried into the
    # Benchmark as known failures that grading ignores.
    if task.pass_to_fail:
        return "gold_patch_breaks_tests", {"tests": list(task.pass_to_fail[:10])}
    if held.is_held_out(task.instance_id):
        return "held_out_instance", {}
    if task.repo.lower() in {r.lower() for r in held.scored_repos}:
        return "held_out_repo", {}
    return None


def license_expression(task: Task, src: Source) -> tuple[Optional[str], str]:
    """(SPDX expression or None, how it was read). None with how="github" means: look it up at the base commit."""
    raw = (task.license_raw or "").strip()
    if src.license_kind == "github_name":
        return (spdx_for_github_name(raw), "row_github_name") if raw else (None, "github")
    if src.license_kind == "spdx" and raw and raw.lower() not in GITHUB_LOOKUP:
        return raw, "row_spdx"
    return None, "github"


def credit(task: Task, src: Source, spdx: str) -> dict:
    ds = src.files[0]
    return {
        "license": spdx, "creator": task.repo, "title": f"{task.instance_id} (issue and accepted fix)",
        "source_uri": f"https://github.com/{task.repo}/commit/{task.base_commit}",
        "commit": task.base_commit, "dataset": f"{ds.repo}@{ds.revision[:12]} ({DATASET_LICENSE_NOTE})",
        "changes": "summarised into goals, procedures and claims",
        "notice": (f"Fix for {task.instance_id} from {task.repo} ({spdx}), via {ds.repo} ({DATASET_LICENSE_NOTE}); "
                   "summarised into goals, procedures and claims."),
    }


async def write_task(pool: Any, *, task: Task, src: Source, spdx: str, how: str, client: Any, model: str,
                     run_id: str) -> tuple[str, str, dict, dict]:
    from app.services.claims import capture_claim
    from app.services.ingestion_context import complete_ingestion_context, open_ingestion_context
    from app.services.procedure_claim_refs import add_procedure_claim_ref
    from app.services.procedures import capture_procedure
    from app.services.verified_solutions import preserve

    from app.ingest.common.embedding import embed_written, ingest_embedder

    emb = ingest_embedder(pool)
    key = task_key(task.instance_id)
    goal = await ensure_task_goal(pool, key=key, issue=task.problem_statement, client=client, model=model,
                                  named_by=EXTRACTOR, source=src.source_id, embedder=emb)
    result = await ex.extract(client, model, goal=goal.canonical_name, repo=task.repo, language=task.language,
                              issue=task.problem_statement, hints=task.hints, patch=task.patch,
                              tests=task.fail_to_pass)
    ds = src.files[0]
    uri = f"hf://datasets/{ds.repo}@{ds.revision}#{task.instance_id}"
    attribution = credit(task, src, spdx)
    context_id = await open_ingestion_context(
        pool, source_type="verified_solution", source_uri=uri, extractor_id=EXTRACTOR,
        extractor_version=EXTRACTOR_VERSION, actor_id=EXTRACTOR, scope_type="global", run_ref=run_id,
        license_spdx=spdx, attribution=attribution)
    objects: dict[str, Any] = {"ingestion_context_id": context_id, "goal_id": goal.goal_id,
                               "goal_created": goal.created}
    try:
        locator = {"source_id": src.source_id, "uri": uri, "commit": task.base_commit, "granularity": "document"}
        steps = [{"order": i, "kind": "action", "do": s.do, "role": s.role, **({"check": s.check} if s.check else {}),
                  "source_locator": {**locator, "step_index": i}} for i, s in enumerate(result.steps)]
        proc = await capture_procedure(
            pool, name=result.name, goal=goal.canonical_name, steps=steps,
            preconditions=[{"source": "verified_solution", "description": p} for p in result.preconditions],
            failure_conditions=[{"source": "verified_solution", "description": p} for p in result.pitfalls],
            provenance="public_generated", created_by=EXTRACTOR, scope_type="global", source_locator=locator,
            ingestion_context_id=context_id, procedure_dedup=True, source_key=f"swe-solution:{task.instance_id}",
            goal_embedder=emb)
        proc_row, proc_id = str(proc["id"]), str(proc["procedure_id"])
        objects.update({"procedure_id": proc_id, "procedure_row_id": proc_row, "reused": bool(proc.get("reused"))})
        version = int(await pool.fetchval("SELECT version FROM procedures WHERE id = $1::uuid", proc_row) or 1)

        verified_by = "FAIL_TO_PASS: " + ", ".join(task.fail_to_pass[:20])
        ref = await preserve(pool, procedure_row_id=proc_row, code=task.patch, task=task.problem_statement,
                             language="diff", verified_by=verified_by[:300], locator=None, visibility="public")
        objects["verified_solution"] = bool(ref)

        tests = TaskTests(task_key=key, source=src.source_id, repo=task.repo, base_commit=task.base_commit,
                          docker_image=task.docker_image, test_cmd=task.test_cmd,
                          fail_to_pass=task.fail_to_pass, pass_to_pass=task.pass_to_pass,
                          test_patch=task.test_patch, language=task.language,
                          fail_to_fail=task.fail_to_fail, pass_to_fail=task.pass_to_fail)
        objects["benchmark_id"] = await ensure_task_benchmark(pool, goal_id=goal.goal_id, tests=tests)
        objects["evidence_id"] = await record_benchmark_support(
            pool, procedure_row_id=proc_row, target_version=version, task_key=key, context_key=key,
            ingestion_context_id=context_id, created_by=EXTRACTOR, extractor_version=EXTRACTOR_VERSION)

        claim_ids = []
        for statement, claim_type, role in ([(f, "verified_solution_fact", "RATIONALE") for f in result.facts]
                                            + [(p, "failure_mode", "FAILURE_MODE") for p in result.pitfalls]):
            cid = await capture_claim(
                pool, statement=statement, task_ids=[], claim_type=claim_type, epistemic_status="inferred",
                extraction_version=f"{EXTRACTOR}:{model}", created_by=EXTRACTOR,
                properties={"provenance": "third_party", "source": src.source_id, "task_key": key,
                            "goal_id": goal.goal_id},
                ingestion_context_id=context_id)
            if cid:
                await add_procedure_claim_ref(pool, procedure_id=proc_id, procedure_version=version, claim_id=cid,
                                              role=role, ref_origin="derived", extractor_version=EXTRACTOR_VERSION,
                                              ingestion_context_id=context_id, created_by=EXTRACTOR)
                claim_ids.append(str(cid))
        objects["claim_ids"] = claim_ids
        objects["embedded"] = await embed_written(pool, goal_ids=[goal.goal_id], procedure_row_ids=[proc_row])
        await complete_ingestion_context(pool, context_id, status="completed")
    except Exception:
        await complete_ingestion_context(pool, context_id, status="failed")
        raise
    detail = {"license": spdx, "license_read": how, "steps": len(result.steps), "facts": len(result.facts),
              "pitfalls": len(result.pitfalls), "runnable_benchmark": bool(objects.get("benchmark_id")),
              "quality": task.quality, "language": task.language, "model": model}
    reason = "written" if objects.get("benchmark_id") else "written_no_runnable_benchmark"
    return WRITTEN, reason, detail, objects


async def run(pool: Any, *, ledger: Ledger, limit: Optional[int], sources: tuple[str, ...] = ORDER,
              instances: Optional[set[str]] = None, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    """Sources in ORDER, one after another (the first source to reach a task writes it, so a source finishes before
    the next starts); the tasks of one source in parallel, up to `concurrency` at once (app/ingest/common/parallel)."""
    from app.config import settings
    from app.ingest.common.llm import extraction_client
    from app.services.ingest_budget import BudgetExceeded
    from app.services.ingestion_sources.held_out import load_held_out
    from app.utils.aio import run_blocking

    held = load_held_out(REPO_ROOT)
    client = extraction_client()
    from app.ingest.common.llm import ingest_model

    model = ingest_model()
    token = settings.github_token or settings.personal_github_token
    stats: dict[str, int] = defaultdict(int)
    attempted = 0
    stop = Stop()
    settled = await ledger.settled_keys()

    async with GitHub(token or "") as gh:

        async def one(item: tuple[Task, Source, Any]) -> None:
            task, src, pinned = item
            ref = ItemRef(task.item_key, src.source_id, pinned.revision, task.instance_id, task.dedup_key)
            rejected = gate_row(task, held)
            if rejected is None:
                owner = await ledger.identity_owner(task.dedup_key)
                if owner is not None:
                    rejected = ("duplicate_identity", {"identity_owner": owner})
            if rejected is not None:
                await ledger.record(ref, REJECTED, rejected[0], detail=rejected[1])
                stats[f"rejected:{rejected[0]}"] += 1
                return
            expression, how = license_expression(task, src)
            try:
                if expression is None and how == "github":
                    if not token:
                        raise GitHubUnavailable("no GitHub token: cannot read the license at the base commit")
                    owner_name = task.repo.split("/", 1)
                    expression = await gh.license_at(owner_name[0], owner_name[1], task.base_commit)
            except RepoGone:
                expression = None
            except GitHubUnavailable as exc:
                await ledger.record(ref, FAILED, "github_unavailable", detail={"error": str(exc)[:500]})
                stats["failed:github_unavailable"] += 1
                return
            verdict = decide(expression, records_attribution=True)
            if not verdict.allowed:
                reason = ("license_unmappable" if verdict.decision == "UNMAPPABLE"
                          else f"license_{verdict.decision.lower()}")
                await ledger.record(ref, REJECTED, reason, detail={"license_raw": task.license_raw,
                                                                    "expression": expression, "read": how,
                                                                    "why": verdict.reason})
                stats[f"rejected:{reason}"] += 1
                return
            try:
                status, why, detail, objects = await write_task(
                    pool, task=task, src=src, spdx=verdict.used_under or expression, how=how,
                    client=client, model=model, run_id=ledger.run_id)
            except BudgetExceeded as exc:
                stop.set(f"budget: {exc}")
                return
            except (ex.ExtractionFailed, TaskGoalUnavailable) as exc:
                await ledger.record(ref, FAILED, type(exc).__name__, detail={"error": str(exc)[:1500]})
                stats[f"failed:{type(exc).__name__}"] += 1
                return
            except Exception as exc:  # noqa: BLE001 -- infrastructure: retried next run
                if _connection_lost(exc):
                    raise                     # the CLI reconnects and resumes the run
                log.exception("verified item %s failed", task.item_key)
                await ledger.record(ref, FAILED, type(exc).__name__, detail={"error": str(exc)[:1500]})
                stats[f"failed:{type(exc).__name__}"] += 1
                return
            stored = await ledger.record(ref, status, why, detail=detail, objects=objects)
            stats[f"{stored}:{why if stored == status else 'duplicate_identity'}"] += 1

        for key in [s for s in ORDER if s in sources]:
            src = SOURCES[key]
            for pinned in src.files:
                if stop or (limit is not None and attempted >= limit):
                    break
                path = await run_blocking(pinned.local_path)
                cols = await run_blocking(columns_for, path)
                rows = await run_blocking(lambda: list(iter_rows(path, cols)))
                todo: list[tuple[Task, Source, Any]] = []
                for row in rows:
                    task = to_task(src, row)
                    if instances is not None and task.instance_id not in instances:
                        continue
                    if task.item_key in settled:
                        stats["skipped_already_decided"] += 1
                        continue
                    if limit is not None and attempted + len(todo) >= limit:
                        break
                    todo.append((task, src, pinned))
                attempted += len(todo)
                await run_units([[t] for t in todo], one, concurrency=concurrency, stop=stop)
            if stop or (limit is not None and attempted >= limit):
                break
    return {"attempted": attempted, "stopped": stop.reason, "sources": list(sources), "held_out": held.as_dict(),
            "github": gh.stats.as_dict(), "stats": dict(stats), "concurrency": concurrency}


def _connection_lost(exc: BaseException) -> bool:
    """A dropped database/network connection is not this item's fault: let the run resume instead of marking it."""
    import asyncpg

    return isinstance(exc, (ConnectionError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError))
