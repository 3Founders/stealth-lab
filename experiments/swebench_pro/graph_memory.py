"""
Leave-one-out memory over the real graph: TMS for the holdout, HTN + hybrid
retrieval for the lookup.

HOLDOUT VIA BI-TEMPORAL INVALIDATION, NOT DELETION

Holding an instance out sets `t_invalid` on its task node, its knowledge
node, and the edge between them. Every read path in the backend already
filters `t_invalid IS NULL` -- that is what the column exists for -- so the
instance vanishes from retrieval without leaving the database, and putting
it back is one UPDATE rather than a re-embed. Running the experiment
through the truth-maintenance mechanism also exercises it: if invalidation
leaked anywhere, the held-out instance would retrieve itself and the hit
rate would be a giveaway 1.0.

THE HIERARCHY IS REBUILT AFTER THE HOLDOUT, EVERY TIME

Internal nodes route on the mean of their children's embeddings
(hierarchy.py:217). A tree built while the held-out leaf was still live has
that leaf's vector folded into its parent's routing signal, so descent is
pulled toward the answer by a structure that should not know about it. The
effect is small -- one child in a group of up to twelve -- but it is real
leakage of exactly the kind that makes a retrieval number flatter than the
mechanism earns. Rebuilding costs seconds and removes the question.

TWO RETRIEVERS, REPORTED SEPARATELY

  HybridRetriever      RRF over vector + Postgres FTS to pick entrypoints,
                       then one hop along OWNS/RESOLVED_AT to pull each
                       matched issue's code-location node. This is the
                       "similar problem -> where that problem lived"
                       traversal, and it is the part a flat store cannot do.

  hierarchical_search  beam descent through the HTN tree over task_nodes.

They answer different questions and can disagree, so both are recorded
rather than blended. Blending them would produce one number that could not
be attributed to either mechanism -- the same mistake as the retracted
cost-saving claim in EXPERIMENT_RESULTS.md.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.access import AccessScope  # noqa: E402
from patch_format import diff_to_search_replace  # noqa: E402
from app.services.hierarchy import build_hierarchy_for_table, hierarchical_search  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402

CREATED_BY = "swebench_ingest"
HIERARCHY_BY = "hierarchy_builder"


@dataclass
class GraphHit:
    instance_id: str
    title: str
    repo: str
    language: str
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    interface: str = ""
    categories: list[str] = field(default_factory=list)
    score: float = 0.0
    matched_by: list[str] = field(default_factory=list)
    via: str = "task"  # "task" = matched directly, "knowledge" = pulled by traversal
    patch: str = ""            # the precedent's gold diff, if stored
    requirements: str = ""     # what that fix had to satisfy


async def hold_out(pool, instance_id: str) -> int:
    """Invalidate one instance's nodes and edges. Returns rows affected."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            n1 = await conn.execute(
                "UPDATE task_nodes SET t_invalid = now() "
                "WHERE skill_ref = $1 AND t_invalid IS NULL", instance_id)
            n2 = await conn.execute(
                "UPDATE knowledge_nodes SET t_invalid = now() "
                "WHERE properties->>'instance_id' = $1 AND t_invalid IS NULL",
                instance_id)
            n3 = await conn.execute(
                "UPDATE edges SET t_invalid = now() "
                "WHERE properties->>'instance_id' = $1 AND t_invalid IS NULL",
                instance_id)
    return sum(int(r.split()[-1]) for r in (n1, n2, n3))


async def restore_all(pool) -> None:
    """Undo every holdout. Cheap, and makes the script safe to re-run."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            for table in ("task_nodes", "knowledge_nodes", "edges"):
                await conn.execute(
                    f"UPDATE {table} SET t_invalid = NULL "
                    f"WHERE created_by = $1 AND t_invalid IS NOT NULL", CREATED_BY)


async def rebuild_hierarchy(pool, embedder, tables=("task_nodes",)) -> dict:
    """
    Drop and rebuild the HTN tree over the CURRENTLY LIVE leaves.

    Must run after hold_out -- see module docstring. Builds over task_nodes
    only by default: descent is for finding a similar PROBLEM, and the
    knowledge nodes are reached from there by edge traversal rather than by
    their own tree.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM edges WHERE created_by = $1", HIERARCHY_BY)
            await conn.execute("DELETE FROM task_nodes WHERE created_by = $1", HIERARCHY_BY)
            await conn.execute("DELETE FROM knowledge_nodes WHERE created_by = $1", HIERARCHY_BY)
    out = {}
    for table in tables:
        report = await build_hierarchy_for_table(
            pool, table, scope=AccessScope.unrestricted(), embedder=embedder,
            apply=True,
        )
        out[table] = report
    return out


