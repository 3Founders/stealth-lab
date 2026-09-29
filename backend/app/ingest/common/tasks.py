"""One Goal per benchmark task, shared by every pipeline (migration 129).

`ensure_task_goal` returns the task's Goal, naming and creating it on first use. The name is the reusable outcome the
issue asks for, written by the model in one call, and it must pass the core Goal quality gate (no file paths, repo
names, code or commands). A rejected name gets exactly one correction round with the gate's reason; after that the
caller's item fails with the reason -- no name is invented by code.

A transaction-level advisory lock on the task key makes two pipelines reaching the same task at once agree on one Goal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

NAMING_OP = "task_goal_naming"
MAX_ISSUE_CHARS = 6000

_PROMPT = """You name the GOAL of a software task so other developers with the same need can find it.

Given a GitHub issue, reply with ONE JSON object: {"goal": "..."}.

The goal is one short imperative sentence (4-14 words) stating the OUTCOME the fix delivers, in terms a developer
would search for: what must work, for which kind of component or behaviour. Rules:
- no file paths, no repository or project names, no issue numbers, no backticks or code, no shell commands;
- name the behaviour, not the edit ("Make relative path conversion return POSIX-style paths", not "Change line 212");
- keep the domain words that make it findable (e.g. "date picker", "translation keys", "HTTP retry").
"""


class TaskGoalUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskGoal:
    goal_id: str
    canonical_name: str
    created: bool


def task_key(instance_id: str) -> str:
    return f"swe:{instance_id}"


async def _name(client: Any, model: str, issue: str, correction: Optional[str]) -> str:
    from app.services import ingest_budget
    from app.services.llm_json import parse_json_object
    from app.utils.aio import run_blocking

    messages = [{"role": "system", "content": _PROMPT},
                {"role": "user", "content": f"Issue:\n{issue[:MAX_ISSUE_CHARS]}"}]
    if correction:
        messages.append({"role": "user", "content": f"Your previous goal was rejected: {correction}. Write it again."})
    await ingest_budget.guard(NAMING_OP)
    resp = await run_blocking(client.chat.completions.create, model=model, messages=messages, temperature=0,
                              max_tokens=200)
    await ingest_budget.record_completion(model, NAMING_OP, getattr(resp, "usage", None))
    parsed = parse_json_object((resp.choices[0].message.content or "").strip()) or {}
    return str(parsed.get("goal") or "").strip().rstrip(".")


async def _registered(conn: Any, key: str) -> Optional[TaskGoal]:
    """The task's registered Goal, followed through merges: identity reconciliation can merge a task Goal into an
    older duplicate, and a Procedure written to a merged Goal would never be found."""
    row = await conn.fetchrow(
        "WITH RECURSIVE chain(id, depth) AS ("
        "  SELECT goal_id, 0 FROM ingest_task_goals WHERE task_key = $1"
        "  UNION ALL SELECT g.merged_into_id, c.depth + 1 FROM chain c JOIN goals g ON g.id = c.id"
        "  WHERE g.status = 'merged' AND g.merged_into_id IS NOT NULL AND c.depth < 20) "
        "SELECT g.id::text AS goal_id, g.canonical_name FROM chain c JOIN goals g ON g.id = c.id "
        "ORDER BY c.depth DESC LIMIT 1", key)
    return TaskGoal(row["goal_id"], row["canonical_name"], False) if row else None


async def ensure_task_goal(pool: Any, *, key: str, issue: str, client: Any, model: str, named_by: str,
                           source: str, provenance: str = "system_pending_review") -> TaskGoal:
    from app.services.goals import describe_goal_quality_issue, find_or_create_goal

    existing = await _registered(pool, key)
    if existing:
        return existing

    name, problem = "", None
    for correction in (None, "first"):
        name = await _name(client, model, issue, problem if correction else None)
        problem = "empty" if not name else describe_goal_quality_issue(name)
        if problem is None:
            break
    if problem is not None:
        raise TaskGoalUnavailable(f"goal name rejected twice: {problem} ({name!r})")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ingest_task_goal:{key}")
            again = await _registered(conn, key)
            if again:
                return again
            goal = await find_or_create_goal(
                pool, canonical_name=name, scope_type="global", provenance=provenance,
                description=issue[:2000], created_from="benchmark_task", created_by=named_by,
                metadata={"task_key": key, "source": source})
            await conn.execute(
                "INSERT INTO ingest_task_goals (task_key, goal_id, canonical_name, named_by, source) "
                "VALUES ($1, $2::uuid, $3, $4, $5) ON CONFLICT (task_key) DO NOTHING",
                key, goal["id"], goal["canonical_name"], named_by, source)
    row = await pool.fetchrow("SELECT goal_id::text AS goal_id, canonical_name FROM ingest_task_goals "
                              "WHERE task_key = $1", key)
    return TaskGoal(row["goal_id"], row["canonical_name"], True)

