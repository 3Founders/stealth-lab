"""
Offline proving tests for claim_family.py (Band 2.6, spec v4 §10).

Two layers, both DB-free:

  pure core   -- normalization identity, cascade verdicts, blocking. The
                 load-bearing tooth everywhere: SIMILARITY CAN NEVER DECIDE
                 IDENTITY. A max-similarity candidate that fails the
                 proposition gate is distinct; an out-of-scope twin is not
                 even a candidate.
  DB boundary -- a fake pool records every query so tests prove SQL
                 CONTENT (hard scope terms, TMS OUT exclusion, visibility
                 fragment, LIMIT) and WRITE BEHAVIOR (hub creation stamps,
                 membership idempotency, nothing written without a
                 same_family winner).
"""
import asyncio

import pytest

from app.services.access import AccessScope
from app.services.claim_family import (
    BLOCK_LIMIT,
    DISTINCT,
    GENERALIZES,
    RELATED_FAMILY,
    RESOLVER_VERSION,
    SAME_FAMILY,
    SPECIALIZES,
    CandidateClaim,
    Proposition,
    block_candidates,
    decide,
    normalize,
    proposition_from_claim_row,
    resolve_claim_family,
)
from app.services.v0_gate import V0Violation

PROJECT = "proj-1"
SUBJ_ID = "00000000-0000-4000-8000-000000000001"


