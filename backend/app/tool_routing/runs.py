"""The run record both comparison arms write (db/191_task_runs.sql, docs/goal_router.md P1-10).

One row per (task, arm, attempt). An arm starts a run, does its work, and finishes it with the measured fields; a
refused or broken call is finished with status "error" and an error class, so it still counts against the arm.

    run_id = await start_run(pool, task_id="web-001", arm="tool", goal_id="web", procedure_id=None,
                             card_id="registry:ai.exa/exa")
    await finish_run(pool, run_id, {"status": "pass", "latency_ms": 840, "cost_usd": 0.001, "score": 1.0})

Only hashes of outputs are stored (artifact_hash), never the prompt, arguments or output.
"""
from __future__ import annotations

from typing import Any, Optional

ARMS = ("procedure", "tool", "baseline")
STATUSES = ("pass", "fail", "error")
# the fields finish_run may set; anything else in the record is ignored, so a caller's extra keys never reach SQL
FIELDS = ("latency_ms", "cost_usd", "tokens_in", "tokens_out", "score", "artifact_hash", "error_class", "trace_ref",
          "goal_id", "procedure_id", "card_id")


class RunError(ValueError):
    pass


async def start_run(pool: Any, *, task_id: str, arm: str, goal_id: Optional[str],
                    procedure_id: Optional[str], card_id: Optional[str]) -> str:
    """Open a run as the next attempt of (task, arm); -> run_id."""
    task_id = str(task_id or "").strip()
    if not task_id:
        raise RunError("task_id is required")
    if arm not in ARMS:
        raise RunError(f"arm must be one of {ARMS}, got {arm!r}")
    for _ in range(5):   # two runs of one (task, arm) starting together: the loser takes the next attempt number
        try:
            row = await pool.fetchrow(
                """
                INSERT INTO task_runs (task_id, arm, attempt, goal_id, procedure_id, card_id)
                SELECT $1, $2, COALESCE(MAX(attempt), 0) + 1, $3, $4, $5
                FROM task_runs WHERE task_id = $1 AND arm = $2
                RETURNING run_id::text
                """,
                task_id, arm, goal_id, procedure_id, card_id,
            )
            return str(row["run_id"])
        except Exception as exc:  # noqa: BLE001 -- asyncpg.UniqueViolationError, without importing asyncpg here
            if type(exc).__name__ != "UniqueViolationError":
                raise
    raise RunError(f"could not allocate an attempt for {task_id}/{arm}")


async def finish_run(pool: Any, run_id: str, record: dict) -> None:
    """Close a run. `record["status"]` is pass | fail | error (missing or unknown is recorded as error, never pass); an
    error without an error_class gets "unknown". A run is finished once: finishing it again raises."""
    status = str(record.get("status") or "")
    if status not in STATUSES:
        status = "error"
    values = {k: record.get(k) for k in FIELDS if record.get(k) is not None}
    if status == "error" and not values.get("error_class"):
        values["error_class"] = "unknown"
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(values, start=3))
    done = await pool.fetchval(
        f"""
        UPDATE task_runs
        SET status = $2, finished_at = now(){", " + sets if sets else ""}
        WHERE run_id = $1::uuid AND status = 'running'
        RETURNING run_id::text
        """,
        run_id, status, *values.values(),
    )
    if done is None:
        raise RunError(f"run {run_id} is unknown or already finished")


async def record_refusal(pool: Any, *, task_id: str, arm: str, goal_id: Optional[str], card_id: Optional[str],
                         error_class: str, latency_ms: Optional[int] = None) -> str:
    """A call the gate refused, as one finished run (status error)."""
    run_id = await start_run(pool, task_id=task_id, arm=arm, goal_id=goal_id, procedure_id=None, card_id=card_id)
    await finish_run(pool, run_id, {"status": "error", "error_class": error_class, "latency_ms": latency_ms,
                                    "cost_usd": 0})
    return run_id


async def list_runs(pool: Any, *, task_id: Optional[str] = None, goal_id: Optional[str] = None,
                    arm: Optional[str] = None, since_s: Optional[int] = None, limit: int = 50) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT run_id::text, task_id, arm, attempt, goal_id, procedure_id, card_id, started_at, finished_at,
               latency_ms, cost_usd::float8 AS cost_usd, tokens_in, tokens_out, status, score, artifact_hash,
               error_class, trace_ref
        FROM task_runs
        WHERE ($1::text IS NULL OR task_id = $1)
          AND ($2::text IS NULL OR goal_id = $2)
          AND ($3::text IS NULL OR arm = $3)
          AND ($4::int IS NULL OR started_at >= now() - make_interval(secs => $4))
        ORDER BY started_at DESC
        LIMIT $5
        """,
        task_id, goal_id, arm, since_s, max(1, min(int(limit), 1000)),
    )
    return [dict(r) for r in rows]
