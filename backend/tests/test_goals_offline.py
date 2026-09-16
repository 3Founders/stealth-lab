"""
DB-free coverage for app/services/goals.py -- the canonical Goal object
(backend/db/83_goals.sql, founder directive "ingestion.md" Sec 2-3).
Hand-rolled FakePool per this repo's own "fakes are hand-rolled per file"
convention (see CLAUDE.md).
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.services.goals import (
    AUTO_DEDUP_MAX_COSINE_DISTANCE,
    compute_simhash,
    create_goal_from_user,
    find_or_create_goal,
    get_goal,
    hamming_distance,
    normalize_goal_name,
    search_goals,
)
from app.services.v0_gate import V0Violation


def _run(coro):
    return asyncio.run(coro)


# --- normalize_goal_name -----------------------------------------------

def test_normalize_goal_name_lowercases_and_collapses_whitespace():
    assert normalize_goal_name("  Find   References  ") == "find references"


def test_normalize_goal_name_strips_punctuation():
    assert normalize_goal_name("Verify auth. behavior!!") == "verify auth behavior"


def test_normalize_goal_name_distinct_synonyms_stay_distinct():
    """Tier 1 (exact match) only -- deliberately does NOT unify true
    synonyms (that needs tiers 3-5, not implemented -- module docstring)."""
    assert normalize_goal_name("find callers") != normalize_goal_name("find references")


def test_normalize_goal_name_same_text_different_case_and_spacing_collides():
    assert normalize_goal_name("Find   References") == normalize_goal_name("find references")


# --- find_or_create_goal: FakePool -------------------------------------

class _FakeGoalsPool:
    """Simulates a real `goals` table's dedup + insert behavior closely
    enough to prove find_or_create_goal's own logic, without a real DB:
    the SELECT branch actually checks stored rows for a match (not just
    "always miss"), so a second call with the same name/scope really
    exercises the dedup path, not just the insert path."""

    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1

    @staticmethod
    def _alias_hit(row, candidate_name: str) -> bool:
        return any(
            a.strip().lower() == candidate_name.strip().lower() for a in row.get("aliases") or []
        )

    async def fetchrow(self, sql, *params):
        s = " ".join(sql.split())
        if s.startswith("SELECT"):
            normalized = params[0]
            if "scope_type = $2" in s:
                scope_type, scope_entity_id, candidate_name = params[1], params[2], params[3]
                for r in self.rows:
                    if (r["status"] != "merged" and r["scope_type"] == scope_type
                            and r["scope_entity_id"] == scope_entity_id
                            and (r["normalized_name"] == normalized or self._alias_hit(r, candidate_name))):
                        return r
            else:
                candidate_name = params[1]
                for r in self.rows:
                    if (r["status"] != "merged" and (r["scope_type"] is None or r["scope_type"] == "global")
                            and (r["normalized_name"] == normalized or self._alias_hit(r, candidate_name))):
                        return r
            return None
        # INSERT ... RETURNING id, canonical_name (trailing params are
        # migration 84's embedding/embedding_model_id/embedding_provider/
        # embedding_text_hash and migration 86's simhash -- the fake only
        # cares about simhash, for the tier 2.5 fetch() query below)
        (goal_id, canonical_name, normalized_name, description, expected_outcome,
         verification_requirement, status, provenance, created_from, owner_id,
         visibility, aliases, created_by, scope_type, scope_entity_id,
         *_embedding_fields, simhash) = params
        row = {
            "id": goal_id, "canonical_name": canonical_name, "normalized_name": normalized_name,
            "aliases": aliases, "simhash": simhash,
            "status": status, "scope_type": scope_type, "scope_entity_id": scope_entity_id,
        }
        self.rows.append(row)
        return row

    async def fetch(self, sql, *params):
        s = " ".join(sql.split())
        assert "simhash IS NOT NULL" in s, f"unexpected fetch() query in fake pool: {s}"
        if "scope_type = $1" in s:
            scope_type, scope_entity_id = params
            return [
                r for r in self.rows
                if r["status"] != "merged" and r.get("simhash") is not None
                and r["scope_type"] == scope_type and r["scope_entity_id"] == scope_entity_id
            ]
        return [
            r for r in self.rows
            if r["status"] != "merged" and r.get("simhash") is not None
            and (r["scope_type"] is None or r["scope_type"] == "global")
        ]

    async def execute(self, sql, *params):
        s = " ".join(sql.split())
        if "UPDATE goals SET aliases = array_append" in s:
            goal_id, alias = params
            for r in self.rows:
                if r["id"] == goal_id and alias not in (r.get("aliases") or []):
                    r.setdefault("aliases", []).append(alias)
        return "OK"


def test_find_or_create_goal_creates_a_new_row_when_nothing_matches():
    pool = _FakeGoalsPool()
    result = _run(find_or_create_goal(
        pool, canonical_name="Find references", scope_type="global",
        provenance="system_pending_review",
    ))
    assert result["created"] is True
    assert result["canonical_name"] == "Find references"
    assert len(pool.rows) == 1


def test_find_or_create_goal_dedups_on_exact_normalized_name_same_scope():
    pool = _FakeGoalsPool()
    first = _run(find_or_create_goal(
        pool, canonical_name="Find References", scope_type="global",
        provenance="system_pending_review",
    ))
    second = _run(find_or_create_goal(
        pool, canonical_name="find   references", scope_type="global",
        provenance="system_pending_review",
    ))
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert len(pool.rows) == 1


def test_find_or_create_goal_does_not_dedup_across_different_local_scopes():
    """ingestion.md Sec 9: a local goal at one project must not silently
    collide with a same-named local goal at a different one."""
    pool = _FakeGoalsPool()
    a = _run(find_or_create_goal(
        pool, canonical_name="regenerate derived artifacts", scope_type="project",
        scope_entity_id="repo-a", provenance="system_pending_review",
    ))
    b = _run(find_or_create_goal(
        pool, canonical_name="regenerate derived artifacts", scope_type="project",
        scope_entity_id="repo-b", provenance="system_pending_review",
    ))
    assert a["id"] != b["id"]
    assert len(pool.rows) == 2


def test_find_or_create_goal_local_scope_does_not_collide_with_global():
    pool = _FakeGoalsPool()
    g = _run(find_or_create_goal(
        pool, canonical_name="deploy safely", scope_type="global",
        provenance="system_pending_review",
    ))
    local = _run(find_or_create_goal(
        pool, canonical_name="deploy safely", scope_type="project",
        scope_entity_id="repo-a", provenance="system_pending_review",
    ))
    assert g["id"] != local["id"]


def test_find_or_create_goal_requires_scope_type():
    """V0 gate discipline (same as capture_procedure): scope is required,
    never implicit-global."""
    pool = _FakeGoalsPool()
    with pytest.raises(V0Violation):
        _run(find_or_create_goal(
            pool, canonical_name="find references", scope_type=None,
            provenance="system_pending_review",
        ))


def test_find_or_create_goal_requires_provenance():
    pool = _FakeGoalsPool()
    with pytest.raises(V0Violation):
        _run(find_or_create_goal(
            pool, canonical_name="find references", scope_type="global",
            provenance=None,
        ))


def test_find_or_create_goal_rejects_empty_canonical_name():
    pool = _FakeGoalsPool()
    with pytest.raises(ValueError):
        _run(find_or_create_goal(
            pool, canonical_name="   !!!   ", scope_type="global",
            provenance="system_pending_review",
        ))


def test_find_or_create_goal_local_scope_requires_entity_id():
    pool = _FakeGoalsPool()
    with pytest.raises(V0Violation):
        _run(find_or_create_goal(
            pool, canonical_name="find references", scope_type="project",
            provenance="system_pending_review",
        ))


# --- concurrent-insert race: UniqueViolationError retried as a dedup hit ---

class _RacingPool:
    """First INSERT attempt raises UniqueViolationError (another writer
    won the race); the retry's SELECT then finds that winner's row --
    proving find_or_create_goal treats a lost race as a successful dedup,
    never a raised error."""

    def __init__(self, winner_row):
        self._winner = winner_row
        self._insert_attempted = False

    async def fetchrow(self, sql, *params):
        s = " ".join(sql.split())
        if s.startswith("SELECT"):
            return self._winner if self._insert_attempted else None
        self._insert_attempted = True
        raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")

    async def fetch(self, sql, *params):
        return []  # tier 2.5 SimHash shortlist -- no candidates in this fake


def test_find_or_create_goal_treats_a_lost_insert_race_as_a_dedup_hit():
    winner = {"id": "winner-id", "canonical_name": "find references"}
    pool = _RacingPool(winner)
    result = _run(find_or_create_goal(
        pool, canonical_name="find references", scope_type="global",
        provenance="system_pending_review",
    ))
    assert result == {"id": "winner-id", "canonical_name": "find references", "created": False}


# --- tier 2: alias dedup -------------------------------------------------

def test_find_or_create_goal_matches_on_alias():
    """A candidate whose text matches an EXISTING row's stored alias is
    the same goal -- ingestion.md Sec 8 tier 2."""
    pool = _FakeGoalsPool()
    original = _run(find_or_create_goal(
        pool, canonical_name="find references", scope_type="global",
        provenance="system_pending_review", aliases=["locate symbol usages"],
    ))
    matched = _run(find_or_create_goal(
        pool, canonical_name="locate symbol usages", scope_type="global",
        provenance="system_pending_review",
    ))
    assert matched["created"] is False
    assert matched["id"] == original["id"]
    assert len(pool.rows) == 1


# --- tier 2.5: text SimHash near-duplicate dedup (always on) -----------

def test_compute_simhash_is_stable_across_calls():
    assert compute_simhash("find all callers of a function") == compute_simhash(
        "find all callers of a function"
    )


def test_compute_simhash_ignores_case_and_word_order_via_normalization():
    """Uses the same normalize_goal_name() tier 1 does, but SimHash's
    bag-of-tokens voting also makes it order-insensitive -- unlike tier
    1's exact string match."""
    assert compute_simhash("Find All Callers") == compute_simhash("callers all find")


