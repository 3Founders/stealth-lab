"""Ask the gate what would happen to one call (P1-04).

    python scripts/check_gate.py --server ai.exa/exa --tool web_search_exa --owner dev-local
    python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local
    python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local \
        --cert ic_... --task demo-2

Prints the decision, the reason and the limits. Exit codes: 0 allow, 3 approve, 4 deny. The
decision is the real `gate.authorize()` path, including the audit receipt.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def parse_arg(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise SystemExit(f"--arg needs key=value, got {text!r}")
    return key.strip(), value


async def run(args: argparse.Namespace) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=30)
    try:
        from app.tool_routing import gate

        call = gate.ToolCall(
            owner_id=args.owner, task_id=args.task or None, goal_id=args.goal,
            server=args.server, tool=args.tool, arguments=dict(args.arg or []),
            data_class=args.data_class, agent=args.agent, cert_id=args.cert,
        )
        decision = await gate.authorize(pool, call)
        print(f"mode:    {gate.gate_mode()}")
        print(f"effect:  {decision.effect}")
        print(f"reason:  {decision.reason}")
        print(f"risk:    {decision.risk}")
        print(f"approval:{decision.approval_id or '-'}")
        print(f"shadow:  {decision.shadow}")
        print(f"limits:  {decision.constraints}")
        return {"allow": 0, "approve": 3, "deny": 4}.get(decision.effect, 4)
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate one call through the gate.")
    ap.add_argument("--server", required=True)
    ap.add_argument("--tool", default="")
    ap.add_argument("--owner", default=os.environ.get("KELL_DEV_OWNER") or "dev-local")
    ap.add_argument("--agent", default="kell")
    ap.add_argument("--task", default="")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--data-class", default="PUBLIC_SOURCE")
    ap.add_argument("--cert", default=None, help="intent certificate id")
    ap.add_argument("--arg", action="append", metavar="KEY=VALUE")
    args = ap.parse_args()
    args.arg = dict(parse_arg(a) for a in (args.arg or []))
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
