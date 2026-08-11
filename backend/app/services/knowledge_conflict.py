"""
Knowledge-conflict trigger detection.

Closes the gap found while designing Experiment 3: TriggerDetector only
ever fires off task execution metrics (error_rate/rework_rate/etc.),
never off a new piece of knowledge conflicting with something already in
the graph. There was no path from "policy contradiction exists" to "a
debate gets opened about it" at all.

Reuses an existing, already-tuned signal rather than inventing a new
one: reuse_detection.py's PARTIAL_MATCH_THRESHOLD..FULL_MATCH_THRESHOLD
band (0.70-0.90) is exactly "related enough to matter, not identical
enough to be a simple duplicate" -- full matches (>=0.90) are already
Part A's dedup job; this only looks at the band Part A deliberately
leaves alone.

Bridging trick: `triggers.task_node_id` is a required, NOT NULL foreign
key -- a trigger cannot point at a knowledge_node directly, by schema.
Rather than change that (touching an existing, load-bearing table), a
lightweight PROXY task_node gets created ("Reconcile: X vs Y"), linked
via edges to BOTH conflicting knowledge_nodes. LoopOrchestrator's
existing 2-hop context walk (_render_graph_context in loop.py) then
surfaces both nodes to the panel automatically -- no change needed to
the debate engine, context rendering, or Layer 1 evaluation. The panel
sees two conflicting pieces of knowledge as "related" context around
the flagged (proxy) task, exactly as it already does for real task
bottlenecks.

Deliberately v1-narrow: takes the SINGLE best-matching existing
conflict per new node, not all matches above threshold -- multiple
simultaneous conflicts would need a design decision (one debate for
all of them, or one each) this doesn't make for you.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg

from app.services.access import AccessScope, visibility_predicate
from app.services.hierarchy import _OWNS_FILTER
from app.services.reuse_detection import FULL_MATCH_THRESHOLD, PARTIAL_MATCH_THRESHOLD
from app.services.triggers import TriggerDetector, TriggerHit


async def find_conflicting_knowledge(
    pool: asyncpg.Pool, new_node_id: str, scope: Optional[AccessScope] = None,
) -> Optional[dict]:
    """
    Returns the best-matching EXISTING knowledge_node in the
    PARTIAL_MATCH_THRESHOLD..FULL_MATCH_THRESHOLD band, or None. Pure
    read -- no side effects, separated from trigger creation so the
    detection logic is independently testable.

    Excludes internal hierarchy nodes (Part B's "Group: ..." aggregator
    nodes) from BOTH sides of the comparison. A real, serious bug found
    against production data: once a real hierarchy exists over
    knowledge_nodes, an unfiltered scan compares synthetic mean-
    embedding aggregator nodes against real content and against each
    other -- confirmed on real data as 321,821 "candidate conflicts"
    for a ~700-document corpus (mathematically impossible for real
    pairs alone; max possible is ~245,000), dominated by nonsense like
    a "Group: Group: Group: ..." node "conflicting" with another group.
    Internal-ness is structural here, same convention as hierarchy.py:
    a node with an outgoing OWNS/PARENT_OF edge is internal, not a real
    knowledge claim, and must never be a candidate for conflict
    detection or supersession.
    """
    scope = scope or AccessScope.unrestricted()
    vis_sql, vis_params = visibility_predicate(scope, alias="b", param_index=2)
    rows = await pool.fetch(
        f"SELECT b.id, b.name, 1 - (a.embedding <=> b.embedding) AS similarity "
        f"FROM knowledge_nodes a JOIN knowledge_nodes b ON a.id != b.id "
        f"WHERE a.id = $1 AND a.t_invalid IS NULL AND b.t_invalid IS NULL "
        f"AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL AND {vis_sql} "
        f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} "
        f"  AND e.source_id = a.id AND e.source_table = 'knowledge_nodes') "
        f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} "
        f"  AND e.source_id = b.id AND e.source_table = 'knowledge_nodes') "
        f"ORDER BY similarity DESC LIMIT 1",
        UUID(new_node_id), *vis_params,
    )
    if not rows:
        return None
    row = rows[0]
    sim = float(row["similarity"])
    if PARTIAL_MATCH_THRESHOLD <= sim < FULL_MATCH_THRESHOLD:
        return {"id": str(row["id"]), "name": row["name"], "similarity": sim}
    return None


async def create_conflict_trigger_for_pair(
    pool: asyncpg.Pool,
    node_id_a: str,
    node_id_b: str,
    similarity: float,
    approver_id: str = "knowledge_conflict_detector",
) -> UUID:
    """
    The shared trigger-creation logic, factored out of
    detect_and_create_conflict_trigger so it can also run against a
    PRE-SELECTED pair (e.g. from scan_knowledge_conflicts.py's output)
    without re-running detection -- useful when you already know which
    pairs you want to debate and don't want the cost/time of scanning
    the whole corpus again. Does not check the partial-match band
    itself; the caller is responsible for that (or for deciding they
    don't care, e.g. testing a pair that's NOT in the band on purpose).
    """
    now = datetime.now(timezone.utc)
    rows = await pool.fetch(
        "SELECT id, name, properties->>'content' AS content FROM knowledge_nodes "
        "WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL",
        [UUID(node_id_a), UUID(node_id_b)],
    )
    names = {str(r["id"]): r["name"] for r in rows}
    contents = {str(r["id"]): r["content"] for r in rows}
    name_a = names.get(node_id_a, node_id_a)
    name_b = names.get(node_id_b, node_id_b)

    # Compute the date-overlap fact HERE, in real Python date math, once
    # -- not left for the panel to notice and correctly compare two date
    # strings itself while reasoning in prose. Confirmed real failure
    # this prevents: a genuine overlap was previously misdiagnosed as an
    # unrelated false positive, and groundedness scoring didn't catch it
    # since the citations were still accurate. Injected into the
    # trigger's `detail`, which build_user_prompt already renders
    # directly into the debate prompt -- the panel is TOLD the answer,
    # not expected to derive it.
    from app.services.temporal_conflict import compute_overlap
    overlap = compute_overlap(contents.get(node_id_a, ""), contents.get(node_id_b, ""))

    async with pool.acquire() as conn:
        async with conn.transaction():
            proxy = await conn.fetchrow(
                "INSERT INTO task_nodes (name, description, success_criteria, provenance, "
                "t_valid, t_created, created_by) "
                "VALUES ($1, $2, $3, 'company_debate', $4, $4, $5) RETURNING id",
                f"Reconcile: {name_a!r} vs {name_b!r}",
                "Created for a pre-selected candidate pair (batch scan), not auto-detection.",
                {"internal_proxy": True, "proxy_kind": "knowledge_conflict_reconciliation"},
                now, approver_id,
            )
            proxy_id = proxy["id"]

            for target_id in (UUID(node_id_a), UUID(node_id_b)):
                await conn.execute(
                    "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                    "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
                    "VALUES ('VALIDATED_BY', 'CONFLICTS_WITH', $1, 'task_nodes', $2, 'knowledge_nodes', "
                    "$3, 'company_debate', $4, $4, $5)",
                    proxy_id, target_id, {"similarity": similarity}, now, approver_id,
                )

    detector = TriggerDetector(pool)
    detail = {
        "node_id_a": node_id_a, "name_a": name_a,
        "node_id_b": node_id_b, "name_b": name_b,
    }
    if overlap:
        detail["MECHANICALLY_COMPUTED_DATE_OVERLAP"] = (
            f"Node A active {overlap['range_a']}. Node B active {overlap['range_b']}. "
            f"These OVERLAP for {overlap['overlap_days']} day(s), from {overlap['overlap_start']} "
            f"to {overlap['overlap_end']}. This is a computed fact, not a suggestion -- do not "
            f"conclude these are an unrelated false positive; your resolution MUST state which "
            f"applies during the overlap. "
            f"DATE PRESERVATION IS MANDATORY: any date you write into a change_set (an end date, "
            f"a 'superseded as of' date, a boundary date, etc.) MUST be one of the exact literal "
            f"dates given above ({overlap['range_a']} / {overlap['range_b']} / overlap boundaries "
            f"{overlap['overlap_start']} to {overlap['overlap_end']}) -- never a rounded, simplified, "
            f"or 'cleaner' substitute (e.g. writing 10/31 instead of the stated 11/12 because it "
            f"reads as a tidier month boundary). This has actually happened in a prior run and was "
            f"caught by the grounding checker as a fabricated number. If you want to express 'this "
            f"promotion is effectively superseded once the other one starts,' state the OTHER node's "
            f"real start date as the precedence point, verbatim -- do not invent a new date to make "
            f"the boundary sound cleaner."
        )

    # Same fix pattern, for a different confirmed real failure: the
    # panel was asked to compare two nodes' content for a SYNTHESIS/
    # MERGE resolution and, across three separate real attempts, every
    # one produced a confident but FALSE claim about what differed
    # ("word-for-word identical" when it wasn't; "lacks status
    # filtering" -- twice -- when the identical filtering language was
    # present in both). Computing the real diff here, in code, removes
    # the task of NOTICING differences from the panel's own reasoning;
    # its job becomes judging what a verified-real difference means,
    # not rediscovering what differs in the first place.
    from app.services.content_diff import compute_content_diff
    content_diff = compute_content_diff(contents.get(node_id_a, ""), contents.get(node_id_b, ""))
    diff_lines = [
        f"MECHANICALLY COMPUTED CONTENT DIFF (not a suggestion -- this is the real, verified "
        f"textual difference between the two nodes; do not independently judge whether they are "
        f"'identical' or whether one 'lacks' something the diff below shows is actually present "
        f"in it). similarity_ratio={content_diff['similarity_ratio']} "
        f"(1.0 = identical text, 0.0 = no overlap at all).",
    ]
    if content_diff["identical"]:
        diff_lines.append("The two nodes' content is textually IDENTICAL. Any resolution claiming "
                           "a content difference between them would be false.")
    else:
        if content_diff["only_in_a"]:
            diff_lines.append(f"Present ONLY in node A ({content_diff['n_common_sentences']} "
                               f"sentences are shared by both, not counted here):")
            diff_lines.extend(f"  - {s}" for s in content_diff["only_in_a"][:10])
        if content_diff["only_in_b"]:
            diff_lines.append(f"Present ONLY in node B:")
            diff_lines.extend(f"  - {s}" for s in content_diff["only_in_b"][:10])
        diff_lines.append(
            "If a sentence in one list has an obvious close counterpart in the other (e.g. both "
            "describe the same step with slightly different wording), that concept is present in "
            "BOTH nodes -- do not claim one side lacks it. Only claim something is missing from a "
            "node if it has no counterpart anywhere in the other node's list above."
        )
    detail["MECHANICALLY_COMPUTED_CONTENT_DIFF"] = "\n".join(diff_lines)

    # Real, serious bug caught before ever applying a proposal: a real
    # debate run proposed writing merged content under a key called
    # "statement" -- the actual key every retrieval query in this
    # codebase reads is "content" (confirmed: ingest_trajectory_library.py,
    # debate_task_a_vs_task_b.py's own SELECT). Applying that proposal
    # as-written would have silently destroyed the real "postconditions"
    # key (KnowledgeUpdater's merge REPLACES the whole properties dict,
    # not a deep merge) and written content under a key nothing ever
    # reads -- the update would "succeed" but be permanently invisible
    # to retrieval. Nothing in the prompt told the panel the real schema,
    # so it invented a plausible-sounding key. Telling it directly.
    schema_keys_a = sorted((await pool.fetchrow(
        "SELECT properties FROM knowledge_nodes WHERE id = $1", node_id_a))["properties"].keys())
    schema_keys_b = sorted((await pool.fetchrow(
        "SELECT properties FROM knowledge_nodes WHERE id = $1", node_id_b))["properties"].keys())
    detail["MECHANICALLY_COMPUTED_PROPERTY_SCHEMA"] = (
        f"Node A's real, current properties keys: {schema_keys_a}. "
        f"Node B's real, current properties keys: {schema_keys_b}. "
        f"If your change_set writes to 'properties', it REPLACES the entire properties "
        f"object -- it is not a deep merge. If a node has a key you are not deliberately "
        f"changing (e.g. 'postconditions'), you MUST include it, unchanged, in your proposed "
        f"'properties' object, or it will be silently deleted. The main content field is "
        f"stored under the key 'content', not 'statement', 'text', 'body', or any other name -- "
        f"use 'content' exactly, or your proposed update will be written but never actually "
        f"read by anything, indistinguishable from having done nothing at all."
    )

    hit = TriggerHit(
        task_node_id=proxy_id,
        rule_name="knowledge_conflict",
        metric_name="similarity",
        observed_value=similarity,
        threshold=PARTIAL_MATCH_THRESHOLD,
        sample_size=1,
        detail=detail,
    )
    ids = await detector.record([hit])
    return ids[0]


async def detect_and_create_conflict_trigger(
    pool: asyncpg.Pool,
    new_node_id: str,
    scope: Optional[AccessScope] = None,
    approver_id: str = "knowledge_conflict_detector",
) -> Optional[UUID]:
    """
    If `new_node_id` conflicts with an existing knowledge_node, creates
    the proxy task_node + CONFLICTS_WITH edges + trigger row, and
    returns the new trigger's id (ready to hand to
    LoopOrchestrator.run()). Returns None if no conflict was found --
    a normal, common outcome, not a failure.
    """
    conflict = await find_conflicting_knowledge(pool, new_node_id, scope)
    if conflict is None:
        return None
    return await create_conflict_trigger_for_pair(
        pool, new_node_id, conflict["id"], conflict["similarity"], approver_id,
    )