def test_hamming_distance_zero_for_identical_hashes():
    h = compute_simhash("deploy the service safely")
    assert hamming_distance(h, h) == 0


def test_hamming_distance_positive_for_different_texts():
    a = compute_simhash("deploy the service safely")
    b = compute_simhash("delete all user records permanently")
    assert hamming_distance(a, b) > 0


def test_simhash_to_int64_stays_in_postgres_bigint_range():
    """Real live-DB rehearsal bug: compute_simhash's unsigned 0..2**64-1
    output overflows Postgres BIGINT (signed, -2**63..2**63-1) whenever
    the top bit is set. Migration 86's `simhash` column is exactly that
    type -- this conversion is required at every INSERT, not optional."""
    from app.services.goals import _simhash_to_int64

    unsigned_with_top_bit_set = (1 << 63) + 42
    signed = _simhash_to_int64(unsigned_with_top_bit_set)
    assert -(1 << 63) <= signed < (1 << 63)
    assert signed < 0


def test_hamming_distance_correct_after_a_signed_round_trip():
    """hamming_distance must give the same answer whether its inputs are
    fresh compute_simhash() output or a value that went through the
    signed-BIGINT round trip -- a real DB row's simhash comes back as a
    (possibly negative) Python int, not the original unsigned value."""
    from app.services.goals import _simhash_to_int64

    h = compute_simhash("find all callers of a function")
    assert hamming_distance(h, _simhash_to_int64(h)) == 0


