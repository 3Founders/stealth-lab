"""
Offline tests for app/services/skill_ingestion.py -- the SKILL.md
document-shaped ingestion path (architecture audit Phase 2).

Fixture: a realistic pandas DataFrame.append()-removal skill, continuing
the same real scenario used throughout this session's other proving
tests (test_plan_persistence_offline.py, the 6-tool MCP live test) --
not a synthetic "skill A/skill B" placeholder.
"""
from __future__ import annotations

import pytest

from app.services.skill_ingestion import (
    ParsedSkill,
    SkillMdParseError,
    check_novelty,
    ingest_skill_md,
    parse_skill_md,
)

PANDAS_APPEND_SKILL_MD = """---
name: fix-pandas-append-removal
description: Fix AttributeError from pandas DataFrame.append() removal in pandas >= 2.0
---

Use when: an AttributeError says 'DataFrame' object has no attribute 'append'.

1. Locate every call site using `df.append(...)`.
2. Replace each with `pd.concat([df, other], ignore_index=True)`.
3. Run the test suite to confirm the migration is complete.
"""

NO_FRONTMATTER_SKILL_MD = """# Diagnose a flaky test

Use when the user reports a test that passes sometimes and fails other times.

- Run the test repeatedly in isolation to confirm it is genuinely flaky.
- Check for shared mutable state or unseeded randomness.
- Form a specific hypothesis before changing anything.
"""

NO_STEPS_SKILL_MD = """---
name: research-with-citations
description: Investigate a question against high-trust primary sources and cite them.
---

This skill has no numbered procedure, just a capability description.
"""


def test_parses_frontmatter_name_and_description():
    parsed = parse_skill_md(PANDAS_APPEND_SKILL_MD)
    assert parsed.name == "fix-pandas-append-removal"
    assert "AttributeError" in parsed.description


def test_parses_numbered_steps_in_order():
    parsed = parse_skill_md(PANDAS_APPEND_SKILL_MD)
    assert parsed.steps == [
        "Locate every call site using `df.append(...)`.",
        "Replace each with `pd.concat([df, other], ignore_index=True)`.",
        "Run the test suite to confirm the migration is complete.",
    ]


def test_applies_when_kept_as_prose_not_a_predicate():
    """THE key design decision this pass made: no Tier-1 template match
    attempted here at all -- applies_when is a raw string, never coerced
    into {subject, predicate, object}."""
    parsed = parse_skill_md(PANDAS_APPEND_SKILL_MD)
    assert isinstance(parsed.applies_when, str)
    assert "AttributeError" in parsed.applies_when


def test_missing_frontmatter_falls_back_to_heading_and_bullets():
    parsed = parse_skill_md(NO_FRONTMATTER_SKILL_MD, fallback_name="diagnose-flaky-test")
    assert parsed.name == "diagnose-flaky-test"  # no frontmatter name -- caller's fallback used
    assert len(parsed.steps) == 3
    assert "passes sometimes and fails" in parsed.applies_when.lower()


def test_no_steps_lands_one_honest_step_from_description():
    parsed = parse_skill_md(NO_STEPS_SKILL_MD)
    assert len(parsed.steps) == 1
    assert parsed.steps[0] == parsed.description


def test_empty_document_refuses_rather_than_fabricating():
    with pytest.raises(SkillMdParseError):
        parse_skill_md("")


class FakeEmbedder:
    async def embed_one(self, text, input_type="query"):
        return [0.1] * 1024


class FakePool:
    def __init__(self, existing_matches=None, captured=None):
        self._existing_matches = existing_matches or []
        self.captured = captured if captured is not None else []

    async def fetch(self, sql, *params):
        if "FROM procedures" in sql:
            return self._existing_matches
        return []

    async def fetchrow(self, sql, *params):
        if "INSERT INTO procedures" in sql:
            self.captured.append(params)
            return {"id": "new-row-id", "procedure_id": "new-procedure-id"}
        return None


@pytest.mark.asyncio
async def test_check_novelty_returns_none_when_nothing_similar_exists(monkeypatch):
    async def fake_find(pool, *, goal_embedding, require_verified, limit):
        return []

    monkeypatch.setattr("app.services.skill_ingestion.find_applicable_procedures", fake_find)
    result = await check_novelty(FakePool(), FakeEmbedder(), "fix a pandas bug")
    assert result is None


@pytest.mark.asyncio
async def test_check_novelty_returns_the_match_above_threshold(monkeypatch):
    async def fake_find(pool, *, goal_embedding, require_verified, limit):
        return [{"procedure_id": "existing-id", "_similarity_score": 0.95}]

    monkeypatch.setattr("app.services.skill_ingestion.find_applicable_procedures", fake_find)
    result = await check_novelty(FakePool(), FakeEmbedder(), "fix a pandas bug")
    assert result is not None
    assert result["procedure_id"] == "existing-id"


@pytest.mark.asyncio
async def test_check_novelty_below_threshold_is_still_novel(monkeypatch):
    async def fake_find(pool, *, goal_embedding, require_verified, limit):
        return [{"procedure_id": "existing-id", "_similarity_score": 0.5}]

    monkeypatch.setattr("app.services.skill_ingestion.find_applicable_procedures", fake_find)
    result = await check_novelty(FakePool(), FakeEmbedder(), "fix a pandas bug")
    assert result is None


@pytest.mark.asyncio
async def test_ingest_reports_duplicate_and_does_not_write(monkeypatch):
    async def fake_find(pool, *, goal_embedding, require_verified, limit):
        return [{"procedure_id": "existing-id", "_similarity_score": 0.97}]

    monkeypatch.setattr("app.services.skill_ingestion.find_applicable_procedures", fake_find)
    pool = FakePool()
    result = await ingest_skill_md(pool, PANDAS_APPEND_SKILL_MD, embedder=FakeEmbedder())
    assert result["status"] == "duplicate"
    assert result["existing_procedure_id"] == "existing-id"
    assert pool.captured == [], "must not write when a near-duplicate already exists"


@pytest.mark.asyncio
async def test_ingest_writes_with_a_real_embedding_when_novel(monkeypatch):
    """Regression pin for the exact bug this session's own production
    test found: a captured procedure with no embedding is later
    unreachable by search. embedding must be non-null in the write."""
    async def fake_find(pool, *, goal_embedding, require_verified, limit):
        return []

    monkeypatch.setattr("app.services.skill_ingestion.find_applicable_procedures", fake_find)
    pool = FakePool()
    result = await ingest_skill_md(pool, PANDAS_APPEND_SKILL_MD, embedder=FakeEmbedder())
    assert result["status"] == "captured"
    assert result["procedure_id"] == "new-procedure-id"
    assert len(pool.captured) == 1
