"""Storing a submission: every step its own goal and one-step Way, kept out of retrieval until approved.

A step's goal is an existing public goal with the same name when there is one (so a step approved once is one more Way
for that goal), else a new goal private to the submitter. Each step's Way is written by the real writer
(`capture_procedure`) as `submitted`, quarantined and private: retrieval reads only active Ways, so it never returns
them. review.py publishes them when they pass.

A human step's Way is bound to `runtime: human`: the general plan executor has no executor for it and fails loudly, so
no model ever stands in for the person; app/submissions/runs.py is what pauses on it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from app.submissions.intake import Step, Submission, SubmissionError

PROVENANCE = "system_pending_review"
CREATED_BY = "goal_submission"
RUN_MODEL = "deepseek-v4.1-flash"


def _judge_mode() -> str:
    """The goal writer's own identity pass is exact-name only: matching.py has already decided (retrieve, rerank, judge)."""
    return "none"


def step_binding(step: Step) -> Optional[dict]:
    """How a step's Way is executed (services/source_locators.py binding)."""
    if step.kind == "human":
        return {"kind": "runtime", "runtime": "human"}
    params = {"prompt": step.prompt} if step.prompt else {}
    loc = step.locator or {}
    kind, value = loc.get("type"), loc.get("value")
    if kind == "url":
        return {"kind": "http_api", "http_api": "web_fetch", "endpoint": value, "parameters": params}
    if kind == "tool":
        return {"kind": "tool", "tool": value, "parameters": params}
    if kind == "script":
        return {"kind": "command", "command": value, "locator": {"uri": value}, "parameters": params}
    if kind == "file":
        return {"kind": "model", "model": RUN_MODEL, "locator": {"path": value}, "parameters": params}
    return {"kind": "model", "model": RUN_MODEL, "parameters": params}


def step_record(step: Step, submission_id: str) -> dict:
    """The one step of a step's Way, as procedures.steps stores it."""
    source = {"source_id": f"goal_submission:{submission_id}", "anchor": f"step-{step.order}", "granularity": "section"}
    if step.locator and step.locator["type"] == "url":
        source["uri"] = step.locator["value"]
    rec: dict[str, Any] = {"order": 1, "actor": "human" if step.kind == "human" else "ai", "title": step.title,
                           "source_locator": source, "binding": step_binding(step)}
    if step.description:
        rec["instruction"] = step.description
    if step.prompt:
        rec["prompt"] = step.prompt
    if step.locator:
        rec["locator"] = step.locator
    return rec


async def resolve_goal(pool: Any, name: str, *, owner_id: str, description: str = "") -> tuple[dict, bool, dict]:
    """-> (goal {id, canonical_name, home_shard_id}, publish, match). The public goal matching.py judges to be the same
    goal, else a new goal private to the submitter (published when its Way is approved). Global goal names are
    unique, so the same name already held by a goal nobody has had approved yet (this person's or someone else's) is
    that goal: `publish` is true for it too, and whichever Way is approved first makes it public (without the other
    person's description, see review._publish_goal)."""
    from app.services.goals import GoalQualityRejected, find_or_create_goal
    from app.submissions.matching import match_goal

    match = await match_goal(pool, name, description)
    if match["goal"] is not None:
        return match["goal"], False, _match_record(match)
    try:
        made = await find_or_create_goal(
            pool, canonical_name=name, scope_type="global", provenance=PROVENANCE, description=description or None,
            owner_id=owner_id, visibility="private", created_from="goal_submission", created_by=CREATED_BY,
            judge_mode="none", on_unavailable="create")
    except GoalQualityRejected as exc:
        raise SubmissionError(f"goal {name!r}: {exc}") from exc
    row = await pool.fetchrow("SELECT home_shard_id, visibility::text AS visibility FROM goals WHERE id = $1::uuid",
                              made["id"])
    return ({"id": str(made["id"]), "canonical_name": made.get("canonical_name") or name,
             "home_shard_id": row["home_shard_id"]},
            bool(made.get("created")) or row["visibility"] != "public", _match_record(match))


def _match_record(match: dict) -> dict:
    goal = match.get("goal") or {}
    out = {"decision": match["decision"], "goal": goal.get("canonical_name"), "reason": match.get("reason", ""),
           "candidates": match.get("candidates", [])[:8], "relations": match.get("relations", [])}
    if match.get("possible_duplicate"):
        out["possible_duplicate"] = match["possible_duplicate"]
    return out