def test_find_or_create_goal_auto_merges_a_near_duplicate_by_simhash_alone():
    """No embedder, no client -- tier 2.5 is zero-cost and always on, so a
    trivial near-duplicate (one word dropped) merges without either opt-in
    dependency."""
    pool = _FakeGoalsPool()
    first = _run(find_or_create_goal(
        pool, canonical_name="find all callers of the function", scope_type="global",
        provenance="system_pending_review",
    ))
    second = _run(find_or_create_goal(
        pool, canonical_name="find all callers of function", scope_type="global",
        provenance="system_pending_review",
    ))
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert len(pool.rows) == 1


def test_find_or_create_goal_simhash_tier_does_not_merge_unrelated_goals():
    pool = _FakeGoalsPool()
    first = _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review",
    ))
    second = _run(find_or_create_goal(
        pool, canonical_name="deploy the service safely", scope_type="global",
        provenance="system_pending_review",
    ))
    assert second["created"] is True
    assert second["id"] != first["id"]


# --- tier 5: LLM adjudication on an ambiguous near-match (opt-in via `client`) --

class _FakeAdjudicationClient:
    """Minimal OpenAI-compatible chat.completions.create() stand-in --
    returns a caller-controlled verdict, records the call for assertion."""

    class _Choice:
        def __init__(self, content):
            self.message = type("_Msg", (), {"content": content})()

    class _Response:
        def __init__(self, content):
            self.choices = [_FakeAdjudicationClient._Choice(content)]

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _FakeAdjudicationClient._Response(self._outer._content)

    class _Chat:
        def __init__(self, outer):
            self.completions = _FakeAdjudicationClient._Completions(outer)

    def __init__(self, content):
        self._content = content
        self.calls: list[dict] = []
        self.chat = _FakeAdjudicationClient._Chat(self)


