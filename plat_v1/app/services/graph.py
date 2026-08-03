"""
Task graph persistence.

Everything that writes to `task_nodes`, `task_edges`, and `implementations`
lives here, so the invariants -- live rows are the ones with `t_invalid IS
NULL`, a plan's refs get bound to real ids exactly once -- are enforced in
one place rather than at each call site.

Note the JSONB columns are handed dicts, never `json.dumps(...)::jsonb`.
The connection has a JSONB codec registered (see app/db.py); pre-serialising
in Python double-encodes on write and corrupts every subsequent read.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence
from uuid import UUID

import asyncpg

from app.models.plan import Expansion, ImplementationSpec, Plan, PlanNode
from app.models.task import Implementation, TaskNode
from app.services.embeddings import Embedder, task_text, to_pgvector
from app.services.typecheck import TypecheckContext

log = logging.getLogger(__name__)


class TaskGraph:
    def __init__(self, pool: asyncpg.Pool, embedder: Optional[Embedder] = None):
        self._pool = pool
        self._embedder = embedder

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_task(self, task_id: UUID) -> Optional[TaskNode]:
        row = await self._pool.fetchrow(
            """
            SELECT id, name, description, kind, input_schema, output_schema,
                   success_criteria, cache_key, version, provenance, t_valid
            FROM task_nodes WHERE id = $1 AND t_invalid IS NULL
            """,
            task_id,
        )
        return TaskNode.from_row(row) if row else None

    async def get_task_by_name(self, name: str) -> Optional[TaskNode]:
        row = await self._pool.fetchrow(
            """
            SELECT id, name, description, kind, input_schema, output_schema,
                   success_criteria, cache_key, version, provenance, t_valid
            FROM task_nodes WHERE name = $1 AND t_invalid IS NULL
            """,
            name,
        )
        return TaskNode.from_row(row) if row else None

    async def list_tasks(self, search: Optional[str] = None, limit: int = 100) -> list[TaskNode]:
        if search:
            rows = await self._pool.fetch(
                """
                SELECT id, name, description, kind, input_schema, output_schema,
                       success_criteria, cache_key, version, provenance, t_valid
                FROM task_nodes
                WHERE t_invalid IS NULL AND (name ILIKE $1 OR description ILIKE $1)
                ORDER BY name LIMIT $2
                """,
                f"%{search}%",
                limit,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT id, name, description, kind, input_schema, output_schema,
                       success_criteria, cache_key, version, provenance, t_valid
                FROM task_nodes WHERE t_invalid IS NULL ORDER BY name LIMIT $1
                """,
                limit,
            )
        return [TaskNode.from_row(r) for r in rows]

    async def implementations_for(self, task_id: UUID) -> list[Implementation]:
        rows = await self._pool.fetch(
            """
            SELECT id, task_node_id, name, kind, spec, cost_estimate,
                   latency_estimate_ms, enabled
            FROM implementations WHERE task_node_id = $1 AND t_invalid IS NULL
            ORDER BY cost_estimate, latency_estimate_ms
            """,
            task_id,
        )
        return [Implementation.from_row(r) for r in rows]

    async def expansion_of(self, task_id: UUID) -> list[TaskNode]:
        """
        A composite's children, in execution order.

        Order comes from `properties->>'position'` on the DECOMPOSES_TO edge
        rather than insertion order: a six-stage workflow whose stages ran in
        whatever order the planner returned them would be a different
        workflow on every read.
        """
        rows = await self._pool.fetch(
            """
            SELECT t.id, t.name, t.description, t.kind, t.input_schema, t.output_schema,
                   t.success_criteria, t.cache_key, t.version, t.provenance, t.t_valid
            FROM task_edges e
            JOIN task_nodes t ON t.id = e.target_id AND t.t_invalid IS NULL
            WHERE e.source_id = $1 AND e.edge_type = 'DECOMPOSES_TO'
              AND e.t_invalid IS NULL
            ORDER BY COALESCE((e.properties->>'position')::int, 0), t.name
            """,
            task_id,
        )
        return [TaskNode.from_row(r) for r in rows]

    async def plan_for_task(self, task: TaskNode) -> Plan:
        """
        The plan that executes a matched task.

        A leaf is one node. A composite becomes one composite node carrying
        its expansion, so the same interface check the typechecker applies to
        a proposed composite also applies to a seeded one -- a workflow whose
        declared contract has drifted from its stages is caught before it
        runs, not during.
        """
        if task.kind != "composite":
            return plan_from_task(task)

        children = await self.expansion_of(task.id)
        if not children:
            return plan_from_task(task)

        inner = plan_from_tasks(children)
        node = PlanNode(
            ref="c1",
            name=task.name,
            description=task.description or "",
            kind="composite",
            input_schema=task.input_schema,
            output_schema=task.output_schema,
            success_criteria=task.success_criteria,
                cache_key=task.cache_key,
            existing_task_id=task.id,
            expansion=Expansion(nodes=inner.nodes, edges=inner.edges),
        )
        return Plan(
            nodes=[node],
            edges=[],
            external_inputs=inner.external_inputs,
            reasoning=f"matched composite task '{task.name}'",
        )

    async def load_typecheck_context(self, plan: Plan) -> TypecheckContext:
        """
        Resolve the outside-world facts the typechecker needs, in one query.

        Batched deliberately: `= ANY($1::uuid[])` rather than a lookup per
        referenced task. A twelve-node plan should cost one round trip, not
        twelve.
        """
        referenced: list[UUID] = []
        for node in plan.nodes:
            if node.existing_task_id:
                referenced.append(node.existing_task_id)
            for child in (node.expansion.nodes if node.expansion else []):
                if child.existing_task_id:
                    referenced.append(child.existing_task_id)
        if not referenced:
            return TypecheckContext.empty()

        rows = await self._pool.fetch(
            """
            SELECT t.id,
                   COUNT(i.id) FILTER (
                       WHERE i.enabled AND i.t_invalid IS NULL
                   ) AS enabled_count
            FROM task_nodes t
            LEFT JOIN implementations i ON i.task_node_id = t.id
            WHERE t.id = ANY($1::uuid[]) AND t.t_invalid IS NULL
            GROUP BY t.id
            """,
            list(set(referenced)),
        )
        return TypecheckContext(
            implementation_counts={r["id"]: int(r["enabled_count"]) for r in rows},
            known_task_ids=frozenset(r["id"] for r in rows),
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def _embed(self, name: str, description: Optional[str]) -> Optional[str]:
        if self._embedder is None:
            return None
        try:
            vector = await self._embedder.embed_one(task_text(name, description))
            return to_pgvector(vector)
        except Exception as exc:  # noqa: BLE001
            # A task written without an embedding is retrievable lexically and
            # invisible to vector search. Degraded, not broken -- but said out
            # loud, because a half-embedded graph searches badly forever.
            log.warning("no embedding for task %r (%s); it will be lexical-only", name, exc)
            return None

    async def create_task(
        self,
        conn: asyncpg.Connection,
        *,
        name: str,
        description: str = "",
        kind: str = "leaf",
        input_schema: Optional[dict] = None,
        output_schema: Optional[dict] = None,
        success_criteria: Optional[dict] = None,
        cache_key: Optional[list[str]] = None,
        provenance: str = "company_ingested",
        embedding: Optional[str] = None,
    ) -> UUID:
        # On a name collision the existing row wins and only its description
        # is refreshed -- deliberately, because silently rewriting a live
        # task's input_schema would change the contract of every plan already
        # bound to it. `persist_plan` checks for the collision beforehand and
        # refuses rather than letting a plan bind to a contract it was never
        # typechecked against.
        row = await conn.fetchrow(
            """
            INSERT INTO task_nodes (name, description, kind, input_schema, output_schema,
                                    success_criteria, cache_key, provenance, embedding)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::provenance_source,$9::vector)
            ON CONFLICT (name) WHERE t_invalid IS NULL
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
            """,
            name,
            description,
            kind,
            input_schema or {},
            output_schema or {},
            success_criteria or {},
            cache_key,
            provenance,
            embedding,
        )
        return row["id"]

    async def add_implementation(
        self,
        conn: asyncpg.Connection,
        task_id: UUID,
        spec: ImplementationSpec,
    ) -> UUID:
        row = await conn.fetchrow(
            """
            INSERT INTO implementations (task_node_id, name, kind, spec, cost_estimate,
                                         latency_estimate_ms, enabled)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (task_node_id, name) WHERE t_invalid IS NULL
            DO UPDATE SET spec = EXCLUDED.spec,
                          cost_estimate = EXCLUDED.cost_estimate,
                          latency_estimate_ms = EXCLUDED.latency_estimate_ms,
                          enabled = EXCLUDED.enabled
            RETURNING id
            """,
            task_id,
            spec.name,
            spec.kind,
            spec.spec,
            spec.cost_estimate,
            spec.latency_estimate_ms,
            spec.enabled,
        )
        return row["id"]

    async def add_edge(
        self,
        conn: asyncpg.Connection,
        edge_type: str,
        source_id: UUID,
        target_id: UUID,
        properties: Optional[dict] = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO task_edges (edge_type, source_id, source_table,
                                    target_id, target_table, properties)
            VALUES ($1::task_edge_type, $2, 'task_nodes', $3, 'task_nodes', $4)
            ON CONFLICT (edge_type, source_id, target_id) WHERE t_invalid IS NULL
            DO UPDATE SET properties = EXCLUDED.properties
            """,
            edge_type,
            source_id,
            target_id,
            properties or {},
        )

    async def _reject_name_collisions(self, plan: Plan) -> None:
        """
        Refuse to persist a new node whose name a live task already holds.

        Approval validated a specific contract. Binding to an existing task
        keeps *that* task's input_schema and output_schema, so quietly
        proceeding would execute a plan against schemas the typechecker never
        saw. Batched into one query rather than one per node.
        """
        fresh = [n for n in _iter_nodes(plan) if n.existing_task_id is None]
        if not fresh:
            return

        # Two new nodes sharing a name is the same bug one level in, and it
        # never reaches the database to be caught: `ON CONFLICT (name)` would
        # return the first node's id for the second, silently collapsing two
        # refs onto one task -- and their identically-named `model_fallback`
        # implementations would then overwrite each other's output schema.
        # Typecheck only enforces unique *refs*, not unique names.
        seen: dict[str, str] = {}
        duplicates = []
        for node in fresh:
            if node.name in seen:
                duplicates.append(
                    f"'{node.name}' (refs '{seen[node.name]}' and '{node.ref}')"
                )
            seen[node.name] = node.ref
        if duplicates:
            raise ValueError(
                "the plan proposes two new tasks with the same name, which would "
                "collapse into one: " + "; ".join(duplicates)
            )

        new_nodes = {n.name: n for n in fresh}
        rows = await self._pool.fetch(
            """
            SELECT id, name, input_schema, output_schema FROM task_nodes
            WHERE name = ANY($1::text[]) AND t_invalid IS NULL
            """,
            list(new_nodes),
        )

        collisions = []
        for row in rows:
            node = new_nodes[row["name"]]
            if _interface(row["input_schema"], row["output_schema"]) != _interface(
                node.input_schema, node.output_schema
            ):
                collisions.append(
                    f"'{row['name']}' (existing task {row['id']} declares a different "
                    f"interface; reuse it with existing_task_id, or rename the new node)"
                )

        if collisions:
            raise ValueError(
                "the plan proposes new tasks whose names are already taken by live "
                "tasks with different interfaces: " + "; ".join(collisions)
            )

    async def _hydrate_bound_nodes(self, plan: Plan) -> None:
        """
        Fill in `cache_key` for nodes that reuse an existing task.

        The decomposer produces reuse nodes carrying only an id -- it has no
        way to know a task's cache_key, and `persist_plan` skips bound nodes
        entirely. Left unset, the executor would fingerprint every input of a
        reused task, which on `map_to_schema` means keying the cache on the
        cell values: the exact miss `cache_key` exists to prevent, on the one
        stage that costs a model call.

        Worse than a miss, actually. `_first_layout_gate` probes with the same
        fingerprint, so a never-repeating key means an implementation marked
        `first_layout_requires_review` is blocked forever on this path. And
        one task would end up with two disjoint fingerprint populations,
        so entries written by the matched path would be invisible here.
        """
        bound = [n for n in _iter_nodes(plan) if n.existing_task_id and n.cache_key is None]
        if not bound:
            return

        rows = await self._pool.fetch(
            "SELECT id, cache_key FROM task_nodes "
            "WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL",
            list({n.existing_task_id for n in bound}),
        )
        by_id = {r["id"]: r["cache_key"] for r in rows}
        for node in bound:
            node.cache_key = by_id.get(node.existing_task_id)

    async def persist_plan(self, plan: Plan, provenance: str = "company_debate") -> Plan:
        """
        Bind every node in an approved plan to a real task node.

        Nodes that name an `existing_task_id` are left alone -- that is the
        whole point of reuse. Everything else is created, along with its
        proposed implementations and the plan's edges. One transaction: a
        half-applied plan would leave orphan tasks that nothing references and
        that the next decomposition would then retrieve as prior art.
        """
        # Embeddings involve a network call and must not happen inside the
        # transaction; a slow embedding provider would otherwise hold a write
        # lock on task_nodes for its duration.
        # A new node whose name is already taken by a live task would bind to
        # that task on ON CONFLICT and inherit *its* schemas -- while the plan
        # was typechecked against the ones the node declared. Execution would
        # then run against a contract nothing validated. Refuse instead; the
        # author should either reuse the task explicitly via existing_task_id
        # or pick a different name.
        await self._reject_name_collisions(plan)
        await self._hydrate_bound_nodes(plan)

        # Embeddings involve a network call and must not happen inside the
        # transaction; a slow embedding provider would otherwise hold a write
        # lock on task_nodes for its duration.
        embeddings: dict[str, Optional[str]] = {}
        for node in _iter_nodes(plan):
            if node.existing_task_id is None:
                embeddings[node.ref] = await self._embed(node.name, node.description)

        async with self._pool.acquire() as conn, conn.transaction():
            ref_to_id: dict[str, UUID] = {}

            for node in _iter_nodes(plan):
                if node.existing_task_id is not None:
                    ref_to_id[node.ref] = node.existing_task_id
                    continue
                task_id = await self.create_task(
                    conn,
                    name=node.name,
                    description=node.description,
                    kind=node.kind,
                    input_schema=node.input_schema,
                    output_schema=node.output_schema,
                    success_criteria=node.success_criteria,
                    cache_key=node.cache_key,
                    provenance=provenance,
                    embedding=embeddings.get(node.ref),
                )
                ref_to_id[node.ref] = task_id
                node.existing_task_id = task_id
                for impl in node.implementations:
                    await self.add_implementation(conn, task_id, impl)

            for edge in plan.edges:
                source, target = ref_to_id.get(edge.source_ref), ref_to_id.get(edge.target_ref)
                if source and target:
                    await self.add_edge(conn, edge.type, source, target)

            for node in plan.nodes:
                if not node.expansion:
                    continue
                parent = ref_to_id.get(node.ref)
                for child in node.expansion.nodes:
                    child_id = ref_to_id.get(child.ref)
                    if parent and child_id:
                        await self.add_edge(conn, "DECOMPOSES_TO", parent, child_id)
                for edge in node.expansion.edges:
                    source = ref_to_id.get(edge.source_ref)
                    target = ref_to_id.get(edge.target_ref)
                    if source and target:
                        await self.add_edge(conn, edge.type, source, target)

        return plan


def _interface(input_schema: dict, output_schema: dict) -> tuple:
    """
    A comparable projection of a task's contract.

    Raw dict equality is the wrong test. It trips on things that carry no
    contractual meaning -- per-property `description` prose, which the
    decomposer copies straight from the model, and `required` list order,
    which is sorted by the decomposer and hand-written elsewhere. A
    re-proposal of the same workflow with reworded descriptions would be a
    hard 409 the operator could only escape by renaming the node.

    Name and declared type, plus the required set, is what a caller actually
    depends on and what the typechecker itself compares.
    """

    def shape(schema: dict) -> tuple:
        props = (schema or {}).get("properties") or {}
        typed = tuple(sorted((k, str((v or {}).get("type"))) for k, v in props.items()))
        required = (schema or {}).get("required")
        return typed, frozenset(required if required is not None else props.keys())

    return shape(input_schema), shape(output_schema)


def _iter_nodes(plan: Plan) -> Sequence[PlanNode]:
    out: list[PlanNode] = []
    for node in plan.nodes:
        out.append(node)
        if node.expansion:
            out.extend(node.expansion.nodes)
    return out


def plan_from_task(task: TaskNode, ref: str = "n1") -> Plan:
    """
    The single-node plan a matched task executes as.

    Match and decompose converge here: both hand the executor a Plan, so
    there is one execution path rather than a fast one and a general one that
    drift apart.
    """
    external = list((task.input_schema.get("properties") or {}).keys())
    return Plan(
        nodes=[
            PlanNode(
                ref=ref,
                name=task.name,
                description=task.description or "",
                kind=task.kind,
                input_schema=task.input_schema,
                output_schema=task.output_schema,
                success_criteria=task.success_criteria,
                cache_key=task.cache_key,
                existing_task_id=task.id,
            )
        ],
        edges=[],
        external_inputs=external,
        reasoning=f"matched existing task '{task.name}'",
    )


def plan_from_tasks(tasks: list[TaskNode]) -> Plan:
    """
    Chain a sequence of existing tasks into one plan.

    Used by the seed script and the end-to-end test to express the PDF ->
    Excel workflow without hand-writing refs and edges. Edges are PRODUCES
    where the upstream output and downstream input share a property name, and
    REQUIRES otherwise -- an ordering dependency with no data attached is a
    real thing (classify before detect) and modelling it as PRODUCES would be
    a lie the typechecker correctly rejects.
    """
    from app.models.plan import PlanEdge

    nodes = [
        PlanNode(
            ref=f"n{i + 1}",
            name=t.name,
            description=t.description or "",
            kind=t.kind,
            input_schema=t.input_schema,
            output_schema=t.output_schema,
            success_criteria=t.success_criteria,
            cache_key=t.cache_key,
            existing_task_id=t.id,
        )
        for i, t in enumerate(tasks)
    ]

    edges: list[PlanEdge] = []
    external: list[str] = []
    produced: set[str] = set()

    for index, node in enumerate(nodes):
        needs = set((node.input_schema.get("properties") or {}).keys())
        satisfied: set[str] = set()
        for earlier in nodes[:index]:
            shared = needs & set((earlier.output_schema.get("properties") or {}).keys())
            if shared:
                edges.append(PlanEdge(type="PRODUCES", source_ref=earlier.ref, target_ref=node.ref))
                satisfied |= shared
        for name in sorted(needs - satisfied):
            if name not in external:
                external.append(name)
        if index > 0 and not satisfied:
            edges.append(
                PlanEdge(type="REQUIRES", source_ref=nodes[index - 1].ref, target_ref=node.ref)
            )
        produced |= set((node.output_schema.get("properties") or {}).keys())

    return Plan(nodes=nodes, edges=edges, external_inputs=external)
