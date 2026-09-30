"""The OpenHands trajectory pipeline: one selected run -> trace, episode, extracted knowledge, evidence, routing.

Per item, in this order (the first gate that says no decides, and the ledger records which):

  1. ledger       -- an item already written or rejected is never redone; a failed one is retried
  2. held-out     -- the task id, or any task of a held-out repository               -> rejected held_out_*
  3. identity     -- this (task, outcome) already written by any trajectory corpus   -> rejected duplicate_identity
  4. parent       -- the task must exist in the pinned nebius/SWE-rebench             -> rejected parent_missing
  5. license      -- the task repository's license (GitHub name -> SPDX -> allowlist) -> rejected license_*
  6. normalize    -- the row must pair every action with its result                   -> rejected malformed
  7. write        -- ingestion context (license + credit), trace, one episode, semantic extraction:
                     resolved runs may produce Procedures, failed runs only Goals and Claims
  8. evidence     -- each Procedure of a resolved run gets the benchmark's grade as testimony
  9. routing      -- the run's model, scaffold and outcome, on the run's primary Goal (source public_import)

Network, model and database errors mark the item failed (retried next run). A spent budget stops the run and
leaves the item untouched.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.ingest.common.hf import PinnedFile, read_columns
from app.ingest.common.parallel import DEFAULT_CONCURRENCY, Stop, run_units
from app.ingest.common.ledger import FAILED, REJECTED, WRITTEN, ItemRef, Ledger
from app.ingest.common.licenses import decide, spdx_for_github_name
from app.ingest.openhands import normalize as nz
from app.ingest.openhands.select import Selected, selection

log = logging.getLogger(__name__)

PIPELINE = "openhands"
EXTRACTOR = f"ingest:{PIPELINE}"
EXTRACTOR_VERSION = "2026-09-29.1"

DATASET = PinnedFile("nebius/SWE-rebench-openhands-trajectories", "35455389ab51bf5e2306bfd436ef72d0f98bf882",
                     "trajectories.parquet")
PARENT_FILES = tuple(PinnedFile("nebius/SWE-rebench", "89cdfbab4ab1bd8f5a658bb212d1b63624f4f881", f)
                     for f in ("data/test-00000-of-00002.parquet", "data/test-00001-of-00002.parquet"))
DATASET_LICENSE = "CC-BY-4.0"
DATASET_CREDIT = ("SWE-rebench-OpenHands-Trajectories by Nebius (Trofimova et al., 2025), CC-BY-4.0 "
                  "(https://creativecommons.org/licenses/by/4.0/)")
REPO_ROOT = Path(__file__).resolve().parents[4]
# Every run is extracted with the STRONG trajectory model. Measured 2026-09-29 on the first local runs: the cheap
# model (gemma-4-31B-it) turned a resolved fix into five sub-Goals and no Procedure, while the strong model proposed
# a Procedure for each run; on General Compute the two cost about the same per token. A resolved run that still
# yields no Procedure is written with reason `written_without_procedure`, so the count is visible.
EXTRACTION_POLICY = "openhands_pipeline:strong_model_for_every_run"


def extraction_model() -> str:
    from app.ingest.common.llm import ingest_model

    return ingest_model()


CACHE_DIR = REPO_ROOT / "backend" / "data" / "ingest_cache"


@dataclass
class ParentTask:
    """The task a trajectory attempted, from the pinned parent nebius/SWE-rebench: its license, its accepted fix
    (the gold patch -- the verified solution served with the run's Procedure) and its tests (the Benchmark)."""
    license_name: Optional[str]
    base_commit: str = ""
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    docker_image: Optional[str] = None
    test_patch: str = ""
    patch: str = ""
    test_cmd: Optional[str] = None
    problem_statement: str = ""
    fail_to_fail: tuple[str, ...] = ()
    pass_to_fail: tuple[str, ...] = ()


@dataclass
class RunState:
    ledger: Ledger
    held: Any
    parents: dict[str, ParentTask]
    client: Any
    stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def load_parents() -> dict[str, ParentTask]:
    """instance_id -> the parent task facts the pipeline needs. Blocking."""
    out: dict[str, ParentTask] = {}
    for pinned in PARENT_FILES:
        for row in read_columns(pinned.local_path(), ["instance_id", "license_name", "base_commit", "FAIL_TO_PASS",
                                                     "PASS_TO_PASS", "docker_image", "test_patch", "patch",
                                                     "install_config", "problem_statement", "FAIL_TO_FAIL",
                                                     "PASS_TO_FAIL"]):
            install = row.get("install_config") or {}
            out[str(row["instance_id"])] = ParentTask(
                license_name=row.get("license_name"), base_commit=str(row.get("base_commit") or ""),
                fail_to_pass=tuple(row.get("FAIL_TO_PASS") or ()), pass_to_pass=tuple(row.get("PASS_TO_PASS") or ()),
                docker_image=row.get("docker_image"), test_patch=str(row.get("test_patch") or ""),
                patch=str(row.get("patch") or ""),
                test_cmd=(install.get("test_cmd") if isinstance(install, dict) else None) or None,
                problem_statement=str(row.get("problem_statement") or ""),
                fail_to_fail=tuple(row.get("FAIL_TO_FAIL") or ()), pass_to_fail=tuple(row.get("PASS_TO_FAIL") or ()))
    return out


def credit(item: Selected, repo_license: str) -> dict:
    return {
        "license": DATASET_LICENSE,
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "creator": "Nebius",
        "title": f"SWE-rebench-OpenHands-Trajectories run {item.trajectory_id}",
        "source_uri": DATASET.uri(item.trajectory_id),
        "changes": "converted into goals, claims and procedures",
        "repository": item.repo,
        "repository_license": repo_license,
        "notice": (f"Derived from an agent run on {item.repo} (code under {repo_license}) in "
                   f"{DATASET_CREDIT}; modified: converted into goals, claims and procedures."),
    }


def _ref(item: Selected) -> ItemRef:
    return ItemRef(item_key=item.item_key, source=DATASET.source_id, revision=DATASET.revision,
                   row_ref=item.trajectory_id, dedup_key=item.dedup_key)


async def gate(state: RunState, item: Selected) -> tuple[Optional[str], dict]:
    """(rejection reason or None, detail). Pure apart from the ledger read."""
    if state.held.is_held_out(item.instance_id):
        return "held_out_instance", {"instance_id": item.instance_id}
    held_repos = {r.lower() for r in state.held.scored_repos}
    if item.repo.lower() in held_repos:
        return "held_out_repo", {"repo": item.repo}
    owner = await state.ledger.identity_owner(item.dedup_key)
    if owner is not None:
        return "duplicate_identity", {"identity_owner": owner}
    parent = state.parents.get(item.instance_id)
    if parent is None:
        return "parent_missing", {"instance_id": item.instance_id}
    expression = spdx_for_github_name(parent.license_name)
    if expression is None:
        return "license_unmappable", {"license_name": parent.license_name}
    verdict = decide(expression, records_attribution=True)
    if not verdict.allowed:
        return f"license_{verdict.decision.lower()}", {"license_name": parent.license_name,
                                                       "expression": expression, "reason": verdict.reason}
    return None, {"license_name": parent.license_name, "expression": expression, "used_under": verdict.used_under,
                  "allowlist_version": verdict.allowlist_version}


async def _prior_extraction(pool: Any, episode_id: str) -> Optional[dict]:
    """A completed extraction of this episode from an earlier, interrupted attempt: reuse it, never extract twice."""
    row = await pool.fetchrow(
        "SELECT id FROM trajectory_extractions WHERE episode_id = $1::uuid AND status = 'completed' "
        "ORDER BY completed_at DESC LIMIT 1", episode_id)
    if row is None:
        return None
    objs = await pool.fetch(
        "SELECT object_type, object_id::text AS id FROM trajectory_extraction_objects WHERE extraction_id = $1",
        row["id"])
    return {"extraction_id": str(row["id"]), "reused": True,
            "goal_ids": [o["id"] for o in objs if o["object_type"] == "goal"],
            "claim_ids": [o["id"] for o in objs if o["object_type"] == "claim"],
            "procedure_ids": [o["id"] for o in objs if o["object_type"] == "procedure"]}


async def _run_outcome_claim(pool: Any, item: Selected, goal: Any, context_id: str) -> Optional[str]:
    """One measured Claim per task from the clean signal -- how often this model and scaffold solved it, as graded by
    the task's own tests (Singh et al.: comparisons from structured results, not prose). Natural language, with its
    conditions. Written once per task, whichever of its items comes first."""
    from app.services.claims import capture_claim

    exists = await pool.fetchval(
        "SELECT id::text FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL "
        "AND properties->>'task_key' = $1 AND properties->>'kind' = 'run_outcomes' LIMIT 1", f"swe:{item.instance_id}")
    if exists or not item.runs_total:
        return exists
    statement = (f"{nz.MODEL.split('/')[-1]} with {nz.PROVIDER_VERSION.replace('-', ' ')} solved \"{goal.canonical_name}\" "
                 f"in {item.runs_resolved} of {item.runs_total} recorded runs on {item.repo}, graded by the task's own "
                 "tests (SWE-rebench).")
    return await capture_claim(
        pool, statement=statement, task_ids=[], claim_type="measured_outcome", epistemic_status="observed",
        extraction_version=f"{EXTRACTOR}:{EXTRACTOR_VERSION}", created_by=EXTRACTOR,
        properties={"kind": "run_outcomes", "task_key": f"swe:{item.instance_id}", "goal_id": goal.goal_id,
                    "provenance": "third_party", "runs_total": item.runs_total, "runs_resolved": item.runs_resolved,
                    "model": nz.MODEL, "scaffold": nz.PROVIDER_VERSION},
        ingestion_context_id=context_id)


async def process(state: RunState, item: Selected, row: dict[str, Any],
                  license_detail: dict) -> tuple[str, str, dict, dict]:
    """Write one gated item. Returns (status, reason, detail, objects). Raises only BudgetExceeded."""
    from app.services.ingestion_context import complete_ingestion_context, open_ingestion_context
    from app.services.trace_worker import write_normalized_trajectory, write_trajectory_episodes
    from app.services.trajectory_semantics import extract_trajectory_semantics

    pool = state.ledger.pool
    try:
        trajectory = nz.normalize(row, dataset=DATASET.repo, revision=DATASET.revision)
    except nz.MalformedTrajectory as exc:
        return REJECTED, "malformed", {"defect": str(exc)}, {}

    resolved = item.outcome == "resolved"
    if bool(trajectory.metadata.get("resolved")) != resolved:
        return REJECTED, "outcome_mismatch", {"row_resolved": trajectory.metadata.get("resolved")}, {}
    attribution = credit(item, license_detail["used_under"])
    context_id = await open_ingestion_context(
        pool, source_type="agent_trajectory", source_uri=DATASET.uri(item.trajectory_id),
        source_hash=trajectory.metadata.get("model_patch_sha256"), extractor_id=EXTRACTOR,
        extractor_version=EXTRACTOR_VERSION, actor_id=EXTRACTOR, scope_type="global",
        license_spdx=DATASET_LICENSE, attribution=attribution)
    objects: dict[str, Any] = {"ingestion_context_id": context_id, "trace_id": trajectory.trace_id}
    import time as _time

    timings: dict[str, float] = {}      # seconds per stage, kept in the ledger (throughput diagnosis)
    mark = [_time.monotonic()]

    def lap(stage: str) -> None:
        now = _time.monotonic()
        timings[stage] = round(now - mark[0], 1)
        mark[0] = now

    try:
        written = await write_normalized_trajectory(pool, trajectory)
        await write_trajectory_episodes(pool, session_id=trajectory.session_id, trajectory=trajectory)
        episodes = await pool.fetch(
            "SELECT id::text AS id FROM episodes WHERE session_id = $1 AND parent_episode_id IS NULL "
            "ORDER BY start_ts", trajectory.session_id)
        if len(episodes) != 1:
            await complete_ingestion_context(pool, context_id, status="rejected")
            return REJECTED, "not_one_episode", {"episodes": len(episodes), "events": len(trajectory.events)}, objects
        episode_id = episodes[0]["id"]
        objects["episode_id"] = episode_id
        await pool.execute(
            "UPDATE episodes SET metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('declared_goal', $2::text) "
            "WHERE id = $1::uuid", episode_id, trajectory.metadata["declared_goal"])

        # K3: every source of this task meets on ONE Goal (migration 129). Named once from the issue.
        from app.ingest.common.tasks import ensure_task_goal, task_key

        model = extraction_model()
        parent = state.parents[item.instance_id]
        key = task_key(item.instance_id)
        goal = await ensure_task_goal(pool, key=key, issue=parent.problem_statement or trajectory.metadata["declared_goal"],
                                      client=state.client, model=model, named_by=EXTRACTOR, source=DATASET.source_id)
        objects["task_goal_id"] = goal.goal_id
        lap("store_and_task_goal")

        result = await _prior_extraction(pool, episode_id)
        if result is None:
            result = await extract_trajectory_semantics(
                pool, episode_id, client=state.client, model=model, escalated=True,
                escalation_reason=EXTRACTION_POLICY, ingestion_context_id=context_id,
                created_by=EXTRACTOR, write_procedures=resolved,
                task_goal={"id": goal.goal_id, "canonical_name": goal.canonical_name})
            procedure_rows = result.get("procedure_rows") or []
        else:
            procedure_rows = [dict(r) for r in await pool.fetch(
                "SELECT id::text AS id, procedure_id::text AS procedure_id FROM procedures "
                "WHERE procedure_id = ANY($1::uuid[])", result["procedure_ids"])]
        objects.update({"extraction_id": result["extraction_id"], "goal_ids": result.get("goal_ids") or [],
                        "claim_ids": result.get("claim_ids") or [],
                        "procedure_ids": [p["procedure_id"] for p in procedure_rows]})
        if not resolved and procedure_rows:
            raise RuntimeError("a failed run produced Procedures; the extractor ignored write_procedures=False")
        lap("extraction")
        timings.update({f"extraction_{k}": v for k, v in (result.get("timings_s") or {}).items()})

        # The task's tests as its frozen Benchmark (idempotent per task), and the benchmark grade as testimony.
        from app.ingest.common.benchmarks import TaskTests, ensure_task_benchmark
        from app.ingest.common.evidence import record_benchmark_support

        objects["benchmark_id"] = await ensure_task_benchmark(pool, goal_id=goal.goal_id, tests=TaskTests(
            task_key=key, source=f"hf:nebius/SWE-rebench@{PARENT_FILES[0].revision}", repo=item.repo,
            base_commit=parent.base_commit, docker_image=parent.docker_image, test_cmd=parent.test_cmd,
            fail_to_pass=parent.fail_to_pass, pass_to_pass=parent.pass_to_pass, test_patch=parent.test_patch,
            language="python", fail_to_fail=parent.fail_to_fail, pass_to_fail=parent.pass_to_fail))
        evidence_ids, solutions = [], 0
        for proc in procedure_rows:
            version = await pool.fetchval("SELECT version FROM procedures WHERE id = $1::uuid", proc["id"])
            evidence_ids.append(await record_benchmark_support(
                pool, procedure_row_id=proc["id"], target_version=int(version or 1), task_key=key, context_key=key,
                ingestion_context_id=context_id, created_by=EXTRACTOR, extractor_version=EXTRACTOR_VERSION))
            # The code served with this way is the maintainers' accepted fix, not the agent's patch.
            if parent.patch.strip():
                from app.services.verified_solutions import preserve

                ref = await preserve(pool, procedure_row_id=proc["id"], code=parent.patch,
                                     task=parent.problem_statement, language="diff",
                                     verified_by=("FAIL_TO_PASS: " + ", ".join(parent.fail_to_pass[:20]))[:300],
                                     locator=None, visibility="public")
                solutions += int(bool(ref))
        objects["evidence_ids"] = evidence_ids
        objects["verified_solutions"] = solutions
        objects["run_outcome_claim_id"] = await _run_outcome_claim(pool, item, goal, context_id)

        reason = "written" if (procedure_rows or not resolved) else "written_without_procedure"
        from app.routing.service import record_observation

        objects["routing_observation_id"] = await record_observation(pool, {
            "source": "public_import", "goal_id": goal.goal_id,
            "procedure_id": procedure_rows[0]["procedure_id"] if len(procedure_rows) == 1 else None,
            "model_key": nz.MODEL, "scaffold": nz.PROVIDER_VERSION, "instance_key": item.instance_id,
            "check_kind": "benchmark", "accepted": resolved, "gold_correct": resolved, "visibility": "public",
        })
        await complete_ingestion_context(pool, context_id, status="completed")
        lap("benchmark_solution_claim_routing")
        detail = {"events": len(trajectory.events), "events_inserted": written.get("inserted"), "timings_s": timings,
                  "model": model, "extraction_policy": EXTRACTION_POLICY,
                  "counts": {k: result.get(k) for k in ("goals", "claims", "failure_claims", "procedures",
                                                          "procedures_withheld") if k in result},
                  "extraction_reused": bool(result.get("reused")), "messages": item.messages}
        return WRITTEN, reason, detail, objects
    except Exception:
        await complete_ingestion_context(pool, context_id, status="failed")
        raise


async def run(pool: Any, *, ledger: Ledger, limit: Optional[int], instances: Optional[set[str]] = None,
              outcomes: tuple[str, ...] = ("resolved", "failed"), concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    """Process selected items row group by row group (each read once). Within a row group, the runs of one task stay
    in order (they share its Goal and Procedures) and different tasks run in parallel, up to `concurrency` at once."""
    from app.ingest.common.llm import extraction_client
    from app.services.ingest_budget import BudgetExceeded
    from app.services.ingestion_sources.held_out import load_held_out
    from app.utils.aio import run_blocking

    held = load_held_out(REPO_ROOT)
    path = await run_blocking(DATASET.local_path)
    items = await run_blocking(lambda: selection(path, revision=DATASET.revision, cache_dir=CACHE_DIR))
    items = [i for i in items if i.outcome in outcomes and (instances is None or i.instance_id in instances)]
    parents = await run_blocking(load_parents)
    state = RunState(ledger=ledger, held=held, parents=parents, client=extraction_client())

    settled = await ledger.settled_keys()
    todo: list[Selected] = []
    # File order: a limited run then takes whole row groups, whose items run in parallel -- in selection order the
    # first 48 items were spread over 15 row groups (1-7 each) and barely overlapped (2026-09-30 test).
    for item in sorted(items, key=lambda i: (i.row_group, i.row_index)):
        if item.item_key in settled:
            state.stats["skipped_already_decided"] += 1
            continue
        todo.append(item)
        if limit is not None and len(todo) >= limit:
            break

    by_group: dict[int, list[Selected]] = defaultdict(list)
    for item in todo:
        by_group[item.row_group].append(item)

    import pyarrow.parquet as pq

    stop = Stop()
    handle = await run_blocking(lambda: pq.ParquetFile(str(path)))
    for rg in sorted(by_group):
        if stop:
            break
        table = await run_blocking(handle.read_row_group, rg)

        async def one(item: Selected, table: Any = table, rg: int = rg) -> None:
            ref = _ref(item)
            reason, detail = await gate(state, item)
            if reason is not None:
                await ledger.record(ref, REJECTED, reason, detail=detail)
                state.stats[f"rejected:{reason}"] += 1
                return
            row = table.slice(item.row_index, 1).to_pylist()[0]
            if row.get("trajectory_id") != item.trajectory_id:
                raise RuntimeError(f"selection cache does not match the pinned file at {rg}/{item.row_index}")
            try:
                status, why, more, objects = await process(state, item, row, detail)
            except BudgetExceeded as exc:
                stop.set(f"budget: {exc}")
                return
            except Exception as exc:  # noqa: BLE001 -- infrastructure: retried by the next run
                if _connection_lost(exc):
                    raise                     # the CLI reconnects and resumes the run
                log.exception("openhands item %s failed", item.item_key)
                await ledger.record(ref, FAILED, type(exc).__name__, detail={**detail, "error": str(exc)[:2000]})
                state.stats[f"failed:{type(exc).__name__}"] += 1
                return
            stored = await ledger.record(ref, status, why, detail={**detail, **more}, objects=objects)
            state.stats[f"{stored}:{why if stored == status else 'duplicate_identity'}"] += 1

        units: dict[str, list[Selected]] = defaultdict(list)
        for item in sorted(by_group[rg], key=lambda i: i.row_index):
            units[item.instance_id].append(item)
        await run_units(list(units.values()), one, concurrency=concurrency, stop=stop)
        del table
    return {"selected_items": len(items), "attempted": len(todo), "stopped": stop.reason,
            "held_out": held.as_dict(), "stats": dict(state.stats), "concurrency": concurrency}


def _connection_lost(exc: BaseException) -> bool:
    """A dropped database/network connection is not this item's fault: let the run resume instead of marking it."""
    import asyncpg

    return isinstance(exc, (ConnectionError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError))
