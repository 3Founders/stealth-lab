"""
Integration check for GET /v1/graph (app/api/graph.py) against a real
database. Not a pytest suite -- like the other integration_check_*.py
scripts here, it exercises real SQL rather than mocks, because that is
where this endpoint's risky assumptions actually live:

  - Placeholder threading. The overview query interpolates
    visibility_predicate(param_index=3) into BOTH branches of a UNION ALL
    and reuses $3 in each. Get the index wrong and asyncpg either raises
    at bind time or, worse, binds the viewer id into the wrong slot. The
    scope loop below is the check for that -- it runs the query under
    every shape of AccessScope, which is the only way to see the
    signed-in variant (the one that actually consumes a parameter).

  - COUNT(*) OVER () counting rows before LIMIT, not after. If that
    assumption were wrong, `truncated` would always be False and a
    partial graph would render as if it were the whole thing.

  - Dangling-edge pruning. Edges whose endpoints fall outside the
    returned node set must be dropped AND counted, never silently
    dropped: a missing link reads as "these steps aren't connected".

Read-only -- it writes nothing and is safe to run against a live
database.

Usage (from backend/, with a populated .env):

    python integration_check_graph_overview.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.api.graph import get_subgraph, get_whole_graph
from app.db.session import create_pool
from app.services.access import AccessScope


async def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    failures = 0

    print("== the query runs under every scope shape ==")
    for label, scope in (
        ("anonymous", AccessScope.anonymous()),
        ("signed-in", AccessScope.for_user("integration-check")),
        ("unrestricted", AccessScope.unrestricted()),
    ):
        try:
            r = await get_whole_graph(
                limit=400, include_superseded=False, pool=pool, scope=scope
            )
            print(f"  {label:13} nodes={len(r.nodes):3} edges={len(r.edges):3} "
                  f"total={r.total_nodes}/{r.total_edges}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  {label:13} FAILED: {exc}")

    public = await get_whole_graph(
        limit=400, include_superseded=False, pool=pool, scope=AccessScope.anonymous()
    )

    print("\n== totals match direct SQL ==")
    live_nodes = (
        await pool.fetchval(
            "SELECT COUNT(*) FROM task_nodes WHERE t_invalid IS NULL AND visibility = 'public'"
        )
    ) + (
        await pool.fetchval(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE t_invalid IS NULL AND visibility = 'public'"
        )
    )
    live_edges = await pool.fetchval(
        "SELECT COUNT(*) FROM edges WHERE t_invalid IS NULL AND visibility = 'public'"
    )
    for what, got, expected in (
        ("nodes", public.total_nodes, live_nodes),
        ("edges", public.total_edges, live_edges),
    ):
        ok = got == expected
        failures += not ok
        print(f"  {what}: endpoint={got} sql={expected} {'ok' if ok else 'MISMATCH'}")

    print("\n== superseded rows are excluded by default, included on request ==")
    with_history = await get_whole_graph(
        limit=2000, include_superseded=True, pool=pool, scope=AccessScope.anonymous()
    )
    print(f"  current only: {public.total_nodes} nodes | with history: "
          f"{with_history.total_nodes} nodes")
    if with_history.total_nodes < public.total_nodes:
        failures += 1
        print("  MISMATCH: including history returned fewer nodes than excluding it")
    if with_history.total_nodes == public.total_nodes:
        print("  (nothing has been superseded yet, so these are equal — expected on a"
              " young graph, but this check only bites once something is)")

    print("\n== a limit is reported, not silently applied ==")
    if public.total_nodes < 2:
        print("  skipped: needs at least 2 nodes in the graph")
    else:
        small = await get_whole_graph(
            limit=1, include_superseded=False, pool=pool, scope=AccessScope.anonymous()
        )
        ok = (
            len(small.nodes) == 1
            and small.total_nodes == public.total_nodes
            and small.truncated
        )
        failures += not ok
        print(f"  limit=1 -> nodes={len(small.nodes)} total={small.total_nodes} "
              f"truncated={small.truncated} {'ok' if ok else 'WRONG'}")

        # Every edge cut loose by the limit must be counted, and no edge
        # may survive with an endpoint that isn't in the response.
        present = {(n.id, n.table) for n in small.nodes}
        dangling = [
            e for e in small.edges
            if (e.source, e.source_table) not in present
            or (e.target, e.target_table) not in present
        ]
        failures += bool(dangling)
        print(f"  omitted_edges={small.omitted_edges}, dangling edges left in the "
              f"response={len(dangling)} {'ok' if not dangling else 'LEAK'}")

    print("\n== the focused subgraph route still agrees with the overview ==")
    if not public.nodes:
        print("  skipped: graph is empty")
    else:
        node = public.nodes[0]
        sub = await get_subgraph(
            node_id=node.id, depth=2, pool=pool, scope=AccessScope.anonymous()
        )
        overview_ids = {n.id for n in public.nodes}
        stray = [n.id for n in sub.nodes if n.id not in overview_ids]
        failures += bool(stray)
        print(f"  subgraph({node.label!r}, depth=2) -> {len(sub.nodes)} nodes, "
              f"{len(sub.edges)} edges")
        print(f"  nodes absent from the whole-graph view: {len(stray)} "
              f"{'ok' if not stray else 'INCONSISTENT'}")

    await pool.close()
    print(f"\n{'all checks passed' if not failures else f'{failures} check(s) FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
