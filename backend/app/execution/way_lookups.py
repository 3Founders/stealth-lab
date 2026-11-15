"""When a step of a running Way looks something up, and what the lookup does (docs/way_executor.md, "Lookups").

The default is no lookup: a Way's step carries its own instructions. A step looks up once at most, and only when:

  goal only    the step names a goal but no Way (`subgoal_id`, or kind "subgoal"): it is resolved when reached.
  hint         JEV judged it, once per version of the Way (`hints`, cached in way_lookup_hints): a reasoning step
               that is a reusable task (triage) and is not the Way's own goal or broader (identity vote). Script,
               tool, human, plain-fetch steps and pinned sub-Ways are never judged: their binding is what they need.
  failed       the step failed: one lookup for a Way that does the step instead.
  needs        kell's model answered "NEEDS: <what>" (headless), or the session asked run_way(run_id, lookup=...).

JEV unavailable means no hint, so no lookup (the opposite of find_ways' triage, which fails toward the search): inside
a run the step already has the Way's text, and an unneeded lookup costs time and can change the plan.

A lookup is find_ways scoped to the step (its text, the Way's goal, what earlier steps produced). Its answer is used
automatically: a resolved Way other than the ones already in the run replaces the step with its own steps (paths
3.1, 3.2, ...; MAX_DEPTH still holds); otherwise what it found is attached to the step as know-how. Steps that came
from a lookup never look up again, so lookups cannot chain. Every lookup is recorded on its step.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

Lookup = Callable[[str], Awaitable[str]]        # a find_ways call: query -> its reply

TRIAGE_MIN = 0.6                 # a "reusable_task" verdict below this is no hint
HINT_TIMEOUT_S = 20.0            # all of a Way's judgments together; past it, no hints (and nothing cached)
KNOW_HOW_CHARS = 2500
NOT_A_SUBTASK = {"same", "possible_same", "generalizes"}   # a lookup would find this Way again (or a broader one)
JUDGED_KINDS = ("prompt", "fetch")

NEEDS_PREFIX = "NEEDS:"
NEEDS_RULE = (f"If you cannot do this step without a method you do not know, reply with only "
              f"\"{NEEDS_PREFIX} <what you need to know>\" and nothing else.")


def _judge() -> Any:
    from app.submissions.matching import jev_judge

    return jev_judge()


def step_text(step: dict) -> str:
    parts = [step.get("prompt") or step.get("title") or ""]
    if step.get("description") and step["description"] != parts[0]:
        parts.append(step["description"])
    return "\n".join(p for p in parts if p)


def root_goal(plan: list[dict]) -> str:
    top = next((s for s in plan if "." not in str(s.get("path", ""))), plan[0] if plan else {})
    return str(top.get("way_goal") or top.get("way_name") or "")


def judged(step: dict) -> bool:
    """Steps JEV is asked about: reasoning steps the Way wrote itself."""
    return (step["kind"] in JUDGED_KINDS and bool(step.get("prompt")) and not step.get("from_lookup")
            and not step.get("needs_way"))


def plan_hash(plan: list[dict]) -> str:
    keys = [(s.get("path"), s["kind"], step_text(s), s.get("way_goal")) for s in plan if judged(s)]
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:24]


# ------------------------------------------------------------------------------------------------------- hints

async def _hint(judge: Any, step: dict) -> Optional[dict]:
    """One step -> {lookup, kind, confidence, relation, why}, or None when JEV did not answer."""
    from app.submissions.matching import vote

    goal = step.get("way_goal") or step.get("way_name") or ""
    ctx = f"This is one step of the Way \"{step.get('way_name') or ''}\", which achieves: {goal}" if goal else ""
    r = await judge.judge_triage(step_text(step), ctx)
    if not (getattr(r, "ok", False) and isinstance(r.value, dict)):
        return None
    kind, conf = r.value.get("kind"), float(r.value.get("confidence") or 0)
    out = {"lookup": False, "kind": kind, "confidence": round(conf, 2)}
    if kind != "reusable_task" or conf < TRIAGE_MIN:
        out["why"] = "not a reusable task"
        return out
    if not goal:
        out.update(lookup=True, why="a reusable task")
        return out
    v = await vote(judge, step.get("title") or "", step_text(step), step.get("way_name") or goal, goal)
    if v is None:
        return None
    out["relation"] = v["relation"]
    if v["relation"] in NOT_A_SUBTASK:
        out["why"] = f"the step is {v['relation']} the Way's goal"
        return out
    out.update(lookup=True, why=f"a reusable task, {v['relation']} to the Way's goal")
    return out


async def hints(pool: Any, way_row_id: str, plan: list[dict], *, judge: Any = None) -> dict[str, dict]:
    """{step path: hint} for the plan, from the cache, or judged now and cached when every step was answered."""
    todo = [s for s in plan if judged(s)]
    if not todo:
        return {}
    key = plan_hash(plan)
    row = await pool.fetchrow("SELECT hints FROM way_lookup_hints WHERE procedure_row_id = $1::uuid AND plan_hash = $2",
                              way_row_id, key)
    if row is not None:
        got = row["hints"]
        return json.loads(got) if isinstance(got, str) else dict(got)
    judge = judge if judge is not None else _judge()
    if not getattr(judge, "providers", True):        # no JEV configured: no hints
        return {}
    try:
        answers = await asyncio.wait_for(
            asyncio.gather(*(_hint(judge, s) for s in todo), return_exceptions=True), HINT_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.info("way %s: lookup hints took over %gs; this run has none", way_row_id, HINT_TIMEOUT_S)
        return {}
    out = {str(s["path"]): a for s, a in zip(todo, answers) if isinstance(a, dict)}
    if len(out) == len(todo):
        await pool.execute(
            """INSERT INTO way_lookup_hints (procedure_row_id, plan_hash, hints, judge) VALUES ($1::uuid, $2, $3::jsonb, $4)
               ON CONFLICT (procedure_row_id, plan_hash) DO NOTHING""",
            way_row_id, key, json.dumps(out), str(getattr(judge, "chain_id", "") or ""))
    else:
        log.info("way %s: JEV answered %d of %d steps; not cached", way_row_id, len(out), len(todo))
    return out


def apply_hints(plan: list[dict], got: dict[str, dict]) -> None:
    for s in plan:
        h = got.get(str(s.get("path")))
        if h and h.get("lookup"):
            s["hint"] = h.get("why") or True


def due(step: dict) -> bool:
    """A step that looks up when the run reaches it (before it runs)."""
    return "lookup" not in step and not step.get("from_lookup") and bool(step.get("hint") or step.get("needs_way"))


def may_look_up(step: dict) -> bool:
    return "lookup" not in step and not step.get("from_lookup") and step["kind"] != "human"


# ---------------------------------------------------------------------------------------------- the session asks

async def asks_for_this_way(query: str, plan: list[dict], *, judge: Any = None) -> bool:
    """The session's lookup asks for what the run already does (the Way's own goal, or broader). JEV unavailable:
    False -- an explicit ask is a signal of its own, so it runs."""
    from app.submissions.matching import vote

    goal = root_goal(plan)
    if not goal:
        return False
    try:
        v = await vote(judge if judge is not None else _judge(), query, query, goal, goal)
    except Exception:  # noqa: BLE001
        return False
    return bool(v and v["relation"] in ("same", "generalizes"))


# ---------------------------------------------------------------------------------------------------- the lookup

def query_for(step: dict, run: dict, need: str = "") -> str:
    q = need.strip() or step_text(step)
    goal = step.get("way_goal") or ""
    if goal and goal not in q:
        q += f"\n\nThis is one step of a larger task: {goal}."
    if run.get("input"):
        q += f"\nThe task as the person gave it: {str(run['input'])[:400]}"
    return q


def _know_how(body: dict) -> str:
    """What a find_ways reply offers when its Way is not run: the chosen Way's steps, or the suggested one."""
    procs = body.get("procedures") if isinstance(body.get("procedures"), list) else []
    lines: list[str] = []
    if procs and isinstance(procs[0], dict):
        p = procs[0]
        lines.append(f"A known way: {p.get('name') or ''}. {p.get('what_it_does') or ''}".strip())
        for s in p.get("steps") or []:
            if isinstance(s, dict):
                lines.append(f"- {s.get('do') or s.get('title') or s.get('description') or ''}")
    elif isinstance(body.get("suggested"), dict):
        sug = body["suggested"]
        lines.append(f"A known way: {sug.get('goal') or sug.get('name') or ''}")
        vs = sug.get("verified_solution") or {}
        if isinstance(vs, dict) and vs.get("summary"):
            lines.append(str(vs["summary"]))
    text = "\n".join(line for line in lines if line.strip("- ").strip())
    return text[:KNOW_HOW_CHARS]


