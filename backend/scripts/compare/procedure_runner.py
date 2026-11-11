"""The procedure arm: run a task through the best active procedure for its Goal (docs/goal_router.md P2-02).

    python -m scripts.compare.procedure_runner --task web-001
    python -m scripts.compare.procedure_runner --task web-001 --procedure <uuid> --no-store

Execution is the existing step executor (app/execution/step_binding.execute_node); nothing is reimplemented here.
A step without a binding would run on the frontier model by default, which is not the procedure: the runner does not
run it, records the run as status "error" with error_class "unbound", and prints the unbound steps. No procedure for
the Goal is "error"/"no_procedure". Either way the arm is recorded, so it counts.

Caps: the task's budget_usd (summed from each step's reported cost) and a wall clock (--timeout-s). The answer is the
last step's output, scored by scripts/compare/scoring.py. The run is stored in task_runs (app/tool_routing/runs.py)
unless --no-store. Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import dotenv_values, load_dotenv

from scripts.compare.contracts import RunRecord, Task
from scripts.compare.corpus import load
from scripts.compare.scoring import score, sha

# The same environment the other scripts load: KELL_DEV / TOOL_CERT_SECRET / TOOL_GATE, so the
# comparison harness can issue a certificate and call the gate on this checkout.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

TOOL_GOAL_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")   # scripts/map_tool_cards_to_goals.py
STEP_TIMEOUT_S = 120.0


def dsn(arg: str) -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    return arg or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


def goal_uuid(key: str) -> str:
    return str(uuid.uuid5(TOOL_GOAL_NS, f"kell:tool-goal:{key}"))


async def resolve_goal(pool: Any, task: Task) -> tuple[Optional[str], Optional[str]]:
    """-> (goal key or uuid as recorded, Goal uuid). The task's goal_hint wins when the router also returns it."""
    from app.tool_routing.goal_router import route_goal

    hint = goal_uuid(task.goal_hint)
    hits = await route_goal(pool, task.prompt, limit=5, include_ways=False, include_tools=False)
    if any(h.goal_id == hint for h in hits) or not hits:
        return task.goal_hint, hint
    return hits[0].goal_id, hits[0].goal_id


async def pick_procedure(pool: Any, goal_id: Optional[str], wanted: Optional[str]) -> Optional[dict]:
    if wanted:
        row = await pool.fetchrow("SELECT id::text AS id, name, steps, domain_payload FROM procedures "
                                  "WHERE id = $1::uuid AND t_invalid IS NULL", wanted)
        return dict(row) if row else None
    if not goal_id:
        return None
    row = await pool.fetchrow(
        """
        SELECT id::text AS id, name, steps, domain_payload
        FROM procedures
        WHERE achieves_goal_id = $1::uuid AND t_invalid IS NULL AND availability = 'active'
        ORDER BY (verification_state::text = 'verified') DESC,
                 COALESCE((verification_stats->>'successes')::int, 0) DESC,
                 COALESCE((domain_payload->>'goal_score')::float, 0) DESC,
                 t_created DESC
        LIMIT 1
        """,
        goal_id,
    )
    return dict(row) if row else None


def nodes_for(steps: list) -> list[Any]:
    from app.models.plan import PlanNode

    out = []
    for i, step in enumerate(steps or []):
        s = step if isinstance(step, dict) else {"goal": str(step)}
        out.append(PlanNode(order=i, goal=str(s.get("goal") or s.get("action") or s.get("name") or f"step {i + 1}"),
                            binding=s.get("binding") or None, parameters=dict(s.get("parameters") or {}),
                            deps=[i - 1] if i else []))
    return out


def output_of(data: dict, notes: Optional[str]) -> Any:
    for key in ("answer", "output", "result", "text", "stdout"):
        if data.get(key) not in (None, ""):
            return data[key]
    return notes or ""