def _claim_row(
    claim_id=SUBJ_ID,
    subject="data augmentation",
    predicate="improves",
    object="image classification",
    proposition_type="causal",
    conditions=None,
    scope=("project", PROJECT),
    truth_state=None,
    t_invalid=False,
    statement=None,
    **extra,
):
    props = {"statement": statement or f"{subject} {predicate} {object}"}
    if conditions:
        props["conditions"] = list(conditions)
    if truth_state:
        props["truth_state"] = truth_state
    row = {
        "id": claim_id,
        "name": (statement or f"{subject} {predicate} {object}")[:200],
        "node_type": "claim",
        "t_invalid": "2026-01-01" if t_invalid else None,
        "scope_type": scope[0],
        "scope_entity_id": scope[1],
        "owner_id": None,
        "visibility": "public",
        "subject": subject,
        "predicate": predicate,
        "object": object,
        "proposition_type": proposition_type,
        "properties": props,
        "embedding": None,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Pure core: normalization + proposition identity
# ---------------------------------------------------------------------------


def test_normalize_is_insensitive_to_case_punct_articles_whitespace():
    assert normalize("The Data augmentation improves Image Classification.") == normalize(
        "  data AUGMENTATION improves image classification "
    )
    assert normalize("im-proves") == "im proves"  # punctuation becomes a gap, never glue
    # meaning-bearing words are never dropped
    assert normalize("does not improve") != normalize("improves")


def test_canonical_key_separates_slots_and_type():
    base = Proposition(subject="Data augmentation", predicate="improves", object="Image classification")
    key = base.canonical_key
    assert key == Proposition(
        subject="the data augmentation", predicate="IMPROVES", object="image classification."
    ).canonical_key
    assert key != Proposition(
        subject="Gaussian noise", predicate="improves", object="tabular accuracy"
    ).canonical_key
    # type is part of identity: same SPO, different type => different key
    assert key != Proposition(
        subject="data augmentation", predicate="improves", object="image classification",
        proposition_type="comparative",
    ).canonical_key


# ---------------------------------------------------------------------------
# Pure core: decide() cascade
# ---------------------------------------------------------------------------


def _cand(cid="c2", sim=0.5, contradicts=False, **prop_kwargs):
    return CandidateClaim(
        claim_id=cid,
        proposition=Proposition(**prop_kwargs) if prop_kwargs else Proposition(),
        similarity=sim,
        contradicts_subject=contradicts,
    )


_SUBJECT_PROP = Proposition(
    subject="data augmentation",
    predicate="improves",
    object="image classification",
    proposition_type="causal",
    conditions=("random crop", "imagenet"),
)


def test_same_family_requires_identity_and_matching_conditions_in_order():
    d = decide(
        _SUBJECT_PROP,
        _cand(
            subject="The data augmentation",
            predicate="improves",
            object="IMAGE CLASSIFICATION",
            proposition_type="causal",
            conditions=("a random crop", "imagenet"),
        ),
    )
    assert d.verdict == SAME_FAMILY
    # stage order IS the spec §10 mapping: contradiction dominates first,
    # then proposition identity, then conditions
    assert d.stages == ("contradiction_check", "proposition_match", "condition_match")


def test_conditions_divergence_is_related_never_merged():
    d = decide(_SUBJECT_PROP, _cand(subject="data augmentation", predicate="improves", object="image classification", proposition_type="causal", conditions=("gaussian noise",)))
    assert d.verdict == RELATED_FAMILY
    assert "condition_match" in d.stages


def test_negation_flip_is_distinct_despite_shared_spo():
    d = decide(_SUBJECT_PROP, _cand(subject="data augmentation", predicate="improves", object="image classification", proposition_type="negative"))
    assert d.verdict == DISTINCT
    assert d.stages == ("contradiction_check",)


def test_explicit_contradiction_beats_perfect_similarity():
    """THE spec-10 tooth: similarity is candidate generation, not identity.
    A CONTRADICTS-linked twin at similarity 1.0 never merges."""
    d = decide(
        _SUBJECT_PROP,
        CandidateClaim(
            claim_id="twin",
            proposition=Proposition(subject="data augmentation", predicate="improves", object="image classification", proposition_type="causal"),
            similarity=1.0,
            contradicts_subject=True,
        ),
    )
    assert d.verdict == DISTINCT
    assert d.stages == ("contradiction_check",)
    assert "CONTRADICTS" in d.reason


def test_statement_only_claims_fail_closed():
    """No structured proposition on the candidate side => no merge, even
    against identical free text -- textual identity without structure is the
    similarity-as-identity mistake §10 forbids."""
    subj = Proposition()  # nothing extracted
    d = decide(subj, _cand())
    assert d.verdict == DISTINCT


def test_generalizes_direction_matches_spec_hierarchy():
    """§10's own example: 'augmentation can improve learning' generalizes
    'image augmentation -> image classification' (fewer qualifiers =
    broader). Subject is the broader one here."""
    subj = Proposition(subject="augmentation", predicate="improve", object="learning")
    d = decide(subj, _cand(subject="image augmentation", predicate="improve", object="image classification"))
    assert d.verdict == GENERALIZES
    assert "ontology_overlap" in d.stages


def test_specializes_direction_is_the_mirror():
    subj = Proposition(subject="image augmentation", predicate="improve", object="image classification")
    d = decide(subj, _cand(subject="augmentation", predicate="improve", object="learning"))
    assert d.verdict == SPECIALIZES


def test_related_on_two_of_three_slots_distinct_on_one():
    two = decide(_SUBJECT_PROP, _cand(subject="data augmentation", predicate="improves", object="tabular accuracy"))
    assert two.verdict == RELATED_FAMILY
    one = decide(_SUBJECT_PROP, _cand(subject="Gaussian noise", predicate="improves", object="tabular accuracy"))
    assert one.verdict == DISTINCT


# ---------------------------------------------------------------------------
# Pure core: blocking -- scope is the hard filter, similarity only ranks
# ---------------------------------------------------------------------------


def _subject_row(**over):
    return _claim_row(**over)


def test_blocking_scope_pair_is_a_hard_filter():
    rows = [
        _claim_row("same-project-twin"),  # identical triple, same project -> IN
        _claim_row("other-project", scope=("project", "proj-2")),
        _claim_row("unscoped", scope=(None, None)),
        _claim_row("global-twin", scope=("global", None)),
    ]
    cands = block_candidates(_subject_row(), rows)
    assert [c.claim_id for c in cands] == ["same-project-twin"]
    # and this must hold no matter how similar the rejects are
    for r in rows[1:]:
        r["_similarity"] = 0.99
    assert [c.claim_id for c in block_candidates(_subject_row(), rows)] == ["same-project-twin"]


def test_blocking_excludes_out_invalid_and_self():
    rows = [
        _subject_row(),  # self
        _claim_row("dead", truth_state="OUT"),
        _claim_row("invalid", t_invalid=True),
        _claim_row("live"),
    ]
    assert [c.claim_id for c in block_candidates(_subject_row(), rows)] == ["live"]


def test_blocking_ranks_by_similarity_then_caps_at_limit():
    rows = [_claim_row(f"c{i:02d}") for i in range(BLOCK_LIMIT + 5)]
    for i, r in enumerate(rows):
        r["_similarity"] = float(i) / len(rows)  # ascending; best is last
    cands = block_candidates(_subject_row(), rows)
    assert len(cands) == BLOCK_LIMIT
    assert cands[0].claim_id == f"c{BLOCK_LIMIT + 4:02d}"
    # explicit limit override is honored (retunable, not magic inline)
    assert len(block_candidates(_subject_row(), rows, limit=3)) == 3


def test_contradicts_flag_survives_blocking():
    rows = [_claim_row("foe")]
    rows[0]["_contradicts_subject"] = True
    cands = block_candidates(_subject_row(), rows)
    assert cands[0].contradicts_subject is True


def test_unscoped_subject_is_a_v0_violation():
    with pytest.raises(V0Violation):
        block_candidates(_subject_row(scope=(None, None)), [])


def test_row_shaping_reads_structured_columns_with_properties_fallback():
    row = _claim_row(subject=None, predicate=None, object=None)
    row["properties"]["subject"] = "fallback subject"
    prop = proposition_from_claim_row(row)
    assert prop.subject == "fallback subject"
    assert prop.conditions == ()
    row["properties"]["conditions"] = [" cond-a ", ""]
    # empty condition strings are dropped at shaping time
    assert proposition_from_claim_row(row).conditions == (" cond-a ",)


# ---------------------------------------------------------------------------
# DB boundary: fake pool proves SQL content + write behavior
# ---------------------------------------------------------------------------


class FakeConn:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def transaction(self):
        # asyncpg's transaction() returns an object entered with `async with`;
        # FakeConn itself is that object (its __aenter__/__aexit__ are no-ops).
        return self

    async def fetchrow(self, sql, *params):
        return self.pool._call(sql, params)

    async def fetch(self, sql, *params):
        return self.pool._call(sql, params)

    async def fetchval(self, sql, *params):
        return self.pool._call(sql, params)

    async def execute(self, sql, *params):
        self.pool.executes.append((" ".join(sql.split()), params))
        return "OK"


class FakePool:
    """
    Answers the four query shapes resolve_claim_family() issues and records
    everything. fetchrow -> subject select; fetch -> CONTRADICTS scan or
    blocking select (keyed on SQL text); fetchval -> hub lookup / hub
    insert / membership-exists check.
    """

    def __init__(self, *, subject=None, candidates=(), contradicts=(), hub_id=None, already_member=False):
        self.subject = subject
        self.candidates = list(candidates)
        self.contradicts = list(contradicts)
        self.hub_id = hub_id
        self.already_member = already_member
        self.calls = []
        self.executes = []
        self._hub_seq = 0

    def _call(self, sql, params):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        if flat.startswith("SELECT id, node_type"):
            return self.subject
        if "COALESCE(properties->>'truth_state'" in flat:
            return self.candidates  # the blocking SELECT
        if "custom_edge_type = 'CONTRADICTS'" in flat:
            return [
                {"source_id": s, "target_id": t} for (s, t) in self.contradicts
            ]
        if "INSERT INTO knowledge_nodes" in flat:
            self._hub_seq += 1
            return f"fam-new-{self._hub_seq}"
        if "node_type = 'claim_family'" in flat:
            return self.hub_id
        if "SELECT 1 FROM edges" in flat:
            return 1 if self.already_member else None
        raise AssertionError(f"unexpected query: {flat[:120]}")

    def acquire(self):
        return FakeConn(self)


def _run(pool, **kw):
    return asyncio.run(resolve_claim_family(pool, claim_id=SUBJ_ID, **kw))


def test_winner_creates_hub_with_mechanical_stamps_and_membership():
    pool = FakePool(
        subject=_subject_row(),
        candidates=[_claim_row("00000000-0000-4000-8000-000000000002")],
    )
    res = _run(pool)
    assert res.attached is True and res.verdict == SAME_FAMILY
    assert res.family_node_id == "fam-new-1"

    # the hub INSERT rides fetchval (RETURNING id); the edge rides execute
    all_sql = [sql for sql, _ in pool.calls] + [sql for sql, _ in pool.executes]
    inserts = [sql for sql in all_sql if "INSERT INTO" in sql]
    hub_inserts = [s for s in inserts if "INSERT INTO knowledge_nodes" in s]
    edge_inserts = [s for s in inserts if "INSERT INTO edges" in s]
    assert len(hub_inserts) == 1 and len(edge_inserts) == 1
    hub_sql = hub_inserts[0]
    assert "'claim_family'" in hub_sql
    assert "system_pending_review" in hub_sql  # mechanically produced, unreviewed
    assert RESOLVER_VERSION in hub_sql  # V0 derived-object stamp
    edge_sql = edge_inserts[0]
    assert "'OWNS'" in edge_sql and "'FAMILY_MEMBER'" in edge_sql
    updates = [s for s, _ in pool.executes if s.startswith("UPDATE knowledge_nodes")]
    assert len(updates) == 1 and "member_count" in updates[0]


def test_joiner_reuses_existing_hub_without_recreating_it():
    pool = FakePool(
        subject=_subject_row(),
        candidates=[_claim_row("00000000-0000-4000-8000-000000000002")],
        hub_id="fam-existing-1",
    )
    res = _run(pool)
    assert res.attached and res.family_node_id == "fam-existing-1"
    every_sql = [s for s, _ in pool.calls] + [s for s, _ in pool.executes]
    assert not any("INSERT INTO knowledge_nodes" in s for s in every_sql)


def test_already_member_is_fully_idempotent():
    pool = FakePool(
        subject=_subject_row(),
        candidates=[_claim_row("00000000-0000-4000-8000-000000000002")],
        hub_id="fam-existing-1",
        already_member=True,
    )
    res = _run(pool)
    assert res.family_node_id == "fam-existing-1"
    assert res.attached is False  # edge existed; nothing re-written
    assert pool.executes == [] and not any("INSERT" in s for s, _ in pool.calls)


def test_no_same_family_winner_writes_nothing_and_reports_best_verdict():
    pool = FakePool(
        subject=_subject_row(),
        candidates=[
            _claim_row("00000000-0000-4000-8000-000000000002", object="tabular accuracy"),
        ],
    )
    res = _run(pool)
    assert res.verdict == RELATED_FAMILY
    assert res.family_node_id is None and res.attached is False
    assert pool.executes == []


def test_blocking_sql_carries_every_hard_term():
    pool = FakePool(subject=_subject_row(), candidates=[])
    _run(pool)
    blocking = [sql for sql, _ in pool.calls if "COALESCE(properties->>'truth_state'" in sql]
    assert blocking, "blocking SELECT missing"
    sql = blocking[0]
    assert "node_type = 'claim'" in sql
    assert "t_invalid IS NULL" in sql
    assert "id <> $1::uuid" in sql
    assert "AND scope_type = $2 AND scope_entity_id = $3" in sql  # hard pair
    assert "<> 'OUT'" in sql  # TMS readability at the family layer too
    assert "LIMIT $4" in sql  # rank cap, never a gate skip
    assert "(TRUE) AND (TRUE)" in sql or "visibility = 'public'" in sql  # viewer fragment present
    # (default scope is unrestricted(), whose predicate pair -- visibility
    # AND tenancy, both axes now via scope_predicates -- is the visible
    # literal TRUE: permissiveness must be readable in the query text)
    # params bind the subject's real scope pair and the cap
    bparams = [p for s, p in pool.calls if "COALESCE(properties->>'truth_state'" in s][0]
    assert bparams[0] == SUBJ_ID and bparams[1] == "project" and bparams[2] == PROJECT
    assert bparams[3] == BLOCK_LIMIT


def test_embedded_subject_orders_by_cosine_cold_subject_does_not():
    emb = _subject_row(embedding="[0.1,0.2]")
    pool = FakePool(subject=emb)
    _run(pool)
    sql = [s for s, _ in pool.calls if "ORDER BY embedding <=> " in s]
    assert sql, "embedded subject must order by cosine distance"

    pool2 = FakePool(subject=_subject_row())
    _run(pool2)
    cold = [s for s, _ in pool2.calls if "COALESCE(properties->>'truth_state'" in s][0]
    assert "ORDER BY t_valid DESC" in cold and "<=>" not in cold


def test_contradicts_edge_marks_candidate_and_blocks_merge_end_to_end():
    foe = "00000000-0000-4000-8000-000000000002"
    pool = FakePool(
        subject=_subject_row(),
        candidates=[_claim_row(foe)],  # identical triple
        contradicts=[(foe, SUBJ_ID)],
    )
    res = _run(pool)
    assert res.decisions[0].verdict == DISTINCT
    assert res.verdict == DISTINCT and res.attached is False
    assert pool.executes == []


def test_unresolvable_subject_returns_none_before_any_candidate_work():
    for bad in (
        None,
        _subject_row(node_type="procedure"),
        _subject_row(t_invalid=True),
        _subject_row(truth_state="OUT"),
    ):
        pool = FakePool(subject=bad)
        assert _run(pool) is None
        kinds = [s for s, _ in pool.calls]
        assert sum(1 for s in kinds if s.startswith("SELECT id, node_type")) == len(kinds), (
            "no candidate/edge/hub work may happen for an unresolvable subject"
        )


def test_scopeless_subject_row_raises_v0_even_in_db_path():
    pool = FakePool(subject=_subject_row(scope=(None, None)))
    with pytest.raises(V0Violation):
        _run(pool)


def test_attach_false_decides_but_never_writes():
    pool = FakePool(
        subject=_subject_row(),
        candidates=[_claim_row("00000000-0000-4000-8000-000000000002")],
    )
    res = _run(pool, attach=False)
    assert res.verdict == SAME_FAMILY and res.family_node_id is None
    assert pool.executes == []


def test_viewer_scope_threads_owner_predicate_into_queries():
    pool = FakePool(subject=_subject_row(), candidates=[])
    asyncio.run(
        resolve_claim_family(
            pool, claim_id=SUBJ_ID, access_scope=AccessScope.for_user("user-7")
        )
    )
    blocking_sql, blocking_params = next(
        (s, p) for s, p in pool.calls if "COALESCE(properties->>'truth_state'" in s
    )
    assert "owner_id = $2" in blocking_sql  # viewer predicate, right index
    assert blocking_params[1] == "user-7"