def test_find_or_create_goal_tier5_merges_and_adds_alias_on_same_verdict():
    pool = _FakeSemanticGoalsPool(
        semantic_distance=AUTO_DEDUP_MAX_COSINE_DISTANCE + 0.05,  # ambiguous band
    )
    first = _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    client = _FakeAdjudicationClient('{"same": true}')
    second = _run(find_or_create_goal(
        pool, canonical_name="locate every invocation site", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]), client=client,
    ))
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert len(client.calls) == 1
    assert "locate every invocation site" in pool.rows[0]["aliases"]


def test_find_or_create_goal_tier5_creates_new_row_on_different_verdict():
    pool = _FakeSemanticGoalsPool(
        semantic_distance=AUTO_DEDUP_MAX_COSINE_DISTANCE + 0.05,
    )
    first = _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    client = _FakeAdjudicationClient('{"same": false}')
    second = _run(find_or_create_goal(
        pool, canonical_name="deploy the service safely", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.9] * 4]), client=client,
    ))
    assert second["created"] is True
    assert second["id"] != first["id"]
    assert len(client.calls) == 1


def test_find_or_create_goal_tier5_skipped_without_client_falls_through_to_create():
    pool = _FakeSemanticGoalsPool(
        semantic_distance=AUTO_DEDUP_MAX_COSINE_DISTANCE + 0.05,
    )
    _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    second = _run(find_or_create_goal(
        pool, canonical_name="locate every invocation site", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    assert second["created"] is True


def test_find_or_create_goal_tier5_fails_closed_on_malformed_response():
    pool = _FakeSemanticGoalsPool(
        semantic_distance=AUTO_DEDUP_MAX_COSINE_DISTANCE + 0.05,
    )
    _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    client = _FakeAdjudicationClient("not json at all")
    second = _run(find_or_create_goal(
        pool, canonical_name="locate every invocation site", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]), client=client,
    ))
    assert second["created"] is True


def test_find_or_create_goal_tier5_not_reached_beyond_ambiguous_band():
    """A distance past AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE never even asks
    the model -- tier 5 is for the close-but-uncertain band only."""
    from app.services.goals import AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE

    pool = _FakeSemanticGoalsPool(
        semantic_distance=AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE + 0.1,
    )
    _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    client = _FakeAdjudicationClient('{"same": true}')
    second = _run(find_or_create_goal(
        pool, canonical_name="deploy the service safely", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.9] * 4]), client=client,
    ))
    assert second["created"] is True
    assert len(client.calls) == 0


# --- tier 3/4: embedding similarity dedup (opt-in via `embedder`) -------

class _FakeEmbedMeta:
    def __init__(self, model_id="fake-model", provider="fake", text_sha256="deadbeef"):
        self.model_id = model_id
        self.provider = provider
        self.text_sha256 = text_sha256


class _FakeEmbedder:
    """Returns a caller-controlled vector per call, in order."""

    def __init__(self, vectors):
        self._vectors = list(vectors)
        self.calls: list[tuple[str, str]] = []

    async def embed_one_with_metadata(self, text, input_type="document"):
        self.calls.append((text, input_type))
        return self._vectors.pop(0), _FakeEmbedMeta()


class _FakeSemanticGoalsPool(_FakeGoalsPool):
    """Extends the exact/alias fake with a fixed cosine-distance answer
    for the embedding-similarity SELECT (ORDER BY embedding <=> ...) --
    good enough to prove find_or_create_goal's OWN threshold logic
    without a real pgvector column."""

    def __init__(self, semantic_distance):
        super().__init__()
        self._semantic_distance = semantic_distance
        self.embedding_insert_seen = False

    async def fetchrow(self, sql, *params):
        s = " ".join(sql.split())
        if "embedding <=> $1::vector AS dist" in s:
            if not self.rows:
                return None
            r = self.rows[0]
            return {"id": r["id"], "canonical_name": r["canonical_name"], "dist": self._semantic_distance}
        return await super().fetchrow(sql, *params)


