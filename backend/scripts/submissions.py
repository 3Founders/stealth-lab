"""Goal submissions from the command line (docs/goal_submission.md): submit, look, review, decide.

    python scripts/submissions.py preview  submission.json
    python scripts/submissions.py submit   submission.json --owner <subject>
    python scripts/submissions.py list     [--status reviewing|needs_changes|ai_approved|approved] [--owner <subject>]
    python scripts/submissions.py show     <submission-id>
    python scripts/submissions.py review   <submission-id> | --pending      (re-run the checks + AI reviewer)
    python scripts/submissions.py approve  <submission-id> <step> --by <you>
    python scripts/submissions.py reject   <submission-id> <step> --by <you> --reason "..."   (the submitter is emailed)

submission.json: {"goal": "...", "description": "...", "steps": [{"kind": "human", "description": "..."},
                  {"kind": "automated", "prompt": "...", "locator": "https://..."}]}

Database: --dsn, else DATABASE_URL in the environment, else backend/.env (which is production: pass --dsn for a scratch
database). --no-email keeps a rejection from emailing the submitter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # titles can be any Unicode; the Windows console defaults to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import dotenv_values


def _dsn(arg: str) -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    dsn = arg or os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""
    if not dsn:
        raise SystemExit("no database: pass --dsn, or set DATABASE_URL")
    return dsn


def _print_submission(sub: dict) -> None:
    print(f"{sub['id']}  {sub['goal_name']}  [{sub['mode_label']}]  {sub['status']}  owner={sub['owner_id']}")
    if sub.get("parent_procedure_id"):
        print(f"  parent Way: {sub['parent_procedure_id']}")
    for s in sub["steps"]:
        what = s["description"] or s["prompt"]
        loc = f"  locator={s['locator']['type']}:{s['locator']['value']}" if s.get("locator") else ""
        print(f"  {s['order']}. [{s['kind']}] {s['status']:<11} {s['title']}")
        if what and what != s["title"]:
            print(f"       {what[:160]}")
        if loc:
            print(f"     {loc}")
        if s["reason"]:
            print(f"       why: {s['reason']}  ({s['reviewer']})")


async def run(args: argparse.Namespace) -> int:
    from app.submissions import api, intake, review, store

    if args.cmd == "preview":
        print(json.dumps(api.preview(json.loads(Path(args.target).read_text(encoding="utf-8"))), indent=1))
        return 0
    from app.db.session import create_pool

    pool = await create_pool(_dsn(args.dsn))
    try:
        if args.cmd == "submit":
            if not args.owner:
                raise SystemExit("--owner <subject> is required (whose submission it is)")
            out = await api.submit(pool, args.owner, json.loads(Path(args.target).read_text(encoding="utf-8")))
            print(out["message"])
            _print_submission(out)
        elif args.cmd == "list":
            rows = await pool.fetch(
                """SELECT g.id::text AS id, g.goal_name, g.mode, g.status, g.owner_id, g.created_at,
                          count(s.*) FILTER (WHERE s.status = 'rejected') AS rejected, count(s.*) AS steps
                   FROM goal_submissions g LEFT JOIN goal_submission_steps s ON s.submission_id = g.id
                   WHERE ($1::text IS NULL OR g.status = $1) AND ($2::text IS NULL OR g.owner_id = $2)
                   GROUP BY g.id ORDER BY g.created_at DESC LIMIT $3""", args.status, args.owner, args.limit)
            for r in rows:
                print(f"{r['id']}  {r['created_at']:%Y-%m-%d %H:%M}  {r['status']:<13} {r['mode']:<11} "
                      f"{r['steps']} steps ({r['rejected']} rejected)  {r['goal_name']}  owner={r['owner_id']}")
            if not rows:
                print("none")
        elif args.cmd == "show":
            sub = await store.get(pool, args.target)
            if sub is None:
                raise SystemExit("no such submission")
            _print_submission(sub)
        elif args.cmd == "review":
            if args.pending:
                for r in await review.review_pending(pool):
                    print(r)
            else:
                out = await review.review_submission(pool, args.target, notify=not args.no_email)
                _print_submission(out)
                print(out["review"])
        elif args.cmd in ("approve", "reject"):
            if not args.by:
                raise SystemExit("--by <you> is required")
            res = await review.decide(pool, args.target, int(args.step), approve=args.cmd == "approve", by=args.by,
                                      reason=args.reason or "", notify=not args.no_email)
            print(res)
            _print_submission(await store.get(pool, args.target))
    except (intake.SubmissionError, LookupError, ValueError) as exc:
        print(f"refused: {exc}")
        return 1
    finally:
        await pool.close()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Goal submissions.")
    ap.add_argument("cmd", choices=("preview", "submit", "list", "show", "review", "approve", "reject"))
    ap.add_argument("target", nargs="?", default="", help="a submission.json, or a submission id")
    ap.add_argument("step", nargs="?", default="", help="approve/reject: the step number")
    ap.add_argument("--owner", default=None)
    ap.add_argument("--status", default=None)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--by", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--dsn", default="")
    args = ap.parse_args()
    if args.cmd in ("preview", "submit", "show", "approve", "reject") and not args.target:
        ap.error(f"{args.cmd} needs a target")
    if args.cmd == "review" and not (args.target or args.pending):
        ap.error("review needs a submission id or --pending")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
