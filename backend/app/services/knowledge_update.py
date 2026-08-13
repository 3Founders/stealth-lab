"""
Applying an approved ChangeSet to the instance graph
(MVP plan, Sections 3.1 and 9).

The only write path into the graph from an approval. Two invariants it
exists to guarantee:

  1. Nothing is destroyed. An "update" closes the old row's validity
     window and appends a new one. History stays queryable, which is what
     makes point-in-time reconstruction and audit possible at all.

  2. All-or-nothing. The whole change set applies in one transaction. A
     partially-applied change set would leave the graph describing a
     workflow that was never approved in that form -- worse than either
     applying it or not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.services.embeddings import Embedder, node_text, to_pgvector

from app.models.change import (
    ChangeSet,
    CreateEdgeOp,
    CreateKnowledgeNodeOp,
    CreateTaskNodeOp,
    InvalidateEdgeOp,
    UpdateKnowledgeNodeOp,
    UpdateTaskNodeOp,
)

log = logging.getLogger(__name__)

# Columns copied forward when superseding a task_node. Excludes id and the
# temporal columns (regenerated) -- everything else carries over unless the
# change set overrides it.
_TASK_CARRY_FORWARD = (
    "tenant_id", "name", "description", "io_schema", "skill_ref",
    "success_criteria", "cost_estimate", "latency_estimate_ms",
    "pert_optimistic_ms", "pert_likely_ms", "pert_pessimistic_ms", "embedding",
)

# Same idea for knowledge_nodes. node_type is deliberately carried forward
# but never overridable via `changes` (see MUTABLE_KNOWLEDGE_FIELDS) --
# supersession changes content, not classification.
_KNOWLEDGE_CARRY_FORWARD = ("tenant_id", "node_type", "name", "properties", "embedding")


class ChangeApplicationError(Exception):
    pass


class KnowledgeUpdater:
    def __init__(self, pool: asyncpg.Pool, embedder: Optional[Embedder] = None):
        self._pool = pool
        # Real, confirmed bug: 'embedding' is in both carry-forward tuples
        # above, meaning a superseding row's embedding was always copied
        # UNCHANGED from the row it replaces -- even when op.changes
        # rewrites the actual content that embedding is supposed to
        # represent. Found for real: a debate-merged node's superseded
        # pointer ("Superseded -- see <id>") kept scoring 0.65 similarity
        # on real semantic queries, because its embedding still reflected
        # the OLD, pre-supersession trajectory text, not its new,
        # unrelated-to-anything pointer text. Lazily constructed so
        # existing callers that never change embeddable content (most
        # ops) pay nothing extra.
        self._embedder = embedder

    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder()
        return self._embedder

    async def apply_generated(
        self,
        change_set: ChangeSet,
        approver_id: str,
        at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Apply an approved decomposition from untrusted public input.

        Separate from `apply()` deliberately, and stricter in two ways:

          - It re-runs `validate_generative()`, not just `validate_ops()`.
            The capability check ran at generation time; running it again
            at apply time means a proposal that was tampered with in
            storage still cannot escalate. Validating once and trusting
            the stored artifact would make the database a trust boundary
            it isn't designed to be.

          - Everything it writes is tagged `public_generated`, so the
            graph never loses track of which content came from an
            anonymous submission versus the company's own documents.

        Refs are resolved to real ids inside the transaction, so an edge
        can reference a node created microseconds earlier in the same set.
        """
        problems = change_set.validate_generative()
        if problems:
            raise ChangeApplicationError(
                "refusing to apply a generated change set that fails the "
                "capability check: " + "; ".join(problems)
            )

        now = at or datetime.now(timezone.utc)
        ref_to_id: dict[str, UUID] = {}
        applied: list[dict[str, Any]] = []

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Nodes first, so refs resolve before any edge needs them.
                for op in change_set.ops:
                    if isinstance(op, CreateTaskNodeOp):
                        row = await conn.fetchrow(
                            "INSERT INTO task_nodes (name, description, io_schema, "
                            "skill_ref, success_criteria, provenance, created_by) "
                            "VALUES ($1,$2,$3,$4,$5,'public_generated',$6) RETURNING id",
                            op.name, op.description, op.io_schema,
                            op.skill_ref, op.success_criteria, approver_id,
                        )
                        ref_to_id[op.ref] = row["id"]
                        applied.append({
                            "op": "create_task_node", "ref": op.ref,
                            "id": str(row["id"]), "name": op.name,
                        })
                    elif isinstance(op, CreateKnowledgeNodeOp):
                        row = await conn.fetchrow(
                            "INSERT INTO knowledge_nodes (node_type, name, properties, "
                            "provenance, created_by) "
                            "VALUES ($1,$2,$3,'public_generated',$4) RETURNING id",
                            op.node_type, op.name, op.properties, approver_id,
                        )
                        ref_to_id[op.ref] = row["id"]
                        applied.append({
                            "op": "create_knowledge_node", "ref": op.ref,
                            "id": str(row["id"]), "name": op.name,
                        })

                for op in change_set.ops:
                    if not isinstance(op, CreateEdgeOp):
                        continue
                    source_id = op.source_id or ref_to_id.get(op.source_ref or "")
                    target_id = op.target_id or ref_to_id.get(op.target_ref or "")
                    if source_id is None or target_id is None:
                        # validate_generative() should have caught this;
                        # failing loudly here rather than writing a
                        # dangling edge if it somehow didn't.
                        raise ChangeApplicationError(
                            f"edge references an unresolved node "
                            f"(source_ref={op.source_ref!r}, target_ref={op.target_ref!r})"
                        )
                    row = await conn.fetchrow(
                        "INSERT INTO edges (edge_type, custom_edge_type, source_id, "
                        "source_table, target_id, target_table, properties, "
                        "provenance, t_valid, t_created, created_by) "
                        "VALUES ($1::edge_type,$2,$3,$4,$5,$6,$7,'public_generated',"
                        "$8,$8,$9) RETURNING id",
                        op.edge_type, op.custom_edge_type, source_id, op.source_table,
                        target_id, op.target_table, op.properties, now, approver_id,
                    )
                    applied.append({"op": "create_edge", "id": str(row["id"])})

        return {
            "applied": applied,
            "refs": {ref: str(node_id) for ref, node_id in ref_to_id.items()},
        }

    async def apply(
        self,
        change_set: ChangeSet,
        approver_id: str,
        at: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """
        Apply every op in one transaction. Returns a record of what was
        written, which is stored on the approval row so the audit trail
        reflects what actually happened rather than what was intended.
        """
        problems = change_set.validate_ops()
        if problems:
            raise ChangeApplicationError(
                "refusing to apply an invalid change set: " + "; ".join(problems)
            )

        now = at or datetime.now(timezone.utc)
        applied: list[dict[str, Any]] = []

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for op in change_set.ops:
                    if isinstance(op, InvalidateEdgeOp):
                        applied.append(await self._invalidate_edge(conn, op, now, approver_id))
                    elif isinstance(op, CreateEdgeOp):
                        applied.append(await self._create_edge(conn, op, now, approver_id))
                    elif isinstance(op, UpdateTaskNodeOp):
                        applied.append(await self._supersede_task(conn, op, now, approver_id))
                    elif isinstance(op, UpdateKnowledgeNodeOp):
                        applied.append(await self._supersede_knowledge(conn, op, now, approver_id))
                    else:  # pragma: no cover -- discriminated union is exhaustive
                        raise ChangeApplicationError(f"unknown op type: {op!r}")
        return applied

    async def _invalidate_edge(self, conn, op: InvalidateEdgeOp, now, approver) -> dict:
        result = await conn.execute(
            "UPDATE edges SET t_invalid = $2, t_expired = $2 "
            "WHERE id = $1 AND t_invalid IS NULL",
            op.edge_id, now,
        )
        if not result.endswith(" 1"):
            raise ChangeApplicationError(
                f"edge {op.edge_id} not found or already invalidated -- "
                "the graph changed between proposal and approval"
            )
        return {"op": "invalidate_edge", "edge_id": str(op.edge_id), "at": now.isoformat()}

    async def _create_edge(self, conn, op: CreateEdgeOp, now, approver) -> dict:
        for node_id, table in ((op.source_id, op.source_table), (op.target_id, op.target_table)):
            # Polymorphic edges can't carry FK constraints (see 01_ontology.sql),
            # so referential integrity is enforced here instead.
            exists = await conn.fetchrow(
                f"SELECT 1 FROM {table} WHERE id = $1 AND t_invalid IS NULL", node_id
            )
            if exists is None:
                raise ChangeApplicationError(
                    f"cannot create edge: {table} {node_id} does not exist or is invalidated"
                )

        row = await conn.fetchrow(
            "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
            "VALUES ($1::edge_type, $2, $3, $4, $5, $6, $7, 'company_debate', $8, $8, $9) "
            "RETURNING id",
            op.edge_type, op.custom_edge_type, op.source_id, op.source_table,
            op.target_id, op.target_table, op.properties, now, approver,
        )
        return {"op": "create_edge", "edge_id": str(row["id"]), "at": now.isoformat()}

    async def _supersede_task(self, conn, op: UpdateTaskNodeOp, now, approver) -> dict:
        old = await conn.fetchrow(
            "SELECT * FROM task_nodes WHERE id = $1 AND t_invalid IS NULL FOR UPDATE",
            op.task_node_id,
        )
        if old is None:
            raise ChangeApplicationError(
                f"task_node {op.task_node_id} not found or already superseded"
            )

        merged: dict[str, Any] = {c: old[c] for c in _TASK_CARRY_FORWARD}
        merged.update(op.changes)

        # Same real fix as _supersede_knowledge, applied preventively here
        # -- no real evidence yet of this specific case occurring (unlike
        # the knowledge_nodes case, which was directly observed), but the
        # structural bug (embedding blindly carried forward even when the
        # embedded content changes) is identical. Re-embeds using the
        # SAME name+description convention every other real task_node
        # embedding uses (node_text, embeddings.py) -- not a different
        # or invented one.
        if "name" in op.changes or "description" in op.changes:
            new_name = merged.get("name")
            new_description = merged.get("description")
            if isinstance(new_name, str) and new_name.strip():
                embedder = self._get_embedder()
                merged["embedding"] = to_pgvector(
                    await embedder.embed_one(
                        node_text(new_name, new_description), input_type="document"
                    )
                )

        cols = list(_TASK_CARRY_FORWARD)
        values = [merged[c] for c in cols]
        placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
        n = len(cols)
        new_row = await conn.fetchrow(
            f"INSERT INTO task_nodes ({', '.join(cols)}, provenance, t_valid, t_created, created_by) "
            f"VALUES ({placeholders}, 'company_debate', ${n+1}, ${n+1}, ${n+2}) RETURNING id",
            *values, now, approver,
        )
        new_id = new_row["id"]

        await conn.execute(
            "UPDATE task_nodes SET t_invalid = $2, t_expired = $2 WHERE id = $1",
            op.task_node_id, now,
        )
        # SUPERSEDES links the versions so history is walkable from either end.
        await conn.execute(
            "INSERT INTO edges (edge_type, source_id, source_table, target_id, target_table, "
            "properties, provenance, t_valid, t_created, created_by) "
            "VALUES ('SUPERSEDES', $1, 'task_nodes', $2, 'task_nodes', $3, "
            "'company_debate', $4, $4, $5)",
            new_id, op.task_node_id, {"reason": op.reason}, now, approver,
        )

        # Edges pointing at the old version must follow it forward, or the
        # new version arrives orphaned and the workflow silently breaks.
        rewired = await conn.fetch(
            "SELECT id, edge_type::text AS edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, properties FROM edges "
            "WHERE t_invalid IS NULL AND edge_type <> 'SUPERSEDES' "
            "AND ((source_id = $1 AND source_table = 'task_nodes') "
            "  OR (target_id = $1 AND target_table = 'task_nodes'))",
            op.task_node_id,
        )
        for e in rewired:
            src = new_id if (e["source_id"] == op.task_node_id and e["source_table"] == "task_nodes") else e["source_id"]
            tgt = new_id if (e["target_id"] == op.task_node_id and e["target_table"] == "task_nodes") else e["target_id"]
            props = e["properties"] if isinstance(e["properties"], dict) else {}
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
                "VALUES ($1::edge_type, $2, $3, $4, $5, $6, $7, 'company_debate', $8, $8, $9)",
                e["edge_type"], e["custom_edge_type"], src, e["source_table"],
                tgt, e["target_table"], props, now, approver,
            )
            await conn.execute(
                "UPDATE edges SET t_invalid = $2, t_expired = $2 WHERE id = $1", e["id"], now
            )

        return {
            "op": "update_task_node",
            "old_id": str(op.task_node_id),
            "new_id": str(new_id),
            "rewired_edges": len(rewired),
            "at": now.isoformat(),
        }

    async def _supersede_knowledge(self, conn, op: UpdateKnowledgeNodeOp, now, approver) -> dict:
        """
        Mirrors _supersede_task exactly, on knowledge_nodes. Previously
        there was no update path for this table at all -- knowledge_nodes
        were read-only reference material a debate could cite but never
        itself revise, which is what made "debate resolves a policy
        conflict" impossible to actually apply even if a panel proposed it.
        """
        old = await conn.fetchrow(
            "SELECT * FROM knowledge_nodes WHERE id = $1 AND t_invalid IS NULL FOR UPDATE",
            op.knowledge_node_id,
        )
        if old is None:
            raise ChangeApplicationError(
                f"knowledge_node {op.knowledge_node_id} not found or already superseded"
            )

        merged: dict[str, Any] = {c: old[c] for c in _KNOWLEDGE_CARRY_FORWARD}
        merged.update(op.changes)

        # Real fix: if this op is changing properties, and the new
        # properties has real 'content', re-embed it -- don't blindly
        # carry the old embedding forward to represent new text it was
        # never computed from. Only re-embeds when 'content' is present
        # in the NEW properties specifically -- an op that only touches
        # e.g. postconditions, leaving content untouched, correctly
        # keeps the existing embedding as-is.
        if "properties" in op.changes:
            new_content = (op.changes.get("properties") or {}).get("content")
            if isinstance(new_content, str) and new_content.strip():
                embedder = self._get_embedder()
                merged["embedding"] = to_pgvector(
                    await embedder.embed_one(new_content, input_type="document")
                )

        cols = list(_KNOWLEDGE_CARRY_FORWARD)
        values = [merged[c] for c in cols]
        placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
        n = len(cols)
        new_row = await conn.fetchrow(
            f"INSERT INTO knowledge_nodes ({', '.join(cols)}, provenance, t_valid, t_created, created_by) "
            f"VALUES ({placeholders}, 'company_debate', ${n+1}, ${n+1}, ${n+2}) RETURNING id",
            *values, now, approver,
        )
        new_id = new_row["id"]

        await conn.execute(
            "UPDATE knowledge_nodes SET t_invalid = $2, t_expired = $2 WHERE id = $1",
            op.knowledge_node_id, now,
        )
        await conn.execute(
            "INSERT INTO edges (edge_type, source_id, source_table, target_id, target_table, "
            "properties, provenance, t_valid, t_created, created_by) "
            "VALUES ('SUPERSEDES', $1, 'knowledge_nodes', $2, 'knowledge_nodes', $3, "
            "'company_debate', $4, $4, $5)",
            new_id, op.knowledge_node_id, {"reason": op.reason}, now, approver,
        )

        # Edges pointing at the old version (including the proxy
        # reconciliation task's CONFLICTS_WITH edges -- see
        # knowledge_conflict.py) must follow it forward.
        rewired = await conn.fetch(
            "SELECT id, edge_type::text AS edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, properties FROM edges "
            "WHERE t_invalid IS NULL AND edge_type <> 'SUPERSEDES' "
            "AND ((source_id = $1 AND source_table = 'knowledge_nodes') "
            "  OR (target_id = $1 AND target_table = 'knowledge_nodes'))",
            op.knowledge_node_id,
        )
        for e in rewired:
            src = new_id if (e["source_id"] == op.knowledge_node_id and e["source_table"] == "knowledge_nodes") else e["source_id"]
            tgt = new_id if (e["target_id"] == op.knowledge_node_id and e["target_table"] == "knowledge_nodes") else e["target_id"]
            props = e["properties"] if isinstance(e["properties"], dict) else {}
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
                "VALUES ($1::edge_type, $2, $3, $4, $5, $6, $7, 'company_debate', $8, $8, $9)",
                e["edge_type"], e["custom_edge_type"], src, e["source_table"],
                tgt, e["target_table"], props, now, approver,
            )
            await conn.execute(
                "UPDATE edges SET t_invalid = $2, t_expired = $2 WHERE id = $1", e["id"], now
            )

        return {
            "op": "update_knowledge_node",
            "old_id": str(op.knowledge_node_id),
            "new_id": str(new_id),
            "rewired_edges": len(rewired),
            "at": now.isoformat(),
        }