def test_find_or_create_goal_auto_merges_a_near_identical_embedding():
    pool = _FakeSemanticGoalsPool(semantic_distance=AUTO_DEDUP_MAX_COSINE_DISTANCE - 0.01)
    first = _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    second = _run(find_or_create_goal(
        pool, canonical_name="locate every caller of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    assert second["created"] is False
    assert second["id"] == first["id"]


def test_find_or_create_goal_does_not_merge_a_merely_similar_embedding():
    """ingestion.md Sec 8: never auto-merge on loose similarity -- only a
    near-identical (distance <= AUTO_DEDUP_MAX_COSINE_DISTANCE) match."""
    pool = _FakeSemanticGoalsPool(semantic_distance=AUTO_DEDUP_MAX_COSINE_DISTANCE + 0.2)
    first = _run(find_or_create_goal(
        pool, canonical_name="find all callers of a function", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.1] * 4]),
    ))
    second = _run(find_or_create_goal(
        pool, canonical_name="deploy the service safely", scope_type="global",
        provenance="system_pending_review", embedder=_FakeEmbedder([[0.9] * 4]),
    ))
    assert second["created"] is True
    assert second["id"] != first["id"]


def test_find_or_create_goal_without_embedder_never_calls_embedding_path():
    """The default (no `embedder`) behavior is byte-identical to before
    this feature existed -- no embedding cost added to an existing caller
    that doesn't opt in."""
    pool = _FakeGoalsPool()
    result = _run(find_or_create_goal(
        pool, canonical_name="find references", scope_type="global",
        provenance="system_pending_review",
    ))
    assert result["created"] is True
    assert "embedding" not in " ".join(str(r) for r in pool.rows)


# --- search_goals ---------------------------------------------------------

class _SearchFakePool:
    """Answers search_goals' lexical leg, semantic leg, and hydration
    query with fixed data -- proves the RRF fuse logic, not real
    Postgres full-text/vector ranking."""

    def __init__(self, lexical_ids, semantic_ids, rows_by_id):
        self._lexical_ids = lexical_ids
        self._semantic_ids = semantic_ids
        self._rows_by_id = rows_by_id

    async def fetch(self, sql, *params):
        s = " ".join(sql.split())
        if "ts_rank" in s:
            return [{"id": i} for i in self._lexical_ids]
        if "embedding <=>" in s:
            return [{"id": i} for i in self._semantic_ids]
        if "WHERE id = ANY" in s:
            ids = params[0]
            return [self._rows_by_id[i] for i in ids if i in self._rows_by_id]
        raise AssertionError("unexpected fetch: " + s[:80])


def _goal_row(goal_id, name):
    return {
        "id": goal_id, "canonical_name": name, "description": None, "status": "active",
        "scope_type": "global", "scope_entity_id": None,
        "expected_outcome": {}, "verification_requirement": {},
    }


def test_search_goals_requires_at_least_one_query_input():
    pool = _SearchFakePool([], [], {})
    with pytest.raises(ValueError):
        _run(search_goals(pool))


def test_search_goals_fuses_lexical_and_semantic_legs():
    rows = {"g1": _goal_row("g1", "find references"), "g2": _goal_row("g2", "deploy safely")}
    # g1 ranks well on both legs; g2 only appears in the semantic leg.
    pool = _SearchFakePool(lexical_ids=["g1"], semantic_ids=["g1", "g2"], rows_by_id=rows)
    results = _run(search_goals(pool, query_text="find refs", query_embedding=[0.1] * 4, limit=5))
    ids = [r["id"] for r in results]
    assert ids[0] == "g1"  # present in both legs -> highest fused score
    assert "g2" in ids


def test_search_goals_lexical_only_when_no_embedding_given():
    rows = {"g1": _goal_row("g1", "find references")}
    pool = _SearchFakePool(lexical_ids=["g1"], semantic_ids=["should-not-be-queried"], rows_by_id=rows)
    results = _run(search_goals(pool, query_text="find refs"))
    assert [r["id"] for r in results] == ["g1"]