async def _instance_of(pool, node_id, table: str) -> Optional[dict]:
    """Map a retrieved node back to the instance it came from, whichever
    side of the task/knowledge pair was hit."""
    if table == "task_nodes":
        row = await pool.fetchrow(
            "SELECT skill_ref AS iid, name, io_schema AS props FROM task_nodes "
            "WHERE id = $1", node_id)
    else:
        row = await pool.fetchrow(
            "SELECT properties->>'instance_id' AS iid, name, properties AS props "
            "FROM knowledge_nodes WHERE id = $1", node_id)
    return dict(row) if row else None


async def _hydrate(pool, instance_id: str) -> Optional[dict]:
    """Everything the renderer needs about one instance, from both nodes."""
    return await pool.fetchrow(
        "SELECT t.name AS title, t.io_schema AS tprops, k.properties AS kprops "
        "FROM task_nodes t LEFT JOIN knowledge_nodes k "
        "  ON k.properties->>'instance_id' = t.skill_ref "
        "WHERE t.skill_ref = $1 LIMIT 1", instance_id)


async def retrieve(
    pool, query_text: str, embedder, top_k: int = 5, expand_depth: int = 1,
    embedding_column: str = "embedding",
) -> tuple[list[GraphHit], dict]:
    """
    Hybrid entrypoints + one-hop expansion, mapped back to instances.

    Returns (hits, diagnostics). Diagnostics carry the raw node counts so a
    run that retrieved nothing is distinguishable from one that retrieved
    the wrong thing -- the distinction Exp 1 kept losing.
    """
    # `embedding_joint` lives on task_nodes only, so restrict the search to
    # that table rather than erroring on a column knowledge_nodes lacks. The
    # knowledge side is still reached -- by one-hop traversal along
    # OWNS/RESOLVED_AT, which is the mechanism under test anyway.
    tables = (("task_nodes",) if embedding_column != "embedding"
              else ("task_nodes", "knowledge_nodes"))
    retriever = HybridRetriever(pool, embedder=embedder,
                                scope=AccessScope.unrestricted(),
                                embedding_column=embedding_column,
                                tables=tables)
    result = await retriever.retrieve(
        query_text, top_k=top_k, expand_depth=expand_depth,
        max_context_nodes=top_k * 4)

    seen: dict[str, GraphHit] = {}
    for node in result.nodes:
        info = await _instance_of(pool, node.id, node.table)
        if not info or not info.get("iid"):
            continue  # a hierarchy group node, which has no instance
        iid = info["iid"]
        if iid in seen:
            seen[iid].matched_by = sorted(set(seen[iid].matched_by + node.matched_by))
            continue
        full = await _hydrate(pool, iid)
        if not full:
            continue
        kprops = full["kprops"] or {}
        tprops = full["tprops"] or {}
        seen[iid] = GraphHit(
            instance_id=iid, title=full["title"],
            repo=kprops.get("repo") or tprops.get("repo", "?"),
            language=kprops.get("language") or tprops.get("language", "?"),
            files=kprops.get("files", []), symbols=kprops.get("symbols", []),
            interface=kprops.get("interface", ""),
            categories=kprops.get("issue_categories", []),
            score=node.score, matched_by=list(node.matched_by),
            via="task" if node.table == "task_nodes" else "knowledge",
            patch=kprops.get("patch", ""),
            requirements=kprops.get("requirements", ""),
        )
    hits = sorted(seen.values(), key=lambda h: -h.score)[:top_k]
    diag = {
        "nodes_returned": len(result.nodes),
        "entrypoints": len(result.entrypoint_ids),
        "instances_resolved": len(seen),
        "direct": sum(1 for n in result.nodes if n.hops == 0),
        "by_expansion": sum(1 for n in result.nodes if n.hops > 0),
    }
    return hits, diag