async def write_step_way(pool: Any, submission_id: str, step: Step, *, owner_id: str) -> dict:
    """The step's goal and its quarantined one-step Way. -> {goal_id, created_goal, procedure_row_id}."""
    from app.services.goals import normalize_goal_name
    from app.services.procedures import capture_procedure

    goal, created, match = await resolve_goal(pool, step.title, owner_id=owner_id,
                                              description=step.description or step.prompt)
    mode = _judge_mode()
    cache = {("global", None, normalize_goal_name(step.title), mode): goal}   # the Way achieves exactly this goal
    out = await capture_procedure(
        pool, name=step.title, goal=step.title, steps=[step_record(step, submission_id)],
        provenance=PROVENANCE, created_by=CREATED_BY, owner_id=owner_id, visibility="private",
        scope_type="global", availability="quarantined", goal_cache=cache, judge_mode=mode,
        source_locator={"source_id": f"goal_submission:{submission_id}", "anchor": f"step-{step.order}"},
        display_description=step.description or step.prompt or None,
        domain_payload={"goal_submission": submission_id, "step_order": step.order, "actor": step.kind})
    await pool.execute("UPDATE procedures SET approval_status = 'submitted' WHERE id = $1::uuid", out["id"])
    return {"goal_id": goal["id"], "created_goal": created, "procedure_row_id": str(out["id"]), "goal_match": match}


async def submit(pool: Any, owner_id: str, sub: Submission) -> dict:
    """Store a parsed submission. -> {submission_id, mode, steps: [...]}. Nothing is reviewed here (review.py)."""
    if not owner_id:
        raise SubmissionError("sign in to kell to submit a goal")
    goal, created, match = await resolve_goal(pool, sub.goal, owner_id=owner_id, description=sub.description)
    submission_id = str(await pool.fetchval(
        """INSERT INTO goal_submissions (owner_id, goal_id, goal_name, description, created_goal, mode, goal_match)
           VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::jsonb) RETURNING id""",
        owner_id, goal["id"], sub.goal, sub.description, created, sub.mode, match))
    steps = []
    for step in sub.steps:
        way = await write_step_way(pool, submission_id, step, owner_id=owner_id)
        await pool.execute(
            """INSERT INTO goal_submission_steps (submission_id, step_order, kind, title, description, prompt, locator,
                                                  goal_id, created_goal, procedure_row_id, goal_match)
               VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8::uuid, $9, $10::uuid, $11::jsonb)""",
            submission_id, step.order, step.kind, step.title, step.description, step.prompt,
            step.locator, way["goal_id"], way["created_goal"],
            way["procedure_row_id"], way["goal_match"])
        steps.append({**step.as_dict(), **way, "status": "reviewing"})
    return {"submission_id": submission_id, "goal_id": goal["id"], "goal_match": match, "mode": sub.mode,
            "status": "reviewing",
            "steps": steps}


async def replace_step(pool: Any, submission_id: str, owner_id: str, order: int, step: Step) -> dict:
    """The submitter fixes one step: its old Way is retired, a new one written, and the step goes back to review.
    Steps already approved are left alone."""
    row = await pool.fetchrow(
        """SELECT s.id, s.status, s.procedure_row_id::text AS procedure_row_id, s.revision, s.goal_id::text AS goal_id
           FROM goal_submission_steps s JOIN goal_submissions g ON g.id = s.submission_id
           WHERE s.submission_id = $1::uuid AND s.step_order = $2 AND g.owner_id = $3""", submission_id, order, owner_id)
    if row is None:
        raise SubmissionError(f"no step {order} in a submission of yours with that id")
    if row["status"] in ("ai_approved", "approved"):
        raise SubmissionError(f"step {order} is already approved; only a rejected or waiting step can be changed")
    step.order = order
    way = await write_step_way(pool, submission_id, step, owner_id=owner_id)
    async with pool.acquire() as conn, conn.transaction():
        if row["procedure_row_id"]:
            await conn.execute("UPDATE procedures SET t_invalid = now(), approval_status = 'rejected' "
                               "WHERE id = $1::uuid AND t_invalid IS NULL", row["procedure_row_id"])
        await conn.execute(
            """UPDATE goal_submission_steps SET kind = $2, title = $3, description = $4, prompt = $5, locator = $6::jsonb,
                      goal_id = $7::uuid, created_goal = $8, procedure_row_id = $9::uuid, goal_match = $10::jsonb,
                      status = 'reviewing',
                      reason = '', checks = '{}', reviewer = '', reviewed_at = NULL, revision = revision + 1,
                      updated_at = now()
               WHERE id = $1""",
            row["id"], step.kind, step.title, step.description, step.prompt,
            step.locator, way["goal_id"], way["created_goal"],
            way["procedure_row_id"], way["goal_match"])
        if row["goal_id"] and row["goal_id"] != way["goal_id"]:
            await _retire_unused_goal(conn, row["goal_id"], owner_id)
        kinds = [r["kind"] for r in await conn.fetch(
            "SELECT kind FROM goal_submission_steps WHERE submission_id = $1::uuid", submission_id)]
        from app.submissions.intake import mode_of

        await conn.execute("UPDATE goal_submissions SET status = 'reviewing', mode = $2, updated_at = now() "
                           "WHERE id = $1::uuid", submission_id, mode_of(kinds))
    return {**step.as_dict(), **way, "status": "reviewing", "revision": row["revision"] + 1}


