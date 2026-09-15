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

from app.services.goals import find_or_create_goal, normalize_goal_name
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

    async def fetchrow(self, sql, *params):
        s = " ".join(sql.split())
        if s.startswith("SELECT"):
            normalized = params[0]
            if "scope_type = $2" in s:
                scope_type, scope_entity_id = params[1], params[2]
                for r in self.rows:
                    if (r["normalized_name"] == normalized and r["scope_type"] == scope_type
                            and r["scope_entity_id"] == scope_entity_id and r["status"] != "merged"):
                        return r
            else:
                for r in self.rows:
                    if (r["normalized_name"] == normalized
                            and (r["scope_type"] is None or r["scope_type"] == "global")
                            and r["status"] != "merged"):
                        return r
            return None
        # INSERT ... RETURNING id, canonical_name
        (goal_id, canonical_name, normalized_name, description, expected_outcome,
         verification_requirement, status, provenance, created_from, owner_id,
         visibility, aliases, created_by, scope_type, scope_entity_id) = params
        row = {
            "id": goal_id, "canonical_name": canonical_name, "normalized_name": normalized_name,
            "status": status, "scope_type": scope_type, "scope_entity_id": scope_entity_id,
        }
        self.rows.append(row)
        return row


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


def test_find_or_create_goal_treats_a_lost_insert_race_as_a_dedup_hit():
    winner = {"id": "winner-id", "canonical_name": "find references"}
    pool = _RacingPool(winner)
    result = _run(find_or_create_goal(
        pool, canonical_name="find references", scope_type="global",
        provenance="system_pending_review",
    ))
    assert result == {"id": "winner-id", "canonical_name": "find references", "created": False}
