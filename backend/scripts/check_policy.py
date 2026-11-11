"""Ask the policy store what would happen to one call (P1-05).

    python scripts/check_policy.py --server ai.exa/exa --tool web_search_exa
    python scripts/check_policy.py --server hinoki --tool scan_repository --risk-ceiling R3
    python scripts/check_policy.py --server unknown --tool x --expect deny

Read-only. Prints the effect, the reason code and the constraints the gate would apply.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


async def run(args: argparse.Namespace) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=30)
    try:
        from app.tool_routing import policy

        call = SimpleNamespace(server=args.server, tool=args.tool, goal_id=args.goal,
                               data_class=args.data_class)
        cert = SimpleNamespace(risk_ceiling=args.risk_ceiling, max_calls=args.max_calls)
        effect, reason, constraints = await policy.evaluate(pool, call, cert)
        print(f"effect:  {effect}")
        print(f"reason:  {reason}")
        print(f"risk:    {constraints.get('risk', '?')}")
        print(f"limits:  {constraints}")
        if args.expect and effect != args.expect:
            print(f"MISMATCH: expected {args.expect}", file=sys.stderr)
            return 1
        return 0
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate one tool call against tool_policies.")
    ap.add_argument("--server", required=True)
    ap.add_argument("--tool", default="")
    ap.add_argument("--goal", default=None, help="goal uuid, when the policy is goal-scoped")
    ap.add_argument("--data-class", default="PUBLIC_SOURCE")
    ap.add_argument("--risk-ceiling", default="R4")
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument("--expect", choices=("allow", "deny", "approve"), default=None)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
