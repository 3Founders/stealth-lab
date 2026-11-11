"""Issue or revoke a task-scoped intent certificate (P1-06).

    python scripts/issue_cert.py --task demo-1 --owner dev-local --risk R0 \
        --server ai.exa/exa --tool web_search_exa --ttl 3600

    python scripts/issue_cert.py --task demo-1 --owner dev-local --risk R3 \
        --server hinoki --tool scan_repository \
        --constraint '{"repo_url": {"required": true, "pattern": "^https://github.com/"}}'

    python scripts/issue_cert.py --revoke ic_... --owner dev-local

Prints the certificate id and the signed scope. The secret is `TOOL_CERT_SECRET`, or a derived
development key when it is unset.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


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
        from app.tool_routing import certificates

        if args.revoke:
            gone = await certificates.revoke(pool, args.revoke, args.owner)
            print(f"revoked: {gone}")
            return 0 if gone else 1

        constraints = {}
        if args.constraint_file:
            try:
                constraints = json.loads(Path(args.constraint_file).read_text(encoding="utf-8"))
            except ValueError as exc:
                raise SystemExit(f"--constraint-file is not JSON: {exc}") from exc
        elif args.constraint:
            try:
                constraints = json.loads(args.constraint)
            except ValueError as exc:
                raise SystemExit(f"--constraint is not JSON: {exc}") from exc

        cert_id = await certificates.issue(
            pool,
            task_id=args.task,
            owner_id=args.owner,
            goal_id=args.goal,
            agent=args.agent,
            allowed_servers=args.server or (),
            allowed_tools=args.tool or (),
            argument_constraints=constraints,
            data_class=args.data_class,
            risk_ceiling=args.risk,
            egress_allow=args.egress or (),
            max_calls=args.max_calls,
            ttl_s=args.ttl,
        )
        print(f"cert:    {cert_id}")
        print(f"task:    {args.task}")
        print(f"owner:   {args.owner}")
        print(f"goal:    {args.goal or '-'}")
        print(f"servers: {', '.join(args.server or []) or 'any'}")
        print(f"tools:   {', '.join(args.tool or []) or 'any'}")
        print(f"risk:    {args.risk}")
        print(f"data:    {args.data_class}")
        print(f"ttl:     {args.ttl}s")
        print(f"args:    {json.dumps(constraints, sort_keys=True)}")
        return 0
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Issue or revoke an intent certificate.")
    ap.add_argument("--task", default="", help="the task this certificate is scoped to")
    ap.add_argument("--owner", default=os.environ.get("KELL_DEV_OWNER") or "dev-local")
    ap.add_argument("--agent", default="kell")
    ap.add_argument("--goal", default=None, help="goal uuid")
    ap.add_argument("--server", action="append", help="allowed server; repeat for more")
    ap.add_argument("--tool", action="append", help="allowed tool; repeat for more")
    ap.add_argument("--data-class", default="PUBLIC_SOURCE")
    ap.add_argument("--risk", default="R0", choices=("R0", "R1", "R2", "R3", "R4"))
    ap.add_argument("--egress", action="append", help="allowed egress host; repeat for more")
    ap.add_argument("--max-calls", type=int, default=20)
    ap.add_argument("--ttl", type=int, default=3600, help="seconds until expiry")
    ap.add_argument("--constraint", default=None,
                    help="JSON argument constraints, e.g. '{\"repo_url\":{\"pattern\":\"^https://\"}}'")
    ap.add_argument("--constraint-file", default=None,
                    help="a JSON file with argument constraints (use this on Windows)")
    ap.add_argument("--revoke", default=None, help="certificate id to revoke instead of issuing")
    args = ap.parse_args()
    if not args.revoke and not args.task.strip():
        raise SystemExit("--task is required when issuing")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
