"""
What a person or an agent adds while working a goal: a tool, a script, a better prompt, or a tip.

One function, two callers, so a suggestion lands in the database the same way whoever makes it:
  * the MCP tool `submit` (an agent in the person's own session, signed in through the connector), and
  * the website route `POST /v1/contributions` (the person typing it in; app/mcp_server/contribution_routes.py).

Stored as a PRIVATE candidate claim owned by the submitter and linked to the goal, exactly as `report_discovery` stores a fix.
Nothing is published, verified or paid here (v2). An automated screen rejects malicious or NSFW text and any link, and
fails closed: if the screen cannot run, nothing is stored. Free text goes through the trace redaction chokepoint first.

The two callers pass in the pieces that live in the MCP server module (finding or creating the goal, resolving a procedure), so
this module imports nothing from it.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional

KINDS = frozenset({"tool", "script", "prompt", "tip"})
SUGGESTED_BY = ("user", "agent")
TEXT_MAX = 4000
TITLE_MAX = 200
PATH = "mcp/submit"


def refuse(message: str) -> str:
    return f"REFUSED: {message}"


def validate(kind: Any, title: Any, content: Any, why: Any, suggested_by: Any, goal_id: Any, goal: Any,
             goal_objective: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """The cleaned fields, or a refusal sentence. No I/O."""
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        return None, f"kind must be one of {sorted(KINDS)}"
    if suggested_by not in SUGGESTED_BY:
        return None, 'suggested_by must be "user" or "agent"'
    title, content, why = (str(title or "").strip(), str(content or "").strip(), str(why or "").strip())
    if not title or not content or not why:
        return None, "title, content and why must all be non-empty"
    if len(title) > TITLE_MAX or len(content) > TEXT_MAX or len(why) > TEXT_MAX:
        return None, f"title is at most {TITLE_MAX} characters, content and why at most {TEXT_MAX}"
    goal = str(goal or "").strip() or None
    goal_id = str(goal_id).strip() if goal_id else None
    if bool(goal_id) == bool(goal):
        return None, "name the goal with exactly one of goal_id (from find_ways) or goal (plain words)"
    objective = str(goal_objective or "").strip() or None
    if goal and not objective:
        return None, "a goal named in words needs goal_objective -- what counts as done"
    if goal and (len(goal) > TITLE_MAX or len(objective or "") > TEXT_MAX):
        return None, f"goal ({TITLE_MAX}) or goal_objective ({TEXT_MAX}) is too long"
    if goal_id and len(goal_id) > 80:
        return None, "goal_id is too long"
    return {"kind": kind, "title": title, "content": content, "why": why, "suggested_by": suggested_by,
            "goal_id": goal_id, "goal": goal, "goal_objective": objective}, None


async def submit_contribution(
    pool: Any, scope: Any, *, kind: Any, title: Any, content: Any, why: Any,
    find_or_create_goal: Callable[..., Awaitable[Any]], resolve_procedure: Callable[..., Awaitable[Any]],
    created_by: str, goal_id: Any = None, goal: Any = None, goal_objective: Any = None,
    procedure_id: Optional[str] = None, step_order: Optional[int] = None, suggested_by: Any = "user",
    repo: Optional[str] = None,
) -> "dict[str, Any] | str":
    """-> a dict ({"outcome": "stored" | "rejected_by_screen" | "goal_ambiguous", ...}) or a "REFUSED: ..." string."""
    from app.economy import constants as econ
    from app.economy.content_screen import screen_contribution
    from app.services.applicability import ProcedureNotFound
    from app.services.claims import capture_claim
    from app.services.governance import RateLimit, RateLimiter, RateLimitExceeded
    from app.services.sources import register_source
    from app.services.trace_redaction import redact_value

    fields, why_not = validate(kind, title, content, why, suggested_by, goal_id, goal, goal_objective)
    if why_not:
        return refuse(why_not)
    if not getattr(scope, "viewer_id", None):
        return refuse("sign in to contribute -- this needs a real user identity")
    kind, title, content, why = fields["kind"], fields["title"], fields["content"], fields["why"]
    suggested_by, goal_id, goal, goal_objective = fields["suggested_by"], fields["goal_id"], fields["goal"], fields["goal_objective"]

    limiter = RateLimiter(pool, limits={
        PATH: RateLimit(max_requests=20, window=timedelta(hours=1)),
        PATH + "/day": RateLimit(max_requests=60, window=timedelta(days=1)),
        PATH + "/all": RateLimit(max_requests=econ.SUBMISSION_GLOBAL_HOURLY_MAX, window=timedelta(hours=1)),
    })
    try:
        await limiter.check_and_record(f"user:{scope.viewer_id}", PATH)
        await limiter.check_and_record(f"user:{scope.viewer_id}", PATH + "/day")
        await limiter.check_and_record("global:submit", PATH + "/all")      # bounds screening spend however many accounts exist
    except RateLimitExceeded as exc:
        return refuse(f"rate limit -- retry in {exc.retry_after_seconds}s ({exc})")

    verdict = await screen_contribution({"goal": goal, "goal_objective": goal_objective, "name": title, "rationale": why,
                                         "steps": [content], "preconditions": [], "expected_outcome": {}})
    if not verdict.allowed:
        return {"outcome": "rejected_by_screen", "screen": verdict.as_dict(),
                "next": "Nothing was stored. Remove any links and anything flagged, then submit again."}

    goal_info: dict[str, Any] = {"goal_id": goal_id, "created": False}
    if goal:
        resolved = await find_or_create_goal(pool, scope=scope, goal=goal, objective=goal_objective or "", rationale=why)
        if isinstance(resolved, str):
            return resolved
        if resolved.get("outcome") == "goal_ambiguous":
            return resolved
        goal_info = resolved
        goal_id = resolved["goal_id"]

    stable_pid = None
    if procedure_id:
        try:
            stable_pid = str((await resolve_procedure(pool, procedure_id, access_scope=scope))["procedure_id"])
        except ProcedureNotFound as exc:
            return refuse(str(exc))

    matched: list[str] = []
    title_r = redact_value(title, matched)
    content_r = redact_value(content, matched)
    why_r = redact_value(why, matched)
    src = await register_source(
        pool, source_type="agent_execution", locator=f"stealth-submit:{goal_id}:{scope.viewer_id}",
        provenance="company_ingested", created_by=created_by,
        visibility="private", owner_id=scope.viewer_id, scope_type="global", scope_entity_id=None,
    )
    claim_id = await capture_claim(
        pool,
        statement=f"[{kind} suggested by {suggested_by}] {title_r}: {content_r} -- {why_r}",
        task_ids=[], source_ref=src["id"],
        subject=f"goal:{goal_id}" + (f"#step{step_order}" if step_order is not None else ""),
        predicate=f"suggested_{kind}", object=title_r,
        properties={"source": "submit", "submit_kind": kind, "suggested_by": suggested_by, "title": title_r,
                    "content": content_r, "why": why_r, "goal_id": goal_id, "procedure_id": stable_pid,
                    "step_order": step_order, "share": False, "redacted_patterns": sorted(set(matched))},
        created_by=created_by, owner_id=scope.viewer_id, visibility="private",
        scope_type="repository" if repo else "global", scope_entity_id=repo,
    )
    if claim_id is None:
        return refuse("the claim store declined this submission (provenance anchor rule)")
    return {"outcome": "stored", "claim_id": claim_id, "visibility": "private", "status": "candidate", "kind": kind,
            "goal": goal_info, "redacted": bool(matched),
            "next": "kept for you with this goal; sharing, verification and credits are v2"}


def as_tool_reply(result: "dict[str, Any] | str") -> str:
    return result if isinstance(result, str) else json.dumps(result, default=str)
