"""The tool arm: run a task through the top tool mapped to its Goal, via the real gate (docs/goal_router.md P2-03).

    python -m scripts.compare.tool_runner --task web-001
    python -m scripts.compare.tool_runner --task sec-001 --no-cert
    python -m scripts.compare.tool_runner --task web-001 --card registry:ai.exa/exa --no-store

The harness is the trusted caller the plan allows to mint an intent certificate (app/tool_routing/certificates.issue):
one per attempt, scoped to this task, owner, Goal, server and tool, public data, a low risk ceiling (--risk-ceiling,
default R1) and a short life. Then app/tool_routing/gate.authorize() decides, exactly as for use_tool. A refusal is a
recorded run (status "error", error_class "gate:denied"), never an exception and never a call. Only an "allow" runs:
a provider adapter (app/tool_providers, e.g. Exa on kell's key) when one claims the server, else the card's MCP
connector through app/tool_routing/gateway. The tool's output is scored by scripts/compare/scoring.py.

Gate mode: TOOL_GATE as set; unset, this runner uses "enforce" (a comparison measured in shadow mode would count
calls the gate would have refused). Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.compare.contracts import RunRecord, Task
from scripts.compare.corpus import load
from scripts.compare.procedure_runner import dsn, resolve_goal
from scripts.compare.scoring import score, sha

OWNER = os.environ.get("KELL_COMPARE_OWNER", "compare-harness")
CALL_TIMEOUT_S = 90.0
# the tool a provider adapter runs for a corpus task, by the task's goal; else the card's first listed tool
ADAPTER_TOOL = {"web": "web_search", "docs": "web_search", "code_search": "web_search", "packages": "web_search",
                "security": "web_search", "database": "web_search"}


async def pick_card(pool: Any, task: Task, goal_id: Optional[str], wanted: Optional[str]) -> Optional[dict]:
    if wanted:
        row = await pool.fetchrow("SELECT id, server, tools, extra FROM tool_cards WHERE id = $1 OR server = $1 LIMIT 1",
                                  wanted)
        return dict(row) if row else None
    if not goal_id:
        return None
    row = await pool.fetchrow(
        """
        SELECT t.id, t.server, t.tools, t.extra
        FROM tool_card_goals g JOIN tool_cards t ON t.id = g.card_id
        WHERE g.goal_id = $1::uuid
        ORDER BY g.score DESC, NULLIF(t.extra->>'stars', '')::int DESC NULLS LAST, t.id
        LIMIT 1
        """,
        goal_id,
    )
    return dict(row) if row else None


def tool_for(card: dict, task: Task) -> tuple[str, dict]:
    """-> (tool name, arguments). A search-shaped call: the task's prompt is the query."""
    from app import tool_providers

    adapter = tool_providers.get(card["server"]) or tool_providers.get(card["id"])
    if adapter is not None:
        return ADAPTER_TOOL.get(task.goal_hint, next(iter(adapter.TOOLS))), {"query": task.prompt}
    tools = list(card.get("tools") or [])
    return (tools[0] if tools else ""), {"query": task.prompt}


