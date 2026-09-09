#!/usr/bin/env python
"""
Personal procedure library CLI -- inspect/use the private local
LocalProcedureStore (app/local_agent/local_store.py) without querying its
SQLite file by hand.

    python scripts/personal_library.py list
    python scripts/personal_library.py search "pandas append"
    python scripts/personal_library.py show <row_id>
    python scripts/personal_library.py publish <row_id> --published-by you@example.com

`--workspace` (default: cwd) picks which .stealthlab/local_procedures.db
is opened, same resolution LocalProcedureStore itself uses. `publish`
needs a real Postgres pool (DATABASE_URL) -- the other subcommands need
neither a server nor a DB.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.local_agent.local_store import LocalProcedureNotFound, LocalProcedureStore


def _stats(row: dict) -> dict:
    return row.get("verification_stats") or {}


def _published(row: dict) -> bool:
    return bool(row.get("published_procedure_row_id"))


def format_list(store: LocalProcedureStore) -> str:
    rows = store.list_local_procedures()
    if not rows:
        return "(no local procedures captured yet)"
    header = f"{'name':<30} {'goal':<35} {'state':<10} {'attempts':<9} {'contexts':<9} {'published':<10} {'staleness'}"
    lines = [header, "-" * len(header)]
    for r in rows:
        stats = _stats(r)
        lines.append(
            f"{r['name'][:30]:<30} {r['goal'][:35]:<35} {r['verification_state']:<10} "
            f"{stats.get('attempts', 0):<9} {stats.get('distinct_contexts', 0):<9} "
            f"{'yes' if _published(r) else 'no':<10} {r['staleness']}"
        )
    return "\n".join(lines)


def format_search(store: LocalProcedureStore, query: str) -> str:
    hits = store.search_local_procedures(query)
    if not hits:
        return f"(no local procedures match {query!r})"
    lines = [f"{i + 1}. {r['name']}  --  {r['goal']}  [id={r['id']}]" for i, r in enumerate(hits)]
    return "\n".join(lines)


def format_show(store: LocalProcedureStore, row_id: str) -> str:
    row = store.get_local_procedure(row_id)
    if row is None:
        raise LocalProcedureNotFound(row_id)
    stats = _stats(row)
    lines = [
        f"id:                {row['id']}",
        f"procedure_id:      {row['procedure_id']}",
        f"name:              {row['name']}",
        f"goal:              {row['goal']}",
        f"provenance:        {row['provenance']}",
        f"scope_type:        {row['scope_type']}",
        f"scope_entity_id:   {row.get('scope_entity_id')}",
        f"scope:             {row.get('scope')}",
        f"verification_state: {row['verification_state']}",
        f"staleness:         {row['staleness']}",
        f"availability:      {row['availability']}",
        f"verification_stats: attempts={stats.get('attempts', 0)} successes={stats.get('successes', 0)} "
        f"distinct_contexts={stats.get('distinct_contexts', 0)} consecutive_failures={stats.get('consecutive_failures', 0)}",
        "steps:",
    ]
    for i, step in enumerate(row.get("steps") or [], 1):
        lines.append(f"  {i}. {step}")
    if _published(row):
        lines.append(
            f"published:         yes -- global procedure_id={row['published_procedure_id']} "
            f"row_id={row['published_procedure_row_id']} by={row.get('published_by')} at={row.get('published_at')}"
        )
    else:
        lines.append("published:         no")
    return "\n".join(lines)


def format_publish_result(row_id: str, result: dict) -> str:
    return (
        f"local procedure {row_id} published as global candidate "
        f"(procedure_id={result['procedure_id']}, row_id={result['id']}), "
        "pending independent validation -- not yet globally trusted"
    )


async def do_publish(pool, store: LocalProcedureStore, row_id: str, published_by: str, force: bool) -> str:
    from app.services.publish import publish_local_procedure
    result = await publish_local_procedure(
        pool, local_store=store, local_row_id=row_id, actor_subject=published_by, force=force,
    )
    return format_publish_result(row_id, result)


def _open_store(args: argparse.Namespace) -> LocalProcedureStore:
    return LocalProcedureStore(args.workspace)


async def _run_publish(args: argparse.Namespace) -> int:
    from app.db.session import create_pool
    store = _open_store(args)
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        print(await do_publish(pool, store, args.id, args.published_by, args.force))
    finally:
        await pool.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", default=os.getcwd(),
                     help="workspace whose .stealthlab/local_procedures.db to open (default: cwd)")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="table of local procedures")

    p_search = sub.add_parser("search", help="search local procedures")
    p_search.add_argument("query")

    p_show = sub.add_parser("show", help="full detail for one local procedure")
    p_show.add_argument("id")

    p_publish = sub.add_parser("publish", help="publish a local procedure as a global candidate")
    p_publish.add_argument("id")
    p_publish.add_argument("--published-by", required=True, help="real, explicit publishing user (e.g. an email)")
    p_publish.add_argument("--force", action="store_true", help="publish again even if already published")

    args = ap.parse_args()

    if args.command == "list":
        print(format_list(_open_store(args)))
        return 0
    if args.command == "search":
        print(format_search(_open_store(args), args.query))
        return 0
    if args.command == "show":
        try:
            print(format_show(_open_store(args), args.id))
        except LocalProcedureNotFound:
            print(f"no local procedure with id {args.id!r}", file=sys.stderr)
            return 1
        return 0
    if args.command == "publish":
        return asyncio.run(_run_publish(args))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
