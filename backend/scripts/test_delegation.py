"""Add a Goal, prove the router can retrieve it, and test the desktop delegation stream end to end.

    # add a Goal, then check the catalog and rank it back
    python scripts/test_delegation.py --goal "Send a Slack message" \
        --rationale "the team lives in Slack" --objective "the message is posted"

    # the delegation stream for a task that has a Way (no write)
    python scripts/test_delegation.py --no-add --task "find vulnerabilities in my dependencies"

    # ...and the scoped side call (needs the desktop app's engine running)
    python scripts/test_delegation.py --no-add --task "find vulnerabilities in my dependencies" \
        --ask "what did you check?"

Steps:
  1. add:  create_goal_from_user -> the row, on K000 (pinned), status candidate
  2. read: goal_catalog.refresh_now -> catalog.match -> rank_payload
  3. plan: POST /api/delegate with Accept: text/event-stream; print every status line with its time,
           then the Goal / Way / Tool / model / brief
  4. ask:  POST /api/delegate/ask (SSE); print sent / waiting / answer

The desktop server must be up (`npm run dev` or the app). --url defaults to http://127.0.0.1:3400.
--dry-run writes nothing. Adding a Goal writes to the database this .env points at: production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a Goal name can be any Unicode; the Windows console defaults to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import httpx  # noqa: E402


def _sse(client: httpx.Client, url: str, body: dict[str, Any], timeout: float) -> Iterator[dict[str, Any]]:
    """Yield each `data: {...}` event as it arrives (the routes answer text/event-stream)."""
    with client.stream("POST", url, json=body, timeout=timeout,
                       headers={"accept": "text/event-stream"}) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"{url} answered {r.status_code}: {r.read().decode()[:300]}")
        for line in r.iter_lines():
            if line.startswith("data: "):
                try:
                    yield json.loads(line[6:])
                except ValueError:
                    continue


def _print_plan(plan: dict[str, Any]) -> None:
    goal = plan.get("goal") or {}
    way = plan.get("way") or {}
    tool = plan.get("tool") or {}
    model = plan.get("model") or {}
    print(f"  Goal : {goal.get('name') or 'no Goal matched'}")
    print(f"  Way  : {way.get('name') or 'none'} [{way.get('approval') or '-'}]")
    print(f"  Tool : {tool.get('name') or 'none'}{' @ ' + tool['server'] if tool.get('server') else ''}")
    print(f"  Model: {model.get('id') or 'the engine default'} ({plan.get('rung') or '-'})")
    print(f"  Why  : {plan.get('why')}")
    print("  Brief:")
    for line in str(plan.get("brief") or "").splitlines():
        print(f"    {line}")


async def add_goal(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.dry_run:
        print(f"would add: {args.goal}")
        return None
    from app.db.session import create_pool
    from app.services.goals import GoalQualityRejected, create_goal_from_user
    from app.services.v0_gate import V0Violation
    from app.tool_routing import goal_catalog

    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        raise SystemExit("no DATABASE_URL: set it in backend/.env")
    pool = await create_pool(url, min_size=1, max_size=2, timeout=60)
    try:
        try:
            made = await create_goal_from_user(
                pool, canonical_name=args.goal, rationale=args.rationale,
                objective=args.objective, scope_type="global", owner_id=args.owner,
                allow_create_anyway=args.allow_anyway,
            )
        except (V0Violation, GoalQualityRejected, ValueError) as exc:
            raise SystemExit(f"REFUSED: {exc}") from exc
        print(f"add: {made.get('outcome')} {made.get('goal', {}).get('id', '')}")
        if made.get("outcome") == "near_matches":
            for c in made.get("candidates", [])[:5]:
                print(f"  near: {c.get('canonical_name')}  ({c.get('id')})")
            print("  nothing written; read them, then rerun with --allow-anyway")
        if made.get("outcome") == "created":
            await goal_catalog.refresh_now(pool)

        # Step 2: the row must be retrievable from the same catalog the router uses.
        from app.tool_routing.route_api import rank_payload

        catalog = await goal_catalog.load(pool, force=True)
        hits = catalog.match(args.goal, limit=3)
        print(f"catalog: {len(catalog.entries)} Goals; match for {args.goal!r}:")
        for h in hits:
            print(f"  {h['id']}  {h['canonical_name']}  ({h['matched_by']}, {h['score']:.2f})")
        ranked = await rank_payload(pool, args.goal, limit=3)
        print("rank: " + ", ".join(g["canonical_name"] for g in ranked["goals"]))
        if not any(h["canonical_name"] == args.goal for h in hits):
            print("WARNING: the new Goal did not come back from the catalog")
        return made
    finally:
        await pool.close()


def delegation(args: argparse.Namespace) -> dict[str, Any] | None:
    base = args.url.rstrip("/")
    with httpx.Client() as client:
        try:
            settings = client.get(f"{base}/api/settings", timeout=10).json()
            print(f"desktop: signedIn={settings.get('signedIn')} lane={settings.get('lane')} "
                  f"models={settings.get('models')}")
        except httpx.HTTPError as exc:
            raise SystemExit(f"the desktop server is not reachable at {base}: {exc}\n"
                             "start it with `npm run dev` (or open the app)") from exc

        print(f"\nplan for {args.task!r} (status stream):")
        t0 = time.perf_counter()
        plan: dict[str, Any] | None = None
        for ev in _sse(client, f"{base}/api/delegate",
                       {"text": args.task, "models": {}, "timeoutMs": 30_000}, timeout=60):
            elapsed = time.perf_counter() - t0
            if ev.get("type") == "status":
                print(f"  {elapsed:5.1f}s  [{ev.get('step')}] {ev.get('text')}")
            elif ev.get("type") == "plan":
                plan = ev["plan"]
            elif ev.get("type") == "error":
                print(f"  {elapsed:5.1f}s  ERROR {ev.get('error')}")
            elif ev.get("type") == "done":
                print(f"  {elapsed:5.1f}s  done")
        if plan:
            print("\nplan:")
            _print_plan(plan)
        return plan


def ask(args: argparse.Namespace, plan: dict[str, Any] | None) -> None:
    if not plan:
        print("\nask: skipped (no plan)")
        return
    base = args.url.rstrip("/")
    print(f"\nask {args.ask!r} (scoped: brief + question, no tools/files/history):")
    t0 = time.perf_counter()
    with httpx.Client() as client:
        for ev in _sse(client, f"{base}/api/delegate/ask",
                       {"plan": plan, "question": args.ask}, timeout=180):
            elapsed = time.perf_counter() - t0
            if ev.get("type") == "status":
                print(f"  {elapsed:5.1f}s  [{ev.get('step')}] {ev.get('text')}")
            elif ev.get("type") == "answer":
                print(f"  {elapsed:5.1f}s  answer ({ev.get('model')}):\n{ev.get('answer')}")
            elif ev.get("type") == "error":
                print(f"  {elapsed:5.1f}s  ERROR {ev.get('error')}")
            elif ev.get("type") == "done":
                print(f"  {elapsed:5.1f}s  done")


def main() -> None:
    ap = argparse.ArgumentParser(description="Add a Goal and test the desktop delegation stream.")
    ap.add_argument("--goal", default="Send a Slack message", help="the Goal's canonical name")
    ap.add_argument("--rationale", default="the team lives in Slack", help="why it is worth doing")
    ap.add_argument("--objective", default="the message is posted", help="what counts as done")
    ap.add_argument("--owner", default="local-user")
    ap.add_argument("--allow-anyway", action="store_true", help="create even when a near match exists")
    ap.add_argument("--no-add", action="store_true", help="skip step 1 (do not write a Goal)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be added; write nothing")
    ap.add_argument("--task", default="find vulnerabilities in my dependencies",
                    help="the task to route and delegate")
    ap.add_argument("--ask", default="", help="also send this scoped question to the delegated model")
    ap.add_argument("--url", default="http://127.0.0.1:3400", help="the desktop server")
    args = ap.parse_args()

    made = None
    if not args.no_add:
        made = asyncio.run(add_goal(args))
        print()
    plan = delegation(args)
    if args.ask:
        ask(args, plan)
    if made and made.get("outcome") == "created":
        print(f"\nadded Goal {made['goal']['id']}; soft-delete it when the check is done "
              "(see scripts/add_goal.py for the pattern).")


if __name__ == "__main__":
    main()
