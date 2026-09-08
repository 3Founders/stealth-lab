"""
Abstention / no-match regression suite (release closure section 8).

A procedural-memory system that confidently retrieves the WRONG procedure
is dangerous. This suite drives 28 deliberately-hard queries through the
real search path.

Two tiers:

  STRICT (test_strict_nomatch_returns_zero) -- categories where there is
  genuinely no answer in the corpus: completely_unrelated, wrong_domain,
  superficial_wording_diff_intent. The relevance gate MUST return zero.

  DOCUMENTED GAP (test_incompatible_environment_gap_is_measured) --
  categories where a TOPICALLY relevant procedure exists but is wrong for
  the stated environment / direction / artifact type
  (same_tool_diff_objective, same_objective_incompatible_env,
  family_cousin_do_not_reuse). A cosine-similarity relevance gate cannot
  see a violated compatibility constraint; the applicability cascade is
  the layer that should, but the ingested skill corpus carries almost no
  structured preconditions. This test MEASURES and PINS the current leak
  rate rather than asserting zero -- see FINAL-REPORT.md release risk.

Skips without DATABASE_URL.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest
import pytest_asyncio

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="live abstention suite needs DATABASE_URL"
)

from app.db.session import create_pool  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.domain_search import search_global  # noqa: E402
from app.services.relevance_gate import RELEVANCE_GATE_MIN_SIMILARITY  # noqa: E402

_DATA = Path(__file__).parent / "data" / "retrieval_abstention_v1.jsonl"
_ROWS = [json.loads(l) for l in _DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
_STRICT = {"completely_unrelated", "wrong_domain", "superficial_wording_diff_intent"}
_GAP = {"same_tool_diff_objective", "same_objective_incompatible_env", "family_cousin_do_not_reuse"}
# Measured current leak rate on the GAP tier (local:mxbai-embed-large @ 0.6839).
# Pin it so a regression that makes abstention WORSE is caught; lowering
# this number is the improvement target.
_GAP_LEAK_CEILING = 8

_POOL = None


@pytest_asyncio.fixture(autouse=True)
async def _pool():
    global _POOL
    _POOL = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        yield
    finally:
        await _POOL.close()
        _POOL = None


async def _kept(query: str):
    res = await search_global(
        _POOL, query, object_types=["procedure"],
        scope=AccessScope.unrestricted(), limit=15,
    )
    return [h for h in res["results"]["procedure"]
            if (h.get("similarity_score") or 0) >= RELEVANCE_GATE_MIN_SIMILARITY]


@pytest.mark.asyncio
async def test_strict_nomatch_returns_zero():
    """Every genuinely-unanswerable query returns zero procedures."""
    leaks = []
    for r in _ROWS:
        if r["category"] not in _STRICT:
            continue
        kept = await _kept(r["query"])
        if kept:
            leaks.append((r["category"], r["query"],
                          [(h["name"], round(h.get("similarity_score") or 0, 3)) for h in kept]))
    assert not leaks, f"{len(leaks)} strict no-match queries leaked: {leaks}"


@pytest.mark.asyncio
async def test_incompatible_environment_gap_is_measured():
    """Pins the current leak rate on same-tool / incompatible-env /
    family-cousin queries. The gate is semantic-only and cannot see a
    violated environment/direction/artifact constraint; this is a KNOWN,
    documented limitation (FINAL-REPORT.md). The assertion only guards
    against REGRESSION -- the leak count must not grow."""
    leaks = []
    per_cat = Counter()
    for r in _ROWS:
        if r["category"] not in _GAP:
            continue
        per_cat[r["category"]] += 1
        kept = await _kept(r["query"])
        if kept:
            leaks.append({"category": r["category"], "query": r["query"],
                          "surfaced": [(h["name"], round(h.get("similarity_score") or 0, 3))
                                       for h in kept[:3]]})
    print(f"\nincompatible-environment gap: {len(leaks)}/{sum(per_cat.values())} queries "
          f"surfaced a topically-relevant but constraint-violating procedure")
    for l in leaks:
        print(f"  [{l['category']}] {l['query']!r} -> {l['surfaced']}")
    assert len(leaks) <= _GAP_LEAK_CEILING, (
        f"abstention REGRESSED: {len(leaks)} gap-tier leaks > ceiling {_GAP_LEAK_CEILING}"
    )