async def run_call(pool: Any, card: dict, tool: str, arguments: dict) -> dict:
    """An allowed call: the provider adapter, else the card's MCP connector through the gateway client."""
    from app import tool_providers
    from app.tool_routing import gateway

    adapter = tool_providers.get(card["server"]) or tool_providers.get(card["id"])
    if adapter is not None:
        return await asyncio.wait_for(adapter.call(tool, arguments), timeout=CALL_TIMEOUT_S)
    extra = card.get("extra")
    extra = json.loads(extra) if isinstance(extra, str) else dict(extra or {})
    url = str(extra.get("connector_url") or "")
    if not url:
        return {"ok": False, "status": "failed:no_connector", "text": ""}
    try:
        result, _s, _r = await asyncio.wait_for(gateway.call_tool(url, tool, arguments, server=card["server"]),
                                                timeout=CALL_TIMEOUT_S)
    except gateway.GatewaySignIn:
        return {"ok": False, "status": "refused:signin", "text": ""}
    except gateway.GatewayRefused as exc:
        return {"ok": False, "status": "failed:refused", "text": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": f"failed:{type(exc).__name__}", "text": ""}
    text, _images = gateway.content_parts(result)
    ok = not result.get("isError")
    return {"ok": ok, "status": "ok" if ok else "failed:tool_error", "text": text, "cost_usd": None}


async def run_task(pool: Any, task: Task, *, card_id: Optional[str] = None, use_cert: bool = True,
                   risk_ceiling: str = "R1", store: bool = True) -> tuple[RunRecord, list[str]]:
    from app.tool_routing import certificates, gate

    os.environ.setdefault("TOOL_GATE", "enforce")
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    goal_key, goal_id = await resolve_goal(pool, task)
    card = await pick_card(pool, task, goal_id, card_id)
    trace: list[str] = []
    status, error_class, raw, cost = "error", None, "", 0.0
    sc = None

    if card is None:
        error_class = "no_tool"
        trace.append(f"no tool mapped to goal {goal_key}")
    else:
        tool, arguments = tool_for(card, task)
        cert_id = None
        if use_cert:
            cert_id = await certificates.issue(
                pool, task_id=task.id, owner_id=OWNER, goal_id=goal_id, agent="compare-harness",
                allowed_servers=[card["server"]], allowed_tools=[tool], data_class=task.data_class,
                risk_ceiling=risk_ceiling, max_calls=3, ttl_s=900, issued_by="scripts/compare/tool_runner")
        call = gate.ToolCall(owner_id=OWNER, task_id=task.id, goal_id=goal_id, server=card["server"], tool=tool,
                             arguments=arguments, data_class=task.data_class, agent="compare-harness", cert_id=cert_id)
        decision = await gate.authorize(pool, call)
        trace.append(f"gate: {decision.effect} ({decision.reason}) risk {decision.risk}  {card['server']}/{tool}")
        if decision.effect != "allow" or getattr(decision, "shadow", False) and "shadow:" in decision.reason \
                and not decision.reason.startswith("shadow:allow"):
            error_class = "gate:denied"
        else:
            result = await run_call(pool, card, tool, arguments)
            cost = float(result.get("cost_usd") or 0.0)
            trace.append(f"call: {result.get('status')}")
            if not result.get("ok"):
                error_class = str(result.get("status") or "failed")
            elif cost > task.budget_usd:
                error_class = "budget"
            else:
                raw = str(result.get("text") or "")
                sc = await score(task, raw)
                status = sc.status
                error_class = None if sc.status != "error" else f"score:{sc.detail[:60]}"

    record = RunRecord(run_id="", task_id=task.id, arm="tool", goal_id=goal_key, procedure_id=None,
                       card_id=card["id"] if card else None, started_at=started.isoformat(),
                       latency_ms=int((time.monotonic() - t0) * 1000), cost_usd=round(cost, 6), tokens_in=0,
                       tokens_out=0, status=status, score=sc.score if sc else 0.0,
                       artifact_hash=sc.raw_hash if sc else None, error_class=error_class,
                       trace_ref=sha("\n".join(trace)) if trace else None)
    if store:
        from app.tool_routing.runs import finish_run, start_run

        record.run_id = await start_run(pool, task_id=task.id, arm="tool", goal_id=goal_key, procedure_id=None,
                                        card_id=record.card_id)
        await finish_run(pool, record.run_id, {"status": record.status, "latency_ms": record.latency_ms,
                                               "cost_usd": record.cost_usd, "score": record.score,
                                               "artifact_hash": record.artifact_hash,
                                               "error_class": record.error_class, "trace_ref": record.trace_ref})
    return record, trace


async def main_async(args: argparse.Namespace) -> int:
    url = dsn(args.dsn)
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    import asyncpg

    task = load(ids=[args.task])[0]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3, timeout=30)
    try:
        record, trace = await run_task(pool, task, card_id=args.card, use_cert=not args.no_cert,
                                       risk_ceiling=args.risk_ceiling, store=not args.no_store)
    finally:
        await pool.close()
    for line in trace:
        print(line)
    print(json.dumps(record.to_dict(), indent=2))
    return 0 if record.status != "error" else 1


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ap = argparse.ArgumentParser(description="Run the tool arm for one task, through the gate.")
    ap.add_argument("--task", required=True)
    ap.add_argument("--card", help="a tool card id or server instead of the Goal's top tool")
    ap.add_argument("--no-cert", action="store_true", help="ask the gate without an intent certificate")
    ap.add_argument("--risk-ceiling", default="R1")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--dsn", default="")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
