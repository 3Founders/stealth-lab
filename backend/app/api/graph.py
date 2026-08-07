"""
Graph read endpoints for visualization (task decomposition add-on).

Two views over the same stored graph:

  GET /v1/graph            the whole thing, for an overview
  GET /v1/graph/{node_id}  one node's neighbourhood, walked to a depth

The focused route reuses GraphStore.traverse_from directly -- no new
graph logic, just a response shape a frontend node-graph library (React
Flow, etc.) can consume without transformation. The overview cannot use
traverse_from: it has no centre to walk from, so it reads the node
tables directly.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import get_scope
from app.db.graph_store import GraphStore
from app.services.access import AccessScope, visibility_predicate

router = APIRouter(prefix="/v1/graph", tags=["graph"])


async def get_pool(request: Request):
    return request.app.state.pool


class GraphNode(BaseModel):
    id: UUID
    table: Literal["knowledge_nodes", "task_nodes"]
    label: str


class GraphEdgeOut(BaseModel):
    id: UUID
    source: UUID
    target: UUID
    label: str


class SubgraphResponse(BaseModel):
    center: UUID
    nodes: list[GraphNode]
    edges: list[GraphEdgeOut]


class GraphOverviewNode(GraphNode):
    """
    A node in the whole-graph view. Supersets GraphNode so anything that
    renders a subgraph renders these too.
    """

    # Knowledge nodes carry their own type ('person', 'policy', ...);
    # task nodes have no type column, so they get the literal 'task'.
    node_type: str
    description: str | None
    provenance: str
    # False once t_invalid is set: superseded, not deleted. The row stays
    # readable because the history is the point of a bitemporal store.
    current: bool
    t_created: str


class GraphOverviewEdge(GraphEdgeOut):
    source_table: Literal["knowledge_nodes", "task_nodes"]
    target_table: Literal["knowledge_nodes", "task_nodes"]
    edge_type: str
    current: bool


class GraphOverview(BaseModel):
    nodes: list[GraphOverviewNode]
    edges: list[GraphOverviewEdge]
    # Counted before the limit was applied, so a caller can tell a small
    # graph from a truncated view of a large one.
    total_nodes: int
    total_edges: int
    truncated: bool
    # Edges dropped because an endpoint isn't in `nodes` -- invisible to
    # this viewer, or cut by the limit. Reported rather than silently
    # dropped: a missing link reads as "these steps aren't connected".
    omitted_edges: int


@router.get("", response_model=GraphOverview)
async def get_whole_graph(
    limit: int = Query(default=400, ge=1, le=2000),
    include_superseded: bool = Query(default=False),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> GraphOverview:
    """
    The stored graph as stored -- every node and edge the viewer may see.

    Both node tables are unioned rather than queried separately so the
    limit applies to the graph as a whole; two independent limits would
    quietly favour whichever table was read first.
    """
    # $1 = include_superseded, $2 = limit, $3 = viewer id (signed-in
    # scopes only). The visibility predicate is interpolated into both
    # branches of the union and reuses $3 in each -- asyncpg numbers
    # placeholders by position, not by occurrence, so one parameter can
    # serve both branches.
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)

    node_rows = await pool.fetch(
        f"""
        SELECT g.*, COUNT(*) OVER () AS total_nodes
        FROM (
            SELECT id,
                   'task_nodes' AS "table",
                   name AS label,
                   'task' AS node_type,
                   description,
                   provenance::text AS provenance,
                   (t_invalid IS NULL) AS current,
                   t_created
            FROM task_nodes
            WHERE ($1 OR t_invalid IS NULL) AND {vis_sql}
            UNION ALL
            SELECT id,
                   'knowledge_nodes' AS "table",
                   name AS label,
                   node_type,
                   NULL AS description,
                   provenance::text AS provenance,
                   (t_invalid IS NULL) AS current,
                   t_created
            FROM knowledge_nodes
            WHERE ($1 OR t_invalid IS NULL) AND {vis_sql}
        ) g
        ORDER BY g.t_created DESC, g.id
        LIMIT $2
        """,
        include_superseded,
        limit,
        *vis_params,
    )

    # COUNT(*) OVER () is evaluated before LIMIT, so this is the size of
    # the whole visible graph even when the rows returned are a slice of
    # it. An empty result means an empty graph, hence the 0 fallback.
    total_nodes = node_rows[0]["total_nodes"] if node_rows else 0

    nodes = [
        GraphOverviewNode(
            id=r["id"],
            table=r["table"],
            label=r["label"],
            node_type=r["node_type"],
            description=r["description"],
            provenance=r["provenance"],
            current=r["current"],
            t_created=r["t_created"].isoformat(),
        )
        for r in node_rows
    ]

    edge_vis_sql, edge_vis_params = visibility_predicate(scope, param_index=2)
    edge_rows = await pool.fetch(
        f"""
        SELECT id, source_id, source_table, target_id, target_table,
               edge_type::text AS edge_type, custom_edge_type,
               (t_invalid IS NULL) AS current
        FROM edges
        WHERE ($1 OR t_invalid IS NULL) AND {edge_vis_sql}
        """,
        include_superseded,
        *edge_vis_params,
    )

    # Edges are matched on (id, table): ids are unique per table, not
    # across the pair, so matching on id alone could join a task node to
    # a knowledge node that happens to share it.
    present = {(n.id, n.table) for n in nodes}
    edges = [
        GraphOverviewEdge(
            id=r["id"],
            source=r["source_id"],
            target=r["target_id"],
            label=r["custom_edge_type"] or r["edge_type"],
            source_table=r["source_table"],
            target_table=r["target_table"],
            edge_type=r["edge_type"],
            current=r["current"],
        )
        for r in edge_rows
        if (r["source_id"], r["source_table"]) in present
        and (r["target_id"], r["target_table"]) in present
    ]

    return GraphOverview(
        nodes=nodes,
        edges=edges,
        total_nodes=total_nodes,
        total_edges=len(edge_rows),
        truncated=len(nodes) < total_nodes,
        omitted_edges=len(edge_rows) - len(edges),
    )


@router.get("/{node_id}", response_model=SubgraphResponse)
async def get_subgraph(
    node_id: UUID,
    depth: int = Query(default=2, ge=1, le=4),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> SubgraphResponse:
    graph = GraphStore(pool, scope=scope)

    # Figure out which table the center node lives in -- callers shouldn't
    # need to know this ahead of time.
    table = None
    for candidate in ("task_nodes", "knowledge_nodes"):
        if await graph.node_exists(node_id, candidate):
            table = candidate
            break
    if table is None:
        raise HTTPException(404, "node not found")

    edges = await graph.traverse_from([node_id], table, max_depth=depth)

    node_ids: dict[UUID, str] = {node_id: table}
    for e in edges:
        node_ids[e.source_id] = e.source_table
        node_ids[e.target_id] = e.target_table

    nodes: list[GraphNode] = []
    for nid, ntable in node_ids.items():
        vis_sql, vis_params = visibility_predicate(scope, param_index=2)
        row = await pool.fetchrow(
            f"SELECT name FROM {ntable} WHERE id = $1 AND {vis_sql}", nid, *vis_params
        )
        # A node the viewer can't see is omitted rather than labelled
        # "?" -- a placeholder would still reveal that something exists.
        if row:
            nodes.append(GraphNode(id=nid, table=ntable, label=row["name"]))

    return SubgraphResponse(
        center=node_id,
        nodes=nodes,
        edges=[
            GraphEdgeOut(id=e.id, source=e.source_id, target=e.target_id,
                        label=e.custom_edge_type or e.edge_type)
            for e in edges
        ],
    )
