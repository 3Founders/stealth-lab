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
    GoalQualityRejected,
    create_goal_from_user,
    describe_goal_quality_issue,
    find_or_create_goal,
    get_goal,
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
        if s.startswith("INSERT INTO identity_decisions"):
            return {"id": "00000000-0000-0000-0000-00000000dec1"}
        # INSERT INTO goals ... RETURNING id, canonical_name, home_shard_id
        # (trailing params: embedding, embedding_model_id, provider, text hash, home_shard_id)
        (goal_id, canonical_name, normalized_name, description, expected_outcome,
         verification_requirement, status, provenance, created_from, owner_id,
         visibility, aliases, created_by, scope_type, scope_entity_id,
         *_embedding_fields, home_shard_id) = params
        row = {
            "id": goal_id, "canonical_name": canonical_name, "normalized_name": normalized_name,
            "aliases": aliases, "home_shard_id": home_shard_id,
            "status": status, "scope_type": scope_type, "scope_entity_id": scope_entity_id,
        }
        self.rows.append(row)
        return row

    async def fetch(self, sql, *params):
        s = " ".join(sql.split())
        if "FROM knowledge_shards" in s:
            return [{"shard_id": "K000", "status": "active", "weight": 100, "dsn_env": None, "capacity_rows": None}]
        # FTS/vector candidate generation: this fake holds no semantically
        # similar rows (semantic identity is covered by test_goal_identity_e2e.py)
        assert "FROM goals" in s, f"unexpected fetch() query in fake pool: {s}"
        return []

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


# --- Sec 20: goal quality gate -----------------------------------------

@pytest.mark.parametrize("bad_name", [
    "use rg command",
    "fix stuff",
    "run this exact command in repo X",
    "rg",
    "src/generated/api.yaml",
    "in repo stealthlab",
])
def test_describe_goal_quality_issue_flags_ingestion_md_bad_examples(bad_name):
    assert describe_goal_quality_issue(bad_name) is not None


@pytest.mark.parametrize("good_name", [
    "find references",
    "verify generated consistency",
    "safely deploy service",
    "inspect semantic code delta",
])
def test_describe_goal_quality_issue_passes_ingestion_md_good_examples(good_name):
    assert describe_goal_quality_issue(good_name) is None


def test_find_or_create_goal_rejects_a_low_quality_new_goal():
    pool = _FakeGoalsPool()
    with pytest.raises(GoalQualityRejected):
        _run(find_or_create_goal(
            pool, canonical_name="use rg command", scope_type="global",
            provenance="system_pending_review",
        ))
    assert pool.rows == []


def test_find_or_create_goal_quality_gate_never_blocks_a_dedup_hit_on_existing_row():
    """The gate only stops NEW rows -- an existing (even low-quality)
    row must still be reachable via tier 1 exact match, never re-judged
    retroactively (CLAUDE.md: no backfills, legacy rows stay quarantined)."""
    pool = _FakeGoalsPool()
    pool.rows.append({
        "id": "legacy-bad-goal", "canonical_name": "use rg command",
        "normalized_name": normalize_goal_name("use rg command"),
        "aliases": [], "status": "active", "scope_type": "global", "scope_entity_id": None,
    })
    result = _run(find_or_create_goal(
        pool, canonical_name="use rg command", scope_type="global",
        provenance="system_pending_review",
    ))
    assert result == {"id": "legacy-bad-goal", "canonical_name": "use rg command", "created": False}


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
        if s.startswith("INSERT INTO identity_decisions"):
            return {"id": "dec"}
        self._insert_attempted = True
        raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")

    async def fetch(self, sql, *params):
        if "FROM knowledge_shards" in " ".join(sql.split()):
            return [{"shard_id": "K000", "status": "active", "weight": 100, "dsn_env": None, "capacity_rows": None}]
        return []  # no semantic candidates in this fake


def test_find_or_create_goal_treats_a_lost_insert_race_as_a_dedup_hit():
    winner = {"id": "winner-id", "canonical_name": "find references"}
    pool = _RacingPool(winner)
    result = _run(find_or_create_goal(
        pool, canonical_name="find references", scope_type="global",
        provenance="system_pending_review",
    ))
    assert result["id"] == "winner-id" and result["created"] is False


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


# (SimHash / cosine-threshold / raw-LLM-adjudication tiers were removed: see
# tests/test_goal_identity_e2e.py for the judge-decided identity tests.)


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


def test_search_goals_delegates_to_the_canonical_candidate_search(monkeypatch):
    """goals.search_goals has no ranking of its own: it is the judge-free face of Tier-1 candidate generation."""
    import app.services.retrieval_service as rs
    seen = {}

    async def fake(pool, **kw):
        seen.update(kw)
        return [{"id": "g1", "canonical_name": "find references"}]

    class _Pool:
        async def fetchval(self, *a):
            return "model-x"

    monkeypatch.setattr(rs, "search_goal_candidates", fake)
    out = _run(search_goals(_Pool(), query_text="find refs", query_embedding=[0.1] * 4, limit=5, status="active"))
    assert out == [{"id": "g1", "canonical_name": "find references"}]
    assert seen["query_text"] == "find refs" and seen["embedding_model"] == "model-x" and seen["status"] == "active" and seen["limit"] == 5


def test_search_goals_text_only_does_not_look_up_an_embedding_model(monkeypatch):
    import app.services.retrieval_service as rs
    seen = {}

    async def fake(pool, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(rs, "search_goal_candidates", fake)
    assert _run(search_goals(object(), query_text="find refs")) == []
    assert seen["embedding_model"] is None


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