async def run_task(pool: Any, task: Task, *, procedure_id: Optional[str] = None, timeout_s: float = 600.0,
                   risk_ceiling: str = "R1", use_cert: bool = True,
                   store: bool = True) -> tuple[RunRecord, list[str]]:
    """-> (the RunRecord, step lines for the trace)."""
    # A comparison measured in shadow mode would count calls the gate would have refused; the tool arm
    # forces enforce for the same reason. An explicit TOOL_GATE still wins.
    os.environ.setdefault("TOOL_GATE", "enforce")
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    goal_key, goal_id = await resolve_goal(pool, task)
    proc = await pick_procedure(pool, goal_id, procedure_id)
    trace: list[str] = []
    status, error_class, answer, cost, tokens_in, tokens_out = "error", None, "", 0.0, 0, 0

    if proc is None:
        error_class = "no_procedure"
        trace.append(f"no active procedure for goal {goal_key}")
    else:
        nodes = nodes_for(proc["steps"] if isinstance(proc["steps"], list) else json.loads(proc["steps"] or "[]"))
        payload = proc.get("domain_payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload or "{}")
        one_tool = (len(nodes) == 1 and nodes[0].binding
                    and str(nodes[0].binding.get("kind") or "") in ("tool", "mcp_tool")
                    and payload.get("card_id"))
        if one_tool:
            # A tool card materialized as a one-step Way (scripts/materialize_tool_procedures.py).
            # Authorize it through the same gate, then dispatch through the same adapter/gateway path
            # as the tool arm: the step executor would call the MCP server directly and skip the gate.
            from app.tool_routing import certificates, gate
            from scripts.compare.tool_runner import OWNER, run_call

            binding = nodes[0].binding
            tool = str(binding.get("tool") or binding.get("mcp_tool") or "")
            card = await pool.fetchrow(
                "SELECT id, server, tools, extra FROM tool_cards WHERE id = $1", payload["card_id"])
            arguments = {"query": task.prompt}
            if card is None:
                error_class = "no_tool"
                trace.append(f"card {payload['card_id']} is gone")
            elif not tool:
                error_class = "no_tool"
                trace.append("step binding names no tool")
            else:
                cert_id = None
                if use_cert:
                    cert_id = await certificates.issue(
                        pool, task_id=task.id, owner_id=OWNER, goal_id=goal_id, agent="compare-harness",
                        allowed_servers=[card["server"]], allowed_tools=[tool],
                        data_class=task.data_class, risk_ceiling=risk_ceiling, max_calls=3, ttl_s=900,
                        issued_by="scripts/compare/procedure_runner")
                decision = await gate.authorize(pool, gate.ToolCall(
                    owner_id=OWNER, task_id=task.id, goal_id=goal_id, server=card["server"], tool=tool,
                    arguments=arguments, data_class=task.data_class, agent="compare-harness", cert_id=cert_id))
                trace.append(f"gate: {decision.effect} ({decision.reason}) risk {decision.risk}  "
                             f"{card['server']}/{tool}")
                if decision.effect != "allow":
                    error_class = "gate:denied"
                else:
                    result = await run_call(pool, dict(card), tool, arguments)
                    cost = float(result.get("cost_usd") or 0.0)
                    trace.append(f"call: {result.get('status')}")
                    if not result.get("ok"):
                        error_class = str(result.get("status") or "failed")
                    else:
                        answer = str(result.get("text") or "")
        else:
            unbound = [n for n in nodes if not n.binding]
            if not nodes:
                error_class = "empty_procedure"
                trace.append("procedure has no steps")
            elif unbound:
                error_class = "unbound"
                for n in nodes:
                    trace.append(f"step {n.order + 1}: {'unbound' if not n.binding else 'bound:' + str(n.binding.get('kind'))}"
                                 f"  {n.goal[:80]}")
            else:
                from app.execution.step_binding import execute_node

                context: dict[str, Any] = {"task": task.prompt, "inputs": list(task.input_refs), "previous": []}
                for n in nodes:
                    remaining = timeout_s - (time.monotonic() - t0)
                    if remaining <= 0:
                        error_class = "timeout"
                        break
                    try:
                        result = await asyncio.wait_for(execute_node(pool, n, context),
                                                        timeout=min(STEP_TIMEOUT_S, remaining))
                    except asyncio.TimeoutError:
                        error_class = "timeout"
                        trace.append(f"step {n.order + 1}: timeout")
                        break
                    except Exception as exc:  # noqa: BLE001 -- a crashing step is the arm's error, recorded
                        error_class = f"step_crash:{type(exc).__name__}"
                        trace.append(f"step {n.order + 1}: crashed {type(exc).__name__}")
                        break
                    data = dict(result.data or {})
                    cost += float(data.get("cost_usd") or 0.0)
                    tokens_in += int(data.get("tokens_in") or 0)
                    tokens_out += int(data.get("tokens_out") or 0)
                    trace.append(f"step {n.order + 1}: {result.status} via {n.binding.get('kind')}  {n.goal[:60]}")
                    if result.status != "success":
                        error_class = str(data.get("error_class") or "step_failed")
                        break
                    if cost > task.budget_usd:
                        error_class = "budget"
                        trace.append(f"budget {task.budget_usd} exceeded ({cost:.4f})")
                        break
                    answer = output_of(data, result.notes)
                    context["previous"].append(answer)
                else:
                    error_class = None

    sc = None
    if error_class is None:
        sc = await score(task, answer)
        status = sc.status
        error_class = None if sc.status != "error" else f"score:{sc.detail[:60]}"
    latency = int((time.monotonic() - t0) * 1000)
    record = RunRecord(run_id="", task_id=task.id, arm="procedure", goal_id=goal_key,
                       procedure_id=proc["id"] if proc else None, card_id=None, started_at=started.isoformat(),
                       latency_ms=latency, cost_usd=round(cost, 6), tokens_in=tokens_in, tokens_out=tokens_out,
                       status=status, score=sc.score if sc else 0.0, artifact_hash=sc.raw_hash if sc else None,
                       error_class=error_class, trace_ref=sha("\n".join(trace)) if trace else None)
    if store:
        from app.tool_routing.runs import finish_run, start_run

        record.run_id = await start_run(pool, task_id=task.id, arm="procedure", goal_id=goal_key,
                                        procedure_id=record.procedure_id, card_id=None)
        await finish_run(pool, record.run_id, {"status": record.status, "latency_ms": record.latency_ms,
                                               "cost_usd": record.cost_usd, "tokens_in": record.tokens_in,
                                               "tokens_out": record.tokens_out, "score": record.score,
                                               "artifact_hash": record.artifact_hash,
                                               "error_class": record.error_class, "trace_ref": record.trace_ref})
    return record, trace


async def main_async(args: argparse.Namespace) -> int:
    url = dsn(args.dsn)
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    task = load(ids=[args.task])[0]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2, timeout=30)
    try:
        record, trace = await run_task(pool, task, procedure_id=args.procedure, timeout_s=args.timeout_s,
                                       risk_ceiling=args.risk_ceiling, use_cert=not args.no_cert,
                                       store=not args.no_store)
    finally:
        await pool.close()
    for line in trace:
        print(line)
    print(json.dumps(record.to_dict(), indent=2))
    return 0 if record.status != "error" else 1


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ap = argparse.ArgumentParser(description="Run the procedure arm for one task.")
    ap.add_argument("--task", required=True)
    ap.add_argument("--procedure", help="a procedure id instead of the Goal's best")
    ap.add_argument("--timeout-s", type=float, default=600.0)
    ap.add_argument("--risk-ceiling", default="R1", help="the intent certificate's risk ceiling")
    ap.add_argument("--no-cert", action="store_true", help="ask the gate without an intent certificate")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
