"""Add Goals, one at a time or from a JSONL file (docs/goal_router.md).

    python scripts/add_goal.py --name "Send a Slack message" --rationale "the team lives in Slack" \
        --objective "the message is posted in the channel"
    python scripts/add_goal.py --file goals.jsonl --dry-run
    python scripts/add_goal.py --file goals.jsonl --allow-anyway

Each line of the JSONL file is {"canonical_name", "rationale", "objective", "description"?}.
A Goal is stored as a candidate owned by --owner (default local-user), exactly as the MCP
`add_goal` tool and POST /v1/goals store it. The in-process catalog is rebuilt at the end, so
`/v1/goals/rank` and `/v1/goals/route` find the new Goals on the next call.

A near match (the same Goal under another name) writes nothing unless --allow-anyway: read the
candidates, then decide. --embed adds the semantic near-match check and stores an embedding
(one external embedding call per Goal); it is off by default so seeding needs no provider.

Database: --dsn, else DATABASE_URL in the environment, else backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a Goal name can be any Unicode; the Windows console defaults to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


def _rows(args: argparse.Namespace) -> list[dict]:
    rows: list[dict] = []
    if args.name:
        rows.append({"canonical_name": args.name, "rationale": args.rationale,
                     "objective": args.objective, "description": args.description})
    if args.file:
        path = Path(args.file)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise SystemExit(f"{path}:{n}: not JSON: {exc}") from exc
            rows.append({
                "canonical_name": str(row.get("canonical_name") or row.get("name") or "").strip(),
                "rationale": str(row.get("rationale") or "").strip(),
                "objective": str(row.get("objective") or "").strip(),
                "description": str(row.get("description") or "").strip(),
            })
    if not rows:
        raise SystemExit("nothing to add: pass --name, or --file with one JSON object per line")
    for i, row in enumerate(rows, 1):
        missing = [k for k in ("canonical_name", "rationale") if not row[k]]
        if not row["objective"] and not row["description"]:
            missing.append("objective (or description)")
        if missing:
            raise SystemExit(f"row {i}: missing {', '.join(missing)}")
    return rows


async def run(args: argparse.Namespace) -> int:
    rows = _rows(args)
    if args.dry_run:
        for row in rows:
            print(f"would add: {row['canonical_name']}")
        print(f"dry run: {len(rows)} Goal(s) not written")
        return 0

    url = dsn_from_env()
    if not url:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL (environment or backend/.env)")
    from app.db.session import create_pool
    from app.services.classification import DataClass
    from app.services.embeddings import Embedder
    from app.services.goals import GoalQualityRejected, create_goal_from_user
    from app.services.v0_gate import V0Violation
    from app.tool_routing import goal_catalog

    # app.db.session.create_pool, not asyncpg.create_pool: the JSONB codec it registers is what
    # lets the goal write pass dicts/lists (identity resolution records a decision as jsonb).
    pool = await create_pool(url, min_size=1, max_size=3, timeout=60)
    embedder = None
    if args.embed:
        embedder = Embedder(data_classification=DataClass.USER_PRIVATE, policy_pool=pool)

    created = matched = near = refused = 0
    try:
        for row in rows:
            try:
                made = await create_goal_from_user(
                    pool, canonical_name=row["canonical_name"],
                    description=row["description"] or None, rationale=row["rationale"],
                    objective=row["objective"] or None,
                    scope_type="global", owner_id=args.owner, embedder=embedder,
                    allow_create_anyway=args.allow_anyway,
                )
            except (V0Violation, GoalQualityRejected, ValueError) as exc:
                refused += 1
                print(f"REFUSED  {row['canonical_name'][:70]}: {exc}")
                continue
            outcome = made.get("outcome")
            if outcome == "created":
                created += 1
                print(f"created  {made['goal']['id']}  {row['canonical_name'][:70]}")
            elif outcome == "matched":
                matched += 1
                print(f"matched  {made['goal']['id']}  {row['canonical_name'][:70]}")
            else:
                near += 1
                names = ", ".join(c.get("canonical_name", "") for c in made.get("candidates", [])[:3])
                print(f"near     {row['canonical_name'][:70]}: {names}  (nothing written; --allow-anyway)")
        if created:
            await goal_catalog.refresh_now(pool)
        print(f"\n{created} created, {matched} matched, {near} near match(es), {refused} refused")
    finally:
        await pool.close()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Add Goals (candidates) to the goal catalog.")
    ap.add_argument("--name", help="one Goal's canonical name")
    ap.add_argument("--rationale", default="", help="why the Goal is worth accomplishing")
    ap.add_argument("--objective", default="", help="what counts as accomplished")
    ap.add_argument("--description", default="")
    ap.add_argument("--file", help="JSONL: one {canonical_name, rationale, objective} per line")
    ap.add_argument("--owner", default="local-user", help="who owns the new Goals")
    ap.add_argument("--allow-anyway", action="store_true",
                    help="create even when a near match exists")
    ap.add_argument("--embed", action="store_true",
                    help="semantic near-match check + a stored embedding (one external call per Goal)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()
    if args.dsn:
        os.environ["DATABASE_URL"] = args.dsn
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