# --- get_goal ---------------------------------------------------------

class _GetGoalFakePool:
    def __init__(self, goal_row, procedures=(), implementations=()):
        self._goal_row = goal_row
        self._procedures = list(procedures)
        self._implementations = list(implementations)

    async def fetchrow(self, sql, *params):
        if "FROM goals WHERE id" in sql:
            return self._goal_row
        raise AssertionError("unexpected fetchrow")

    async def fetch(self, sql, *params):
        if "FROM procedures WHERE achieves_goal_id" in sql:
            return self._procedures
        if "FROM implementations WHERE goal_id" in sql:
            return self._implementations
        raise AssertionError("unexpected fetch: " + sql[:80])


def test_get_goal_returns_none_for_a_missing_row():
    pool = _GetGoalFakePool(goal_row=None)
    assert _run(get_goal(pool, "missing-id")) is None


def test_get_goal_attaches_procedures_and_implementations_and_hides_embedding():
    row = {"id": "g1", "canonical_name": "find references", "embedding": "[0.1,0.2]"}
    pool = _GetGoalFakePool(
        goal_row=row,
        procedures=[{"id": "p1", "procedure_id": "pp1", "name": "grep-based search"}],
        implementations=[{"id": "i1", "name": "ripgrep"}],
    )
    result = _run(get_goal(pool, "g1"))
    assert result["procedures"] == [{"id": "p1", "procedure_id": "pp1", "name": "grep-based search"}]
    assert result["implementations"] == [{"id": "i1", "name": "ripgrep"}]
    assert "embedding" not in result


# --- create_goal_from_user ---------------------------------------------

def test_create_goal_from_user_creates_when_no_near_matches(monkeypatch):
    async def _no_matches(pool, **kw):
        return []

    monkeypatch.setattr("app.services.goals.search_goals", _no_matches)
    pool = _FakeGoalsPool()
    result = _run(create_goal_from_user(
        pool, canonical_name="reconcile schema drift", scope_type="global", owner_id="u1",
    ))
    assert result["outcome"] == "created"
    assert result["goal"]["created"] is True


def test_create_goal_from_user_surfaces_near_matches_without_writing(monkeypatch):
    async def _some_matches(pool, **kw):
        return [_goal_row("existing-1", "reconcile schema differences")]

    monkeypatch.setattr("app.services.goals.search_goals", _some_matches)
    pool = _FakeGoalsPool()
    result = _run(create_goal_from_user(
        pool, canonical_name="reconcile schema drift", scope_type="global", owner_id="u1",
    ))
    assert result["outcome"] == "near_matches"
    assert result["candidates"][0]["id"] == "existing-1"
    assert pool.rows == []  # nothing written


def test_create_goal_from_user_allow_create_anyway_bypasses_near_match_check(monkeypatch):
    async def _should_not_be_called(pool, **kw):
        raise AssertionError("search_goals must not run when allow_create_anyway=True")

    monkeypatch.setattr("app.services.goals.search_goals", _should_not_be_called)
    pool = _FakeGoalsPool()
    result = _run(create_goal_from_user(
        pool, canonical_name="reconcile schema drift", scope_type="global", owner_id="u1",
        allow_create_anyway=True,
    ))
    assert result["outcome"] == "created"


def test_create_goal_from_user_does_not_surface_its_own_exact_match(monkeypatch):
    """find_or_create_goal's own tier-1 exact match would resolve this
    identically anyway -- must not be shown back as a 'near match'."""
    async def _exact_match_only(pool, **kw):
        return [_goal_row("existing-1", "Reconcile Schema Drift")]

    monkeypatch.setattr("app.services.goals.search_goals", _exact_match_only)
    pool = _FakeGoalsPool()
    pool.rows.append({
        "id": "existing-1", "canonical_name": "Reconcile Schema Drift",
        "normalized_name": normalize_goal_name("Reconcile Schema Drift"),
        "status": "active", "scope_type": "global", "scope_entity_id": None,
    })
    result = _run(create_goal_from_user(
        pool, canonical_name="reconcile schema drift", scope_type="global", owner_id="u1",
    ))
    assert result["outcome"] == "matched"
    assert result["goal"]["id"] == "existing-1"
