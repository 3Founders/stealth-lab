"""
Tests for Part A reuse consolidation (app/services/dedup.py).

The important assertion here is the chaining one: does complete-linkage
clustering actually avoid the failure naive transitive union-find has
(two dissimilar nodes merged anyway through a path of intermediates)?
That's the whole reason this module exists instead of a five-line
union-find, so it's tested directly, not just exercised incidentally.
"""
from __future__ import annotations

from app.models.change import ChangeSet, CreateEdgeOp, CreateKnowledgeNodeOp, CreateTaskNodeOp
from app.services.dedup import complete_linkage_clusters, dedupe_changeset_ops


# --- complete_linkage_clusters -----------------------------------------

def test_chain_is_not_merged_by_complete_linkage():
    """
    A-B similar, B-C similar, C-D similar, but A-D is NOT similar.
    Naive transitive union-find merges all four anyway (through the
    chain). Complete-linkage must not.
    """
    sims = {
        ("A", "B"): 0.95, ("B", "C"): 0.95, ("C", "D"): 0.95,
        ("A", "C"): 0.80, ("B", "D"): 0.80,
        ("A", "D"): 0.60,  # the pair that must NOT end up in one cluster
    }

    def sim(a, b):
        if a == b:
            return 1.0
        return sims.get((a, b)) or sims.get((b, a)) or 0.0

    clusters = complete_linkage_clusters(["A", "B", "C", "D"], sim, threshold=0.90)

    for cluster in clusters:
        assert not ("A" in cluster and "D" in cluster), (
            f"A and D (similarity 0.60, threshold 0.90) were merged via chaining: {clusters}"
        )


def test_tight_cluster_still_merges():
    """Sanity check the other direction: genuinely all-similar items DO cluster."""
    def sim(a, b):
        return 1.0 if a == b else 0.95

    clusters = complete_linkage_clusters(["X", "Y", "Z"], sim, threshold=0.90)
    assert len(clusters) == 1
    assert set(clusters[0]) == {"X", "Y", "Z"}


def test_dissimilar_items_stay_separate():
    def sim(a, b):
        return 1.0 if a == b else 0.1

    clusters = complete_linkage_clusters(["P", "Q", "R"], sim, threshold=0.90)
    assert len(clusters) == 3


# --- dedupe_changeset_ops -----------------------------------------------

def _task_op(ref, name, desc=""):
    return CreateTaskNodeOp(ref=ref, name=name, description=desc)


def test_no_duplicates_leaves_changeset_untouched():
    cs = ChangeSet(ops=[
        _task_op("t1", "Extract invoice fields", "Pull structured data from a PDF invoice"),
        _task_op("t2", "Notify finance team", "Send a Slack message when review is complete"),
    ])
    new_cs, report = dedupe_changeset_ops(cs)
    assert report == []
    assert len(new_cs.ops) == 2


def test_near_duplicate_siblings_are_merged():
    cs = ChangeSet(ops=[
        _task_op("t1", "Validate user input", "Check the submitted form fields are well formed"),
        _task_op("t2", "Validate the user input", "Check the submitted form fields are well formed"),
        _task_op("t3", "Send confirmation email", "Email the user once validation passes"),
        CreateEdgeOp(edge_type="PRODUCES", source_ref="t2", target_ref="t3"),
    ])
    new_cs, report = dedupe_changeset_ops(cs)

    assert len(report) == 1
    assert report[0]["canonical_ref"] == "t1"
    assert report[0]["merged_refs"] == ["t2"]

    remaining_refs = {op.ref for op in new_cs.ops if hasattr(op, "ref")}
    assert remaining_refs == {"t1", "t3"}

    # The edge that pointed at the dropped ref t2 must now point at t1,
    # not silently dangle or still reference the removed op.
    edge = next(op for op in new_cs.ops if isinstance(op, CreateEdgeOp))
    assert edge.source_ref == "t1"
    assert edge.target_ref == "t3"


def test_task_and_knowledge_node_never_merge_despite_identical_text():
    cs = ChangeSet(ops=[
        _task_op("t1", "Data retention policy"),
        CreateKnowledgeNodeOp(ref="k1", node_type="policy", name="Data retention policy"),
    ])
    new_cs, report = dedupe_changeset_ops(cs)
    assert report == []
    assert len(new_cs.ops) == 2


def test_merge_that_would_self_reference_an_edge_drops_it_instead():
    """
    If an edge's source and target both get rewritten onto the SAME
    canonical ref (both ends were duplicates of each other), the edge
    must be dropped, not written as self-referential garbage.
    """
    cs = ChangeSet(ops=[
        _task_op("t1", "Review submission", "Check the submission for completeness"),
        _task_op("t2", "Review the submission", "Check the submission for completeness"),
        CreateEdgeOp(edge_type="REQUIRES", source_ref="t1", target_ref="t2"),
    ])
    new_cs, report = dedupe_changeset_ops(cs)
    assert report and report[0]["canonical_ref"] == "t1"
    assert not any(isinstance(op, CreateEdgeOp) for op in new_cs.ops)
