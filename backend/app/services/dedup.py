"""
Reuse consolidation (Part A of HIERARCHICAL_DECOMPOSITION_PLAN.md).

The gap this closes: reuse_detection.py only ever checks ONE incoming
problem string against the existing graph, at query time. It never
checks the several new nodes a single decompose call proposes against
EACH OTHER, and nothing periodically reconciles nodes that entered the
graph independently at different times and turned out to be duplicates
(the exact failure hide_duplicate_seeds.py was a one-off manual patch
for -- exact-name matches only, hidden via visibility rather than
properly merged).

Two entry points, deliberately different in what they're allowed to
touch:

  - dedupe_changeset_ops: pure, in-memory. Consolidates a PROPOSED,
    not-yet-applied ChangeSet's own create ops against each other.
    Never touches the database. Safe to run on generated/untrusted
    output -- it can only make a proposal smaller (drop a redundant
    create, rewrite a ref), never attach to or modify anything that
    already exists, so it cannot violate GENERATIVE_OP_TYPES
    (app/models/change.py) no matter what produced the ChangeSet.

  - find_duplicate_clusters / merge_cluster / run_dedup_sweep: batch
    reconciliation over the ALREADY-PERSISTED graph. This invalidates
    real rows and rewires real edges (same pattern as
    knowledge_update.py::_supersede_task), so it is an internal/admin
    operation only -- an operator running scripts/dedup_sweep.py, same
    trust boundary reuse_detection.py and knowledge_update.py already
    draw between generated input and privileged maintenance paths.

Merge rule is COMPLETE-LINKAGE, not naive transitive union-find: a
candidate joins a cluster only if it scores above threshold against
EVERY existing member, not just its nearest neighbor. Plain transitive
union-find chains -- A~B~C~D can end up one cluster even when A and D
aren't themselves similar, reproduced and confirmed as a real failure
mode during design (see plan doc, Part A). Complete linkage was
confirmed to correctly split that same chain.

Borderline merges are NOT auto-applied here. A cluster is a candidate
for merge; nothing in this module decides "is 0.87 close enough" for
you without a human in the loop for anything short of the same
FULL_MATCH_THRESHOLD reuse_detection.py already uses for full-match
reuse -- see run_dedup_sweep's `apply` flag and dry-run default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

import asyncpg

from app.models.change import (
    ChangeSet,
    CreateEdgeOp,
    CreateKnowledgeNodeOp,
    CreateTaskNodeOp,
)
from app.services.access import AccessScope, visibility_predicate
from app.services.embeddings import Embedder
from app.services.reuse_detection import (
    FULL_MATCH_THRESHOLD,
    LEXICAL_FULL_MATCH_THRESHOLD,
    _lexical_overlap,
)

log = logging.getLogger(__name__)

_TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at",
    "is", "are", "your", "you", "this", "that", "with", "how", "what",
    "explained", "understanding", "overview",
})


def _title_diff_is_trivial(name_a: str, name_b: str) -> bool:
    """
    True only if every substantive word in one title also appears in the
    other -- i.e. the symmetric difference, after stripping punctuation
    and stopwords, is empty.

    Exists because embedding similarity ALONE is not a safe dedup signal
    on corpora with deliberate cross-variant templating (confirmed on
    real data: "Blue Account specifications and requirements" vs
    "Purple Account specifications and requirements" scored 0.95 cosine
    similarity -- two genuinely different products sharing a template).
    Generic lexical-overlap measures don't fix this either -- checked
    both before writing this: word-Jaccard on that same pair scores
    0.67 (above the existing 0.55 lexical threshold), and
    difflib.SequenceMatcher scores 0.93 -- both dominated by the long
    shared boilerplate, both would wrongly pass. The actual signal that
    matters isn't "how similar overall", it's "does any real content
    word differ at all" -- "blue" vs "purple" is a small edit distance
    and a small set-difference, but it's exactly the word that makes
    these different things.

    Deliberately strict (empty diff, not "mostly the same"): a false
    merge silently destroys a real distinction (confirmed: 303 wrongly
    merged on this corpus); a false non-merge just leaves two rows a
    later, more careful pass can still catch. The costs are not
    symmetric, so this errs toward under-merging, not over-merging.
    """
    import re

    def tokens(s: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", s.lower())
        return {w for w in words if w not in _TITLE_STOPWORDS}

    ta, tb = tokens(name_a), tokens(name_b)
    return (ta ^ tb) == set()


def _categories_agree(properties_a, properties_b) -> bool:
    """
    False only when BOTH rows carry an explicit 'category' in properties
    AND those categories differ. True whenever either side lacks a
    category (most tables/corpora don't have one -- this must not block
    dedup for data that never opted into categorization).

    Exists for the exact false positive the title gate alone can't
    catch: "Business Silver Rewards Card: ..." vs "Silver Rewards
    Card: ..." -- "business" appears in BOTH titles (once as the
    distinguishing product-line prefix, once incidentally in "Business
    Travel" in both), so the word-SET diff is empty even though these
    are genuinely different products. The corpus's own category label
    (business_credit_cards vs credit_cards) catches what the title
    heuristic structurally cannot, since it's position-blind by design.
    """
    cat_a = (properties_a or {}).get("category") if isinstance(properties_a, dict) else None
    cat_b = (properties_b or {}).get("category") if isinstance(properties_b, dict) else None
    if cat_a is None or cat_b is None:
        return True
    return cat_a == cat_b


def complete_linkage_clusters(
    keys: list[str], similarity: Callable[[str, str], float], threshold: float
) -> list[list[str]]:
    """
    Group `keys` by pairwise `similarity`, complete-linkage: a key only
    joins a cluster if it scores >= threshold against EVERY member
    already in it.

    O(n^2) comparisons. Fine at changeset scale (single-digit to low
    tens of proposed ops) and at typical per-table batch-sweep scale --
    a corpus large enough for this to be a real cost is also large
    enough that Part B (hierarchical retrieval) is the relevant
    mechanism, not this one.
    """
    clusters: list[list[str]] = []
    for key in keys:
        placed = False
        for cluster in clusters:
            if all(similarity(key, member) >= threshold for member in cluster):
                cluster.append(key)
                placed = True
                break
        if not placed:
            clusters.append([key])
    return clusters


# ---------------------------------------------------------------------
# In-memory: consolidate a proposed ChangeSet's own new ops
# ---------------------------------------------------------------------

def _op_text(op) -> str:
    if isinstance(op, CreateTaskNodeOp):
        return f"{op.name} {op.description or ''}"
    if isinstance(op, CreateKnowledgeNodeOp):
        return op.name
    return ""


def dedupe_changeset_ops(change_set: ChangeSet) -> tuple[ChangeSet, list[dict]]:
    """
    Collapse near-duplicate create ops WITHIN one proposed ChangeSet
    against each other.

    Lexical-only, deliberately: this runs on a just-generated proposal
    that hasn't been embedded yet, and embedding every op just to dedupe
    it would add a round-trip for what's usually a handful of ops.
    Reuses LEXICAL_FULL_MATCH_THRESHOLD as-is rather than inventing a
    new constant.

    A task and a knowledge node are never considered duplicates of each
    other regardless of text similarity -- they're different kinds of
    thing even when worded alike.

    Returns (possibly-smaller ChangeSet, report). Report is empty when
    nothing merged, which is the common case and a real, valid answer.
    """
    create_ops = [
        op for op in change_set.ops
        if isinstance(op, (CreateTaskNodeOp, CreateKnowledgeNodeOp))
    ]
    if len(create_ops) < 2:
        return change_set, []

    ref_to_op = {op.ref: op for op in create_ops}
    refs = list(ref_to_op.keys())

    def sim(ref_a: str, ref_b: str) -> float:
        a, b = ref_to_op[ref_a], ref_to_op[ref_b]
        if type(a) is not type(b):
            return 0.0
        return _lexical_overlap(_op_text(a), _op_text(b))

    clusters = complete_linkage_clusters(refs, sim, LEXICAL_FULL_MATCH_THRESHOLD)
    merges = [c for c in clusters if len(c) > 1]
    if not merges:
        return change_set, []

    ref_rewrite: dict[str, str] = {}
    dropped_refs: set[str] = set()
    report: list[dict] = []
    for cluster in merges:
        canonical, *dupes = cluster  # first-declared ref wins; no other
                                      # signal available at proposal time
        for dup in dupes:
            ref_rewrite[dup] = canonical
            dropped_refs.add(dup)
        report.append({
            "canonical_ref": canonical,
            "canonical_name": ref_to_op[canonical].name,
            "merged_refs": dupes,
            "merged_names": [ref_to_op[d].name for d in dupes],
        })

    new_ops = []
    for op in change_set.ops:
        if isinstance(op, (CreateTaskNodeOp, CreateKnowledgeNodeOp)):
            if op.ref in dropped_refs:
                continue
            new_ops.append(op)
        elif isinstance(op, CreateEdgeOp):
            new_source = ref_rewrite.get(op.source_ref, op.source_ref) if op.source_ref else op.source_ref
            new_target = ref_rewrite.get(op.target_ref, op.target_ref) if op.target_ref else op.target_ref
            if new_source and new_source == new_target:
                # Both ends collapsed onto the same canonical ref --
                # would be self-referential and validate_ops() would
                # reject it anyway; drop rather than write it.
                continue
            new_ops.append(op.model_copy(update={"source_ref": new_source, "target_ref": new_target}))
        else:
            new_ops.append(op)

    return ChangeSet(ops=new_ops), report


# ---------------------------------------------------------------------
# Batch sweep over the persisted graph -- internal/admin only
# ---------------------------------------------------------------------

@dataclass
class MergeReport:
    table: str
    canonical_id: str
    canonical_name: str
    merged_ids: list[str]
    merged_names: list[str]
    rewired_edges: int  # -1 in dry-run reports: not computed without writing


async def find_duplicate_clusters(
    pool: asyncpg.Pool,
    table: str,
    scope: AccessScope,
    embedder: Optional[Embedder] = None,
) -> list[list[dict]]:
    """
    Scan the current rows of `table` and complete-linkage-cluster them.

    Vector similarity (real cosine via pgvector's `<=>`, computed in
    SQL -- asyncpg has no codec for the vector type, so pairwise
    similarity is computed server-side rather than parsing raw vectors
    back into Python) when every current row has an embedding. Falls
    back to lexical overlap, same binary fallback reuse_detection.py
    uses and for the same reason (Voyage frequently unconfigured in
    this project's own testing).

    Returns only clusters with more than one member.
    """
    name_expr = "name || ' ' || COALESCE(description, '')" if table == "task_nodes" else "name"
    props_expr = "properties" if table == "knowledge_nodes" else "NULL"
    vis_sql, vis_params = visibility_predicate(scope, param_index=1)

    rows = await pool.fetch(
        f"SELECT id, name, {name_expr} AS full_text, (embedding IS NOT NULL) AS has_embedding, "
        f"{props_expr} AS properties "
        f"FROM {table} WHERE t_invalid IS NULL AND {vis_sql}",
        *vis_params,
    )
    if len(rows) < 2:
        return []

    by_id = {str(r["id"]): r for r in rows}
    ids = list(by_id.keys())
    use_vector = all(r["has_embedding"] for r in rows)

    if use_vector:
        vis_sql_a, vis_params = visibility_predicate(scope, alias="a", param_index=1)
        vis_sql_b, _ = visibility_predicate(scope, alias="b", param_index=1)  # same $1, reused
        pair_rows = await pool.fetch(
            f"SELECT a.id AS id_a, b.id AS id_b, "
            f"1 - (a.embedding <=> b.embedding) AS similarity "
            f"FROM {table} a JOIN {table} b ON a.id < b.id "
            f"WHERE a.t_invalid IS NULL AND b.t_invalid IS NULL AND {vis_sql_a} AND {vis_sql_b}",
            *vis_params,
        )
        sim_lookup: dict[tuple[str, str], float] = {}
        for r in pair_rows:
            a, b, s = str(r["id_a"]), str(r["id_b"]), float(r["similarity"])
            sim_lookup[(a, b)] = s
            sim_lookup[(b, a)] = s

        def sim(a: str, b: str) -> float:
            if not _title_diff_is_trivial(by_id[a]["name"], by_id[b]["name"]):
                return 0.0  # any substantive title difference disqualifies a merge,
                            # regardless of embedding similarity -- see _title_diff_is_trivial
            if not _categories_agree(by_id[a].get("properties"), by_id[b].get("properties")):
                return 0.0  # different product lines with a same-tier name in common
                            # (e.g. "Business Silver Rewards Card" vs "Silver Rewards Card" --
                            # a real false positive: the title gate alone can't see that
                            # "business" is a distinguishing PREFIX, not incidental repetition,
                            # since it works on word sets, not position). Only applies when
                            # both rows actually carry a category -- absent on most tables.
            return sim_lookup.get((a, b), 0.0)

        threshold = FULL_MATCH_THRESHOLD
    else:
        def sim(a: str, b: str) -> float:
            return _lexical_overlap(by_id[a]["full_text"], by_id[b]["full_text"])

        threshold = LEXICAL_FULL_MATCH_THRESHOLD

    clusters = complete_linkage_clusters(ids, sim, threshold)
    return [
        [{"id": i, "name": by_id[i]["name"]} for i in cluster]
        for cluster in clusters if len(cluster) > 1
    ]


async def merge_cluster(
    conn: asyncpg.Connection,
    table: str,
    cluster_ids: list[str],
    approver_id: str,
    now: datetime,
    canonical_rule: str = "earliest",
) -> Optional[MergeReport]:
    """
    Merge one cluster of duplicate ids within `table`, inside a
    transaction the caller owns.

    `canonical_rule` controls which member survives:
      - "earliest" (default): earliest t_created wins. Correct for Part
        A's actual use case -- accidental re-creation of the same node,
        where the first-created row is the real one and later ones are
        the duplicates.
      - "latest": most recent t_created wins. Required for supersession
        (a NEW policy replacing an OLD one, e.g. Experiment 3's debate-
        triggered updates) -- using "earliest" there would keep the
        STALE node live and invalidate the new one, exactly backwards.
        This was a real gap: found via a merge_cluster test built
        specifically to check post-merge read behavior, not caught by
        code review alone.

    Edges pointing at a duplicate are rewired onto the canonical node
    (same pattern as knowledge_update.py::_supersede_task), or dropped
    if rewiring would make them self-referential. Each duplicate is
    invalidated (never deleted) and linked to the canonical node with
    a SUPERSEDES edge tagged custom_edge_type='DUPLICATE_OF'.

    Returns None if fewer than 2 members are still live (a concurrent
    sweep or edit already reconciled this cluster) -- a valid no-op,
    not an error.
    """
    if canonical_rule not in ("earliest", "latest"):
        raise ValueError(f"canonical_rule must be 'earliest' or 'latest', got {canonical_rule!r}")

    rows = await conn.fetch(
        f"SELECT id, name, t_created FROM {table} "
        f"WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL FOR UPDATE",
        [UUID(i) for i in cluster_ids],
    )
    if len(rows) < 2:
        return None

    ordered = sorted(rows, key=lambda r: r["t_created"], reverse=(canonical_rule == "latest"))
    canonical = ordered[0]
    duplicates = ordered[1:]
    canonical_id = canonical["id"]

    rewired_total = 0
    for dup in duplicates:
        dup_id = dup["id"]

        rewired = await conn.fetch(
            "SELECT id, edge_type::text AS edge_type, custom_edge_type, source_id, "
            "source_table, target_id, target_table, properties FROM edges "
            "WHERE t_invalid IS NULL AND "
            "((source_id = $1 AND source_table = $2) OR (target_id = $1 AND target_table = $2))",
            dup_id, table,
        )
        for e in rewired:
            src = canonical_id if e["source_id"] == dup_id else e["source_id"]
            tgt = canonical_id if e["target_id"] == dup_id else e["target_id"]
            await conn.execute(
                "UPDATE edges SET t_invalid = $2, t_expired = $2 WHERE id = $1", e["id"], now
            )
            if src == tgt:
                # Both ends collapse onto the canonical node -- would be
                # self-referential once rewired; drop instead of writing it.
                continue
            props = e["properties"] if isinstance(e["properties"], dict) else {}
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
                "VALUES ($1::edge_type, $2, $3, $4, $5, $6, $7, 'company_debate', $8, $8, $9)",
                e["edge_type"], e["custom_edge_type"], src, e["source_table"],
                tgt, e["target_table"], props, now, approver_id,
            )
            rewired_total += 1

        await conn.execute(
            "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
            "VALUES ('SUPERSEDES', 'DUPLICATE_OF', $1, $2, $3, $2, $4, 'company_debate', $5, $5, $6)",
            canonical_id, table, dup_id,
            {"reason": "reuse-consolidation: complete-linkage merge"}, now, approver_id,
        )
        await conn.execute(
            f"UPDATE {table} SET t_invalid = $2, t_expired = $2 WHERE id = $1", dup_id, now
        )

    return MergeReport(
        table=table,
        canonical_id=str(canonical_id),
        canonical_name=canonical["name"],
        merged_ids=[str(d["id"]) for d in duplicates],
        merged_names=[d["name"] for d in duplicates],
        rewired_edges=rewired_total,
    )


async def run_dedup_sweep(
    pool: asyncpg.Pool,
    tables: tuple[str, ...] = ("task_nodes", "knowledge_nodes"),
    scope: Optional[AccessScope] = None,
    embedder: Optional[Embedder] = None,
    approver_id: str = "dedup_sweep",
    apply: bool = False,
) -> list[MergeReport]:
    """
    Find and (if `apply`) merge duplicate clusters across `tables`.

    Defaults to a dry run -- callers (scripts/dedup_sweep.py) must pass
    apply=True explicitly to write anything, same cautious-by-default
    posture as the rest of this project's maintenance tooling.

    Dry-run reports have rewired_edges=-1 (not computed without a write).
    """
    scope = scope or AccessScope.unrestricted()
    now = datetime.now(timezone.utc)
    reports: list[MergeReport] = []

    for table in tables:
        clusters = await find_duplicate_clusters(pool, table, scope, embedder)
        for cluster in clusters:
            ids = [c["id"] for c in cluster]
            if not apply:
                reports.append(MergeReport(
                    table=table, canonical_id=ids[0], canonical_name=cluster[0]["name"],
                    merged_ids=ids[1:], merged_names=[c["name"] for c in cluster[1:]],
                    rewired_edges=-1,
                ))
                continue
            async with pool.acquire() as conn:
                async with conn.transaction():
                    report = await merge_cluster(conn, table, ids, approver_id, now)
                    if report is not None:
                        reports.append(report)

    return reports