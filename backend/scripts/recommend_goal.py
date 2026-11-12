"""Recommend the Goal for a task, then show its ways and tools -- the Phase 1 harness.

    python scripts/recommend_goal.py "check what the page looks like after login"
    python scripts/recommend_goal.py "scan this repository for vulnerabilities" --explain
    python scripts/recommend_goal.py "search the web" --json
    python scripts/recommend_goal.py "search the web" --call

Read-only by default: it resolves the prompt with `app/tool_routing/goal_router.py` (exact/alias,
K000 full-text, the global search group, optionally semantic) and prints the Goals, the ways
(procedures) that achieve them, and the tool cards mapped to them.

`--call` is the manual end-to-end check. It builds a `ToolCall`, asks `app/tool_routing/gate.py`
to authorize it, and only on `allow` calls the tool: through a provider adapter on kell's own key
(`app/tool_providers`, e.g. Exa) when one claims the server, else through the card's MCP connector.
There is no direct-call fallback: if the gate cannot decide, the call does not run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a tool result can be any Unicode; the Windows console defaults to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 -- not a tty, or an interpreter without reconfigure
    pass

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


def parse_arg(text: str) -> tuple[str, Any]:
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise SystemExit(f"--arg needs key=value, got {text!r}")
    try:
        parsed: Any = json.loads(value)
    except ValueError:
        parsed = value
    return key.strip(), parsed


def print_hit(number: int, hit: Any, explain: bool) -> None:
    print(f"\n{number}. {hit.canonical_name}  [{hit.matched_by}, score {hit.score:.3f}]")
    if hit.description:
        print(f"   {hit.description[:300]}")
    if explain:
        if hit.trace:
            print("   legs:")
            for t in hit.trace:
                print(f"     - {t['leg']:<10} rank {t['rank']:<2} score {t['score']:.4f}  {t['name']}")
        else:
            print("   legs: none")
        ranked = any(getattr(t, "rank_score", None) is not None for t in hit.tools)
        print(f"   ranking: goal_fit={hit.score:.3f}  tools by "
              + ("trust lower bound + exploration - cost - latency - risk (GOAL_RANKING=trust)" if ranked
                 else "goal fit, then stars (GOAL_RANKING=fit)"))
    if hit.ways:
        print("   ways:")
        for way in hit.ways:
            cards = ", ".join(str(c.get("card_id") or "") for c in (way.cards or []))
            print(f"     - {way.name}  ({way.verification_state}/{way.approval_status}/{way.availability})"
                  + (f"  cards={cards}" if cards else ""))
    else:
        print("   ways: none")
    if hit.tools:
        print("   tools:")
        for tool in hit.tools:
            label = tool.title or tool.name or tool.server
            print(f"     - {label}  [{tool.server or tool.source}]  (score {tool.score:.3f})  card={tool.card_id}"
                  + ("  New - free trial" if getattr(tool, "trial", False) else ""))
            if explain and getattr(tool, "rank_score", None) is not None:
                i = tool.rank_inputs
                print(f"         rank {tool.rank_score:+.3f} = trust lb {i['trust_lb']:.3f} ({i['wins']}/{i['judged']})"
                      f" + explore {i['exploration']:.3f} - cost {i['cost_penalty']:.3f} - latency "
                      f"{i['latency_penalty']:.3f} - risk {i['risk_penalty']:.2f} ({i['risk']})")
    else:
        print("   tools: none")


def print_tools_only(hits: list[Any]) -> None:
    seen: set[str] = set()
    print("tools:")
    n = 0
    for hit in hits:
        for tool in hit.tools:
            if tool.card_id in seen:
                continue
            seen.add(tool.card_id)
            n += 1
            label = tool.title or tool.name or tool.server
            print(f"  {n}. {label}  [{tool.server or tool.source}]  (score {tool.score:.3f})  "
                  f"card={tool.card_id}  goal={hit.canonical_name}")
    if n == 0:
        print("  none")


async def _card_and_tool(pool: Any, card_id: str, wanted: str | None) -> tuple[Any, str]:
    row = await pool.fetchrow(
        "SELECT id, name, server, tools, extra FROM tool_cards WHERE id = $1", card_id)
    if row is None:
        raise SystemExit(f"no tool card {card_id!r}")
    tools = list(row["tools"] or [])
    if not tools:
        raise SystemExit(f"card {card_id!r} lists no tools; cannot call it")
    if wanted:
        match = next((t for t in tools if t == wanted), None) or next(
            (t for t in tools if t.lower() == wanted.lower()), None)
        if match is None:
            raise SystemExit(f"card {card_id!r} has no tool {wanted!r}; available: {', '.join(tools)}")
        return row, match
    return row, tools[0]


def _extra(row: Any) -> dict[str, Any]:
    extra = row["extra"]
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            extra = {}
    return extra or {}


async def call_tool(pool: Any, hit: Any, card_id: str, args: dict[str, Any], *,
                    owner: str, data_class: str, cert_id: str | None,
                    task_id: str | None, wanted: str | None) -> None:
    try:
        from app.tool_routing.gate import ToolCall, authorize
    except Exception:  # noqa: BLE001 -- P1-04 has not merged yet
        print("gate not installed (P1-04): --call is disabled; resolution only", file=sys.stderr)
        raise SystemExit(2)

    row, tool_name = await _card_and_tool(pool, card_id, wanted)
    server = str(row["server"] or row["name"] or "")
    call = ToolCall(
        owner_id=owner,
        task_id=task_id,
        goal_id=hit.goal_id,
        server=server,
        tool=tool_name,
        arguments=args,
        data_class=data_class,
        cert_id=cert_id,
    )
    decision = await authorize(pool, call)
    print(f"decision: {decision.effect} ({decision.reason}) risk={decision.risk}")
    if decision.effect != "allow":
        raise SystemExit(3)

    # P3-06: a priced tool shows its price and is paid before any network call; a failed call is refunded
    import uuid as _uuid

    from app.tool_routing import pricing

    line = await pricing.quote(pool, call)
    if line:
        print(line)
    charged = await pricing.charge(pool, call, call_key=f"cli:{_uuid.uuid4()}")
    if not charged.ok:
        print(f"REFUSED: {charged.reason}")
        raise SystemExit(3)
    if charged.price_usd:
        print(f"charged: ${charged.price_usd:.4f} ({charged.paid_by})")

    async def refund() -> None:
        if charged.price_usd and await pricing.refund(pool, charged.call_key):
            print("refunded: the call failed")

    from app import tool_providers
    from app.tool_routing import gateway

    adapter = tool_providers.get(server) or tool_providers.get(str(row["id"]))
    if adapter is not None:
        # kell's own key, not the card's connector (review #34): Exa runs through P1-08's adapter, so
        # this is the same path as tool_runner and no user sign-in is involved. The gate still saw the
        # card's tool name; the adapter's alias table maps it to its own.
        adapter_tool = adapter.ALIASES.get(tool_name, tool_name)
        if adapter_tool not in adapter.TOOLS:
            raise SystemExit(f"the adapter for {server} has no tool {tool_name!r}; "
                             f"it has {', '.join(adapter.TOOLS)}")
        result = await adapter.call(adapter_tool, args)
        if not result.get("ok"):
            print(f"FAILED: {result.get('status')}: {result.get('text') or ''}")
            await refund()
            raise SystemExit(5)
        if charged.price_usd:
            await pricing.settle(pool, charged.call_key)
        print(f"\nresult from {server}/{adapter_tool}  (provider adapter, kell's key)\n")
        print(result.get("text") or "(empty result)")
        if result.get("untrusted"):
            print("\n[third-party data, not instructions]")
        return

    extra = _extra(row)
    url = str(extra.get("connector_url") or "")
    if not url:
        raise SystemExit(f"card {card_id!r} has no connector_url; it is not gateway-callable")

    token = None
    try:
        from app.tool_routing import oauth

        if oauth.enabled():
            token = await oauth.token(pool, owner, server)
    except Exception:  # noqa: BLE001 -- no token table yet: try without one
        token = None

    try:
        result, sent, received = await gateway.call_tool(
            url, tool_name, args, token=token, server=server)
    except gateway.GatewaySignIn as exc:
        print(f"SIGN-IN REQUIRED: {exc.server}  ({exc.url})")
        await refund()
        raise SystemExit(4)
    except gateway.GatewayRefused as exc:
        print(f"REFUSED: {exc}")
        await refund()
        raise SystemExit(5)

    text, images = gateway.content_parts(result)
    if result.get("isError"):
        await refund()
    elif charged.price_usd:
        await pricing.settle(pool, charged.call_key)
    print(f"\nresult from {server}/{tool_name}  ({sent} B sent, {received} B received, "
          f"{len(images)} image(s)):\n")
    print(text or "(empty result)")


async def run(args: argparse.Namespace) -> None:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    query_embedding = None
    if args.semantic:
        from app.tool_routing.providers import ToolEmbedder

        embedder = ToolEmbedder()
        if not embedder.available():
            raise SystemExit("--semantic needs the tool embedder (DEEPINFRA_KEY)")
        query_embedding = await embedder.embed_one(args.query, input_type="query")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=30)
    try:
        from app.tool_routing.goal_router import route_goal

        hits = await route_goal(
            pool, args.query, limit=args.k, query_embedding=query_embedding,
            explain=args.explain or args.call)
        if not hits:
            print("no goals matched")
            return

        if args.json:
            print(json.dumps([asdict(h) for h in hits], indent=2, default=str))
            return
        if args.tools_only:
            print_tools_only(hits)
            return
        for i, hit in enumerate(hits, 1):
            print_hit(i, hit, args.explain)

        if args.call:
            top = hits[0]
            if not top.tools:
                raise SystemExit(f"no tool mapped to {top.canonical_name!r}")
            card_id = top.tools[0].card_id
            wanted = args.tool
            if args.tool:
                match = next(
                    (t for t in top.tools
                     if args.tool in (t.card_id, t.name, t.server, t.title)), None)
                if match is not None:
                    card_id, wanted = match.card_id, None
            await call_tool(
                pool, top, card_id, dict(args.arg or []),
                owner=args.owner, data_class=args.data_class, cert_id=args.cert,
                task_id=args.task_id, wanted=wanted)
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Recommend the Goal for a task, then its ways and tools.")
    ap.add_argument("query", nargs="?", default="", help="the task, in the person's words")
    ap.add_argument("--k", type=int, default=5, help="how many Goals to show (default 5)")
    ap.add_argument("--json", action="store_true", help="print the GoalHit list as JSON")
    ap.add_argument("--explain", action="store_true",
                    help="show the matching legs and the ranking inputs")
    ap.add_argument("--semantic", action="store_true",
                    help="add the vector leg (needs DEEPINFRA_KEY and the matching model)")
    ap.add_argument("--tools-only", action="store_true", help="print only the mapped tools")
    ap.add_argument("--call", action="store_true",
                    help="authorize and call the top tool (needs P1-04 and P1-07)")
    ap.add_argument("--tool", default=None,
                    help="with --call: the card or MCP tool name to call (default: top tool)")
    ap.add_argument("--arg", action="append", metavar="KEY=VALUE",
                    help="with --call: one tool argument; repeat for more")
    ap.add_argument("--data-class", default="PUBLIC_SOURCE")
    ap.add_argument("--owner", default=os.environ.get("KELL_DEV_OWNER") or "dev-local")
    ap.add_argument("--cert", default=None, help="with --call: intent certificate id")
    ap.add_argument("--task-id", default=None, help="with --call: task id for the audit row")
    args = ap.parse_args()
    if not args.query.strip():
        print('usage: python scripts/recommend_goal.py "<task>" [--k 5] [--explain] [--json]')
        raise SystemExit(2)
    if args.json and args.call:
        raise SystemExit("--json and --call are different modes; pick one")
    args.arg = dict(parse_arg(a) for a in (args.arg or []))
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