async def _retire_unused_goal(conn: Any, goal_id: str, owner_id: str) -> None:
    """A fixed step moved to another goal: the goal it left is retired when this person made it, it never went public,
    and nothing else uses it (no live Way, no other submission or step)."""
    await conn.execute(
        """UPDATE goals g SET t_invalid = now() WHERE g.id = $1::uuid AND g.t_invalid IS NULL
             AND g.visibility <> 'public' AND g.owner_id = $2 AND g.created_from = 'goal_submission'
             AND NOT EXISTS (SELECT 1 FROM procedures p WHERE p.achieves_goal_id = g.id AND p.t_invalid IS NULL)
             AND NOT EXISTS (SELECT 1 FROM goal_submission_steps s WHERE s.goal_id = g.id)
             AND NOT EXISTS (SELECT 1 FROM goal_submissions s WHERE s.goal_id = g.id)""",
        goal_id, owner_id)
    await conn.execute("UPDATE goal_search_index SET status = 'merged' WHERE goal_id = $1::uuid AND EXISTS "
                       "(SELECT 1 FROM goals WHERE id = $1::uuid AND t_invalid IS NOT NULL)", goal_id)


async def get(pool: Any, submission_id: str, *, owner_id: Optional[str] = None) -> Optional[dict]:
    """One submission and its steps (each with its state and the reason it was rejected). owner_id limits it to the
    submitter's own; None is the reviewer's view."""
    sub = await pool.fetchrow(
        """SELECT id::text AS id, owner_id, goal_id::text AS goal_id, goal_name, description, mode, status,
                  parent_procedure_id::text AS parent_procedure_id, goal_match, created_at, updated_at, notified_at
           FROM goal_submissions WHERE id = $1::uuid AND ($2::text IS NULL OR owner_id = $2)""",
        submission_id, owner_id)
    if sub is None:
        return None
    steps = await pool.fetch(
        """SELECT step_order AS "order", kind, title, description, prompt, locator, goal_id::text AS goal_id,
                  procedure_row_id::text AS procedure_row_id, goal_match, status, reason, reviewer, revision, reviewed_at
           FROM goal_submission_steps WHERE submission_id = $1::uuid ORDER BY step_order""", submission_id)
    out = _plain(dict(sub))
    out["mode_label"] = {"fully_human": "Fully Human", "human_ai": "Human+AI", "fully_ai": "Fully AI"}[out["mode"]]
    out["steps"] = [_plain(dict(s)) for s in steps]
    return out


async def list_for_owner(pool: Any, owner_id: str, limit: int = 20) -> list[dict]:
    rows = await pool.fetch(
        """SELECT g.id::text AS id, g.goal_name, g.mode, g.status, g.created_at,
                  count(s.*) AS steps, count(s.*) FILTER (WHERE s.status = 'rejected') AS rejected
           FROM goal_submissions g LEFT JOIN goal_submission_steps s ON s.submission_id = g.id
           WHERE g.owner_id = $1 GROUP BY g.id ORDER BY g.created_at DESC LIMIT $2""", owner_id, limit)
    return [_plain(dict(r)) for r in rows]


def _plain(row: dict) -> dict:
    for k, v in list(row.items()):
        if hasattr(v, "isoformat"):
            row[k] = v.isoformat()
        elif isinstance(v, str) and k == "locator":
            row[k] = json.loads(v)
    return row
