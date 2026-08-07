"""
Graph endpoints for visualization (task decomposition add-on).

Two shapes, both consumable by a frontend node-graph library (React
Flow, mermaid, etc.) without transformation:

  GET /v1/graph            -- the whole visible graph, for browsing
  GET /v1/graph/{node_id}  -- a subgraph centred on one node, for focus

The subgraph route reuses GraphStore.traverse_from directly -- no new
graph logic. The overview route cannot: traversal starts from
entrypoints, and "show me everything" has no entrypoint, so it reads the
node tables directly. It still goes through the same visibility
predicate, which is the invariant that actually matters (see
services/access.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
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


class OverviewNode(GraphNode):
    """
    A node in the whole-graph view: `GraphNode` plus what a browsing view
    needs and a focused subgraph doesn't. Deliberately a superset, so the
    same frontend component renders either response.

    `node_type` is the knowledge node's own type ('person', 'policy',
    ...) and the literal 'task' for task nodes -- one field to colour or
    group by, rather than making the client special-case the table name.
    """

    node_type: str
    description: Optional[str] = None
    provenance: str
    current: bool
    t_created: datetime


class OverviewEdge(GraphEdgeOut):
    source_table: str
    target_table: str
    edge_type: str
    current: bool


class GraphOverviewResponse(BaseModel):
    nodes: list[OverviewNode]
    edges: list[OverviewEdge]
    # Totals are counted before the limit is applied, so a truncated view
    # can say what it is instead of quietly looking like the whole graph.
    total_nodes: int
    total_edges: int
    truncated: bool
    # Edges dropped because an endpoint isn't in `nodes` -- invisible to
    # this viewer, or cut off by the limit. Reported rather than hidden:
    # a silently missing link reads as "these steps aren't connected".
    omitted_edges: int


@router.get("", response_model=GraphOverviewResponse)
async def get_whole_graph(
    limit: int = Query(default=400, ge=1, le=2000),
    include_superseded: bool = Query(
        default=False,
        description="Include nodes and edges whose validity window has closed.",
    ),
    pool=Depends(get_pool),
    scope: AccessScope = Depends(get_scope),
) -> GraphOverviewResponse:
    """
    The whole graph the caller may see, for a browse-everything view.

    Unbounded by nature, so it is bounded explicitly: `limit` is a budget
    shared across both node tables, filled oldest-first so repeat loads
    return the same slice rather than reshuffling as new nodes arrive.
    """
    # $1 include_superseded, $2 limit, $3.. visibility. The same fragment
    # is interpolated into both UNION branches and reuses $3 -- asyncpg
    # allows a placeholder to appear more than once.
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)

    node_rows = await pool.fetch(
        f"""
        SELECT *, COUNT(*) OVER () AS total_matching FROM (
            SELECT id, 'task_nodes'::text AS node_table, name,
                   description, 'task'::text AS node_type,
                   provenance::text AS provenance, t_created, t_invalid
            FROM task_nodes
            WHERE ($1::bool OR t_invalid IS NULL) AND {vis_sql}
          UNION ALL
            SELECT id, 'knowledge_nodes'::text, name,
                   properties->>'description', node_type::text,
                   provenance::text, t_created, t_invalid
            FROM knowledge_nodes
            WHERE ($1::bool OR t_invalid IS NULL) AND {vis_sql}
        ) n
        ORDER BY t_created ASC, id ASC
        LIMIT $2
        """,
        include_superseded, limit, *vis_params,
    )

    nodes = [
        OverviewNode(
            id=r["id"],
            table=r["node_table"],
            label=r["name"],
            node_type=r["node_type"],
            description=r["description"],
            provenance=r["provenance"],
            current=r["t_invalid"] is None,
            t_created=r["t_created"],
        )
        for r in node_rows
    ]
    total_nodes = node_rows[0]["total_matching"] if node_rows else 0

    edge_rows = await pool.fetch(
        f"""
        SELECT id, edge_type::text AS edge_type, custom_edge_type,
               source_id, source_table, target_id, target_table, t_invalid,
               COUNT(*) OVER () AS total_matching
        FROM edges
        WHERE ($1::bool OR t_invalid IS NULL) AND {vis_sql}
        ORDER BY t_created ASC, id ASC
        LIMIT $2
        """,
        include_superseded, limit, *vis_params,
    )
    total_edges = edge_rows[0]["total_matching"] if edge_rows else 0

    present = {(n.id, n.table) for n in nodes}
    edges = [
        OverviewEdge(
            id=r["id"],
            source=r["source_id"],
            target=r["target_id"],
            label=r["custom_edge_type"] or r["edge_type"],
            edge_type=r["edge_type"],
            source_table=r["source_table"],
            target_table=r["target_table"],
            current=r["t_invalid"] is None,
        )
        for r in edge_rows
        if (r["source_id"], r["source_table"]) in present
        and (r["target_id"], r["target_table"]) in present
    ]

    return GraphOverviewResponse(
        nodes=nodes,
        edges=edges,
        total_nodes=total_nodes,
        total_edges=total_edges,
        truncated=len(nodes) < total_nodes or len(edge_rows) < total_edges,
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