async def look_up(pool: Any, run: dict, idx: int, why: str, lookup: Lookup, *, need: str = "",
                  expand: Callable[..., Awaitable[list[dict]]], load_way: Callable[..., Awaitable[dict]],
                  max_depth: int) -> str:
    """Look up for plan[idx] and use the answer: "spliced" (the step is now another Way's steps), "know_how" (attached
    to the step), or "nothing". The lookup is recorded on the step either way; a step looks up once."""
    from app.services.untrusted import wrap_untrusted

    plan = run["plan"]
    step = plan[idx]
    q = query_for(step, run, need)
    rec: dict[str, Any] = {"why": why[:300], "query": q[:600], "outcome": None, "used": "nothing"}
    step["lookup"] = rec
    try:
        body = json.loads(await lookup(q))
    except Exception as exc:  # noqa: BLE001 -- a lookup never fails the run by itself
        rec["outcome"] = f"error: {type(exc).__name__}"
        log.info("way run %s: lookup for step %s failed (%s)", run["id"], step["order"], type(exc).__name__)
        return "nothing"
    if not isinstance(body, dict):
        rec["outcome"] = "not json"
        return "nothing"
    rec["outcome"] = body.get("outcome")
    procs = body.get("procedures") if isinstance(body.get("procedures"), list) else []
    found = procs[0] if body.get("outcome") in (None, "resolved") and procs and isinstance(procs[0], dict) else None
    way_id = str((found or {}).get("procedure_id") or (found or {}).get("id") or "")
    in_run = {str(s.get("way")) for s in plan} | {str(s.get("way_pid")) for s in plan} | {str(run.get("way_id"))}
    if way_id and way_id not in in_run:
        depth = str(step.get("path", "")).count(".") + 1
        try:
            if depth > max_depth:
                raise ValueError(f"nested more than {max_depth} deep")
            child = await load_way(pool, way_id, run["owner_id"])
            if str(child["id"]) in in_run or str(child["procedure_id"]) in in_run:
                raise ValueError("a Way already in this run")
            inner = await expand(pool, child, run["owner_id"], depth=depth, path=f"{step['path']}.",
                                 seen=tuple(x for x in in_run if x))
        except Exception as exc:  # noqa: BLE001 -- not runnable here: what it says is still know-how
            rec["not_spliced"] = str(exc)[:200]
        else:
            if inner:
                for s in inner:
                    s["from_lookup"] = True
                plan[idx:idx + 1] = inner
                for n, s in enumerate(plan, 1):
                    s["order"] = n
                inner[0]["lookup"] = {**rec, "used": "spliced", "way_id": way_id, "replaced": step["title"],
                                      "steps": len(inner)}
                return "spliced"
    kh = _know_how(body)
    if kh:
        step["know_how"] = wrap_untrusted(kh, label="kell:find_ways")
        rec["used"] = "know_how"
        if way_id:
            rec["way_id"] = way_id
        return "know_how"
    return "nothing"