async def htn_route(pool, query_text: str, embedder, beam: int = 3) -> dict:
    """Beam descent through the tree, reported alongside but not blended."""
    res = await hierarchical_search(
        pool, "task_nodes", query_text, scope=AccessScope.unrestricted(),
        embedder=embedder, beam=beam, adaptive=True)
    iid = None
    if res.leaf_id:
        row = await pool.fetchrow(
            "SELECT skill_ref FROM task_nodes WHERE id = $1", res.leaf_id)
        iid = row["skill_ref"] if row else None
    return {
        "instance_id": iid, "leaf_name": res.leaf_name,
        "similarity": res.similarity,
        # SearchResult exposes exactly these: leaf_id, leaf_name, similarity,
        # used_flat_fallback, comparisons. `used_flat_fallback` True means the
        # tree declined to route and this is not an HTN result at all.
        "used_flat_fallback": res.used_flat_fallback,
        "comparisons": res.comparisons,
    }


def render_context(
    hits: list[GraphHit],
    max_chars: int = 6000,
    include_patches: bool = False,
    patch_chars: int = 1400,
    include_requirements: bool = False,
    minimal: bool = False,
) -> str:
    """
    What the agent actually sees.

    `include_patches` renders each precedent's actual gold DIFF. This is
    NOT leakage of the held-out instance -- its own row is invalidated
    before retrieval runs and the runner aborts if it retrieves itself --
    but it does change what the resolution number means, and that change is
    the point rather than a side effect. With diffs present, a success may
    come from adapting a near-duplicate fix rather than from reasoning about
    the current one. Those are different capabilities and a single
    resolved/not flag cannot tell them apart, so run_graph_instance.py scores
    copyability alongside resolution: how much of the gold patch was already
    present in what the agent was shown. Read the two together or the number
    means nothing.

    Default stays off, so the localization-only arm remains available as the
    baseline the patch arm is compared against.
    """
    if not hits:
        return ""
    header = (
        "PRIOR RESOLVED ISSUES IN THIS CODEBASE (retrieved from the knowledge "
        "graph: similar problems, and where their fixes landed)"
    )
    header += (
        ", EACH WITH THE EXACT CHANGE THAT FIXED IT, shown as "
        "SEARCH/REPLACE blocks in the same form your edit_file tool takes "
        "(SEARCH = old_str, REPLACE = new_str). Adapt the pattern where it "
        "applies -- the text will not match this repository verbatim, and the "
        "current issue may need something different:\n"
        if include_patches else
        ". These are hints about where to look, not answers -- the current issue "
        "may live somewhere else entirely:\n"
    )
    out = [header]
    used = len(header)
    omitted = 0
    for h in hits:
        block = f"- [{h.repo}] {h.title}"
        if h.files:
            block += f"\n    files changed: {', '.join(h.files[:6])}"
        # `minimal` renders ONLY the issue and the change, in the agent's own
        # edit format. Symbols, area tags and requirements are prose ABOUT the
        # fix; the SEARCH/REPLACE block IS the fix. Every extra line is resent
        # on every call, and the flat agent's cost is quadratic in context, so
        # prose that the model cannot act on directly is paid for repeatedly.
        if not minimal:
            if h.symbols:
                block += f"\n    functions/classes: {', '.join(h.symbols[:8])}"
            if h.categories:
                block += f"\n    area: {', '.join(h.categories[:4])}"
            if include_requirements and h.requirements:
                block += f"\n    what that fix had to satisfy: {h.requirements[:600].strip()}"
        if include_patches and h.patch:
            # SEARCH/REPLACE, not a unified diff. The agent's only editing
            # tool is edit_file(old_str, new_str), so a block maps onto it
            # one-to-one; a `@@ -42,7 +42,8 @@` header makes the model read
            # one format and write another, and its line numbers refer to
            # the PRECEDENT's file, not the one being edited. See
            # patch_format.diff_to_search_replace.
            body = diff_to_search_replace(h.patch, max_chars=patch_chars)
            if body:
                block += f"\n    how that fix was made:\n{body}"
        if used + len(block) > max_chars and len(out) > 1:
            omitted = len(hits) - (len(out) - 1)
            break
        out.append(block)
        used += len(block)
    if omitted:
        out.append(f"…[{omitted} further precedents omitted for length]")
    return "\n".join(out) + "\n"
